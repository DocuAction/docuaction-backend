"""Optional USPS Address Validation connector.

Entirely optional. Without USPS_API_USER_ID this module reports itself as not
configured and every caller falls back to code-only normalization
(address_normalizer.USPSNormalizer), which needs no key, no network, and no quota.

The free USPS tier allows ~500 lookups/day — ample for the 383-entity sample, but
not for bulk registry work, so results are cached for the process lifetime and the
daily budget is capped below the tier limit. That guard exists because of the
Perigon incident: an unbudgeted free-tier API called from a retrying pipeline
exhausted its quota by 06:00 and the failure was invisible until someone looked.

Never raises. Every failure path returns a result whose `verified` is False with a
reason, so an address check can never break a verification run.
"""

import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger("docuaction.tefca.usps")

USPS_API_USER_ID = os.getenv("USPS_API_USER_ID", "").strip()
USPS_ENABLED = bool(USPS_API_USER_ID)
USPS_BASE = "https://production.shippingapis.com/ShippingAPI.dll"
TIMEOUT = float(os.getenv("USPS_TIMEOUT", "15"))
DAILY_BUDGET = int(os.getenv("USPS_DAILY_BUDGET", "400"))  # under the ~500 tier
CACHE_TTL_S = int(os.getenv("USPS_CACHE_TTL_S", str(24 * 3600)))

_cache: Dict[str, Any] = {}
_budget: Dict[str, Any] = {"date": None, "calls": 0}


@dataclass
class USPSResult:
    verified: bool
    standardized: str = ""
    zip5: str = ""
    zip4: str = ""
    source: str = "usps"          # usps | cache | fallback
    reason: str = ""


def _budget_roll() -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _budget["date"] != today:
        _budget.update({"date": today, "calls": 0})


def _budget_allows() -> bool:
    _budget_roll()
    return _budget["calls"] < DAILY_BUDGET


def budget_status() -> Dict[str, Any]:
    _budget_roll()
    return {
        "enabled": USPS_ENABLED,
        "date": _budget["date"],
        "calls_today": _budget["calls"],
        "daily_budget": DAILY_BUDGET,
        "remaining": max(0, DAILY_BUDGET - _budget["calls"]),
        "cache_entries": len(_cache),
    }


def _reset_for_tests() -> None:
    _cache.clear()
    _budget.update({"date": None, "calls": 0})


class USPSConnector:
    """Address verification via the USPS API, with code-only fallback."""

    def __init__(self, user_id: Optional[str] = None, normalizer=None):
        self.user_id = (user_id if user_id is not None else USPS_API_USER_ID).strip()
        if normalizer is None:
            from app.tefca_registry.address_normalizer import USPSNormalizer
            normalizer = USPSNormalizer()
        self.normalizer = normalizer

    @property
    def enabled(self) -> bool:
        return bool(self.user_id)

    def _fallback(self, address: str, reason: str) -> USPSResult:
        """Code-only normalization — always available, never fails."""
        return USPSResult(
            verified=False,
            standardized=self.normalizer.normalize(address),
            zip5=self.normalizer.extract_zip(address),
            source="fallback",
            reason=reason,
        )

    def verify(self, address: str, *, city: str = "", state: str = "",
               zip_code: str = "", _opener=None) -> USPSResult:
        """Verify one address. Falls back to normalization on any problem."""
        if not (address or "").strip():
            return USPSResult(False, "", "", source="fallback", reason="empty address")
        if not self.enabled:
            return self._fallback(address, "USPS_API_USER_ID not configured")

        key = "|".join([address.strip().lower(), city.strip().lower(),
                        state.strip().lower(), zip_code.strip()])
        hit = _cache.get(key)
        if hit and time.time() < hit[0]:
            cached: USPSResult = hit[1]
            return USPSResult(cached.verified, cached.standardized, cached.zip5,
                              cached.zip4, source="cache", reason=cached.reason)

        if not _budget_allows():
            logger.warning("USPS: daily budget of %d spent — using code normalization",
                           DAILY_BUDGET)
            return self._fallback(address, "daily budget exhausted")

        xml = (
            f'<AddressValidateRequest USERID="{self.user_id}">'
            f"<Revision>1</Revision><Address ID='0'>"
            f"<Address1></Address1><Address2>{_esc(address)}</Address2>"
            f"<City>{_esc(city)}</City><State>{_esc(state)}</State>"
            f"<Zip5>{_esc(zip_code)}</Zip5><Zip4></Zip4>"
            f"</Address></AddressValidateRequest>"
        )
        url = f"{USPS_BASE}?{urllib.parse.urlencode({'API': 'Verify', 'XML': xml})}"

        _budget["calls"] += 1
        try:
            opener = _opener or urllib.request.urlopen
            with opener(url, timeout=TIMEOUT) as resp:
                body = resp.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError) as e:
            return self._fallback(address, f"request failed: {type(e).__name__}")
        except Exception as e:  # noqa: BLE001 — never break a verification run
            return self._fallback(address, f"unexpected error: {type(e).__name__}")

        result = self._parse(body, address)
        _cache[key] = (time.time() + CACHE_TTL_S, result)
        return result

    def _parse(self, body: str, original: str) -> USPSResult:
        try:
            try:
                from defusedxml import ElementTree as ET
            except Exception:  # pragma: no cover
                import xml.etree.ElementTree as ET
            root = ET.fromstring(body)
        except Exception:
            return self._fallback(original, "unparseable USPS response")

        err = root.find(".//Error")
        if err is not None:
            desc = err.findtext("Description") or "USPS returned an error"
            return self._fallback(original, desc.strip()[:200])

        addr = root.find(".//Address")
        if addr is None:
            return self._fallback(original, "no Address element in USPS response")

        line2 = (addr.findtext("Address2") or "").strip()
        city = (addr.findtext("City") or "").strip()
        state = (addr.findtext("State") or "").strip()
        zip5 = (addr.findtext("Zip5") or "").strip()
        zip4 = (addr.findtext("Zip4") or "").strip()
        standardized = " ".join(p for p in (line2, city, state, zip5) if p).upper()
        return USPSResult(bool(zip5), standardized, zip5, zip4, source="usps")


def _esc(value: str) -> str:
    return (str(value or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))
