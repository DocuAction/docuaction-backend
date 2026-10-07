"""SAM.gov screening vocabulary, sanitised failures, provenance, identity grouping and the OFFLINE daily-extract prototype.

Synthetic data only. The mini extract uses the real 31-column layout of SAM_Exclusions_Public_Extract_V2 with invented
names, identifiers and agencies.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from pathlib import Path

import pytest

from app.Tefca import sam_screening as ss
from app.Tefca.sam_exclusions_extract import (ExtractLayoutError, extract_date_from_name, load_extract)

HEADER = ["Classification", "Name", "Prefix", "First", "Middle", "Last", "Suffix", "Address 1", "Address 2",
          "Address 3", "Address 4", "City", "State / Province", "Country", "Zip Code", "Open Data Flag",
          "Blank (Deprecated)", "Unique Entity ID", "Exclusion Program", "Excluding Agency", "CT Code",
          "Exclusion Type", "Additional Comments", "Active Date", "Termination Date", "Record Status",
          "Cross-Reference", "SAM Number", "CAGE", "NPI", "Creation_Date"]


def _row(**kw):
    base = dict.fromkeys(HEADER, "")
    base.update({"Record Status": "Active", "Termination Date": "Indefinite", "Country": "USA",
                 "Exclusion Program": "Reciprocal", "Excluding Agency": "HHS",
                 "Exclusion Type": "Prohibition/Restriction", "Active Date": "01/15/2024"})
    base.update(kw)
    return [base[h] for h in HEADER]


ROWS = [
    # one organisation excluded by THREE agencies: one identity, three actions
    _row(Classification="Firm", Name="Synth Alpha Clinic LLC", **{"Unique Entity ID": "UEIALPHA0001"},
         **{"State / Province": "NY", "Zip Code": "12207"}, **{"Excluding Agency": "HHS"}),
    _row(Classification="Firm", Name="Synth Alpha Clinic LLC", **{"Unique Entity ID": "UEIALPHA0001"},
         **{"State / Province": "NY", "Zip Code": "12207"}, **{"Excluding Agency": "DOD"}),
    _row(Classification="Firm", Name="Synth Alpha Clinic", **{"Unique Entity ID": "UEIALPHA0001"},
         **{"State / Province": "NY"}, **{"Excluding Agency": "GSA"}),
    # two DIFFERENT organisations with the same name (distinct UEIs)
    _row(Classification="Firm", Name="Synth Family Care", **{"Unique Entity ID": "UEIFAM000001"},
         **{"State / Province": "TX"}),
    _row(Classification="Firm", Name="Synth Family Care", **{"Unique Entity ID": "UEIFAM000002"},
         **{"State / Province": "OH"}),
    # firm with NPI only
    _row(Classification="Firm", Name="Synth Imaging Partners", NPI="1888888884", **{"State / Province": "NJ"}),
    # an NPI whose holder's name is something else entirely
    _row(Classification="Firm", Name="Totally Different Name Inc", NPI="1999999992", **{"State / Province": "FL"}),
    # name only, no identifiers, one record
    _row(Classification="Firm", Name="Synth Bare Name Services", **{"State / Province": "CA"}),
    # individual
    _row(Classification="Individual", First="Pat", Last="Synthperson", NPI="1777777775",
         **{"State / Province": "WA"}),
    _row(Classification="Special Entity Designation", Name="Synth Special Group"),
]


def _extract(tmp_path: Path, rows=ROWS, header=HEADER, name="SAM_Exclusions_Public_Extract_V2_26279.ZIP") -> Path:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    p = tmp_path / name
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("SAM_Exclusions_Public_Extract_V2_26279.CSV", buf.getvalue())
    return p


@pytest.fixture
def index(tmp_path):
    return load_extract(_extract(tmp_path))


class TestSanitisedFailures:
    def test_credentials_in_a_url_are_redacted(self):
        text = "failed calling https://api.sam.gov/entity-information/v4/exclusions?api_key=SUPERSECRET123&q=x"
        out = ss.sanitize_reason(text)
        assert "SUPERSECRET123" not in out and "REDACTED" in out

    def test_long_bodies_are_truncated_and_control_characters_removed(self):
        out = ss.sanitize_reason("a\x00b\n\n  c" + "x" * 1000, limit=50)
        assert len(out) <= 50 and "\x00" not in out and "\n" not in out

    @pytest.mark.parametrize("text,expected", [
        ("SAM_GOV unavailable: SAM_GOV_API_KEY not set (register a free key at sam.gov)", ss.NO_KEY),
        ("SAM_GOV unavailable: HTTP 404 with an empty body — api.sam.gov served no route. Upstream routing at SAM", ss.NOT_ROUTING),
        ("SAM_GOV unavailable: HTTP 403 — SAM.gov rejected the API key: x", ss.AUTH_REJECTED),
        ("SAM_GOV unavailable: HTTP 429 — SAM.gov rate limit reached (daily quota is per key)", ss.QUOTA),
        ("SAM_GOV unavailable: malformed_response: SAM.gov response was not a JSON object", ss.STRUCTURAL),
        ("SAM_GOV unavailable: source_error_body: SAM.gov answered HTTP 200 with an error body", ss.STRUCTURAL),
        ("SAM_GOV unavailable: no UEI or legal name to query", ss.NO_IDENTIFIER),
        ("SAM_GOV unavailable: ReadTimeout", ss.TRANSPORT),
        ("SAM_GOV unavailable: HTTP 502", ss.UPSTREAM_ERROR),
        ("", ss.NOT_QUERIED),
    ])
    def test_failure_classes(self, text, expected):
        assert ss.classify_failure(text) == expected

    def test_failure_record_is_sanitised(self):
        rec = ss.failure_record("HTTP 401 https://api.sam.gov/x?api_key=ABCDEF12345")
        assert rec["failure_class"] == ss.AUTH_REJECTED and "ABCDEF12345" not in rec["failure_reason"]


class TestOutcomes:
    @pytest.mark.parametrize("kw,expected", [
        (dict(answered=False), (ss.INCOMPLETE, "CHECK_DID_NOT_COMPLETE")),
        (dict(answered=True, record_count=0), (ss.NO_HIT, "NO_RECORD_FOR_QUERY")),
        (dict(answered=True, record_count=3, distinct_identities=1, matched_by="uei"), (ss.IDENTIFIER_MATCH, "SINGLE_IDENTITY_BY_UEI")),
        (dict(answered=True, record_count=1, distinct_identities=1, matched_by="npi"), (ss.IDENTIFIER_MATCH, "SINGLE_IDENTITY_BY_NPI")),
        (dict(answered=True, record_count=1, distinct_identities=1, matched_by="name"), (ss.POTENTIAL_MATCH, "NAME_ONLY_MATCH")),
        (dict(answered=True, record_count=2, distinct_identities=2, matched_by="name"), (ss.POTENTIAL_MATCH, "MULTIPLE_DISTINCT_IDENTITIES")),
        (dict(answered=True, record_count=2, distinct_identities=2, matched_by="uei"), (ss.POTENTIAL_MATCH, "MULTIPLE_DISTINCT_IDENTITIES")),
    ])
    def test_classification(self, kw, expected):
        assert ss.classify_outcome(**kw) == expected

    def test_a_name_alone_is_never_an_identifier_match(self):
        for n in (1, 2, 50):
            assert ss.classify_outcome(answered=True, record_count=n, distinct_identities=1, matched_by="name")[0] != ss.IDENTIFIER_MATCH

    @pytest.mark.parametrize("outcome,by_name,insufficient,expected", [
        (ss.INCOMPLETE, True, False, "UNAVAILABLE"), (ss.INCOMPLETE, True, True, "INSUFFICIENT_EVIDENCE"),
        (ss.POTENTIAL_MATCH, True, False, "REVIEW"), (ss.IDENTIFIER_MATCH, False, False, "REVIEW"),
        (ss.NO_HIT, True, False, "NOT_FOUND"), (ss.NO_HIT, False, False, "PASS"),
    ])
    def test_disposition_mapping_is_the_historical_one(self, outcome, by_name, insufficient, expected):
        assert ss.disposition_for(outcome, by_name=by_name, insufficient=insufficient) == expected


class TestIdentityGrouping:
    def _recs(self, rows):
        return [dict(classification=r[0], name=r[1], uei=r[2], npi=r[3], cage=r[4], state=r[5], zip=r[6],
                     agency=r[7]) for r in rows]

    def test_several_actions_on_one_uei_are_one_identity_with_every_action_kept(self):
        recs = self._recs([("Firm", "A Co", "U1", "", "", "NY", "1", "HHS"), ("Firm", "A Co", "U1", "", "", "NY", "1", "DOD"),
                           ("Firm", "A Co.", "U1", "", "", "NY", "1", "GSA")])
        ids = ss.group_identities(recs, normalize_name=lambda s: s.lower())
        assert len(ids) == 1 and ids[0].action_count == 3
        assert {a.agency for a in ids[0].actions} == {"HHS", "DOD", "GSA"}

    def test_every_input_record_becomes_exactly_one_action(self):
        recs = self._recs([("Firm", "A", "U1", "", "", "", "", "x"), ("Firm", "B", "U2", "", "", "", "", "y"),
                           ("Firm", "C", "", "123", "", "", "", "z"), ("Firm", "C", "", "", "", "", "", "w")])
        ids = ss.group_identities(recs, normalize_name=lambda s: s.lower())
        assert sum(i.action_count for i in ids) == len(recs)

    def test_different_ueis_with_the_same_name_stay_separate(self):
        recs = self._recs([("Firm", "Same Name", "U1", "", "", "TX", "", "a"), ("Firm", "Same Name", "U2", "", "", "OH", "", "b")])
        assert len(ss.group_identities(recs, normalize_name=lambda s: s.lower())) == 2

    def test_a_chain_of_shared_identifiers_merges(self):
        recs = self._recs([("Firm", "A", "U1", "N1", "", "", "", "a"), ("Firm", "A2", "", "N1", "C1", "", "", "b"),
                           ("Firm", "A3", "", "", "C1", "", "", "c")])
        assert len(ss.group_identities(recs, normalize_name=lambda s: s.lower())) == 1

    def test_records_without_identifiers_group_by_name_and_location_only(self):
        recs = self._recs([("Firm", "B", "", "", "", "CA", "90001", "a"), ("Firm", "B", "", "", "", "CA", "90001", "b"),
                           ("Firm", "B", "", "", "", "NV", "89101", "c")])
        ids = ss.group_identities(recs, normalize_name=lambda s: s.lower())
        assert sorted(i.action_count for i in ids) == [1, 2] and all(i.key_basis == "name_location" for i in ids)


class TestExtractLoader:
    def test_loads_and_anchors(self, tmp_path):
        idx = load_extract(_extract(tmp_path))
        a = idx.meta.anchor()
        assert a["rows"] == len(ROWS) and a["extract_date"] == "2026-10-06" and len(a["sha256"]) == 64

    @pytest.mark.parametrize("name,expected", [("SAM_Exclusions_Public_Extract_V2_26279.ZIP", "2026-10-06"),
                                               ("SAM_Exclusions_Public_Extract_V2_26001.ZIP", "2026-01-01"),
                                               ("nope.zip", None)])
    def test_date_from_name(self, name, expected):
        d = extract_date_from_name(name)
        assert (d.isoformat() if d else None) == expected

    def test_a_missing_required_column_fails_loudly(self, tmp_path):
        bad = [h for h in HEADER if h != "NPI"]
        rows = [[v for h, v in zip(HEADER, r) if h != "NPI"] for r in ROWS]
        with pytest.raises(ExtractLayoutError, match="required columns missing"):
            load_extract(_extract(tmp_path, rows=rows, header=bad))

    def test_an_empty_or_truncated_file_never_reads_as_no_hits(self, tmp_path):
        with pytest.raises(ExtractLayoutError):
            load_extract(_extract(tmp_path, rows=[]))
        with pytest.raises(ExtractLayoutError, match="fields"):
            load_extract(_extract(tmp_path, rows=ROWS + [["Firm", "short row"]]))

    def test_not_a_zip_is_refused(self, tmp_path):
        p = tmp_path / "x.ZIP"
        p.write_bytes(b"not a zip")
        with pytest.raises(ExtractLayoutError):
            load_extract(p)


class TestIdentifierFirstScreening:
    def test_uei_hit_on_one_identity_is_confirmed_and_keeps_all_three_actions(self, index):
        r = index.screen(uei="UEIALPHA0001", name="Synth Alpha Clinic")
        assert (r.outcome, r.matched_by) == (ss.IDENTIFIER_MATCH, "uei")
        assert r.distinct_identities == 1 and r.action_count == 3
        data = r.to_check_exclusions_data()
        assert data["ambiguous"] is False and data["match_count"] == 3

    def test_npi_hit_is_confirmed(self, index):
        r = index.screen(npi="1888888884", name="Synth Imaging Partners")
        assert (r.outcome, r.matched_by) == (ss.IDENTIFIER_MATCH, "npi")

    def test_an_identifier_whose_holder_has_a_different_name_is_only_potential(self, index):
        r = index.screen(npi="1999999992", name="Synth Unrelated Hospital")
        assert (r.outcome, r.reason) == (ss.POTENTIAL_MATCH, "IDENTIFIER_NAME_DISAGREE")

    def test_name_only_hit_is_potential_never_confirmed(self, index):
        r = index.screen(name="Synth Bare Name Services")
        assert (r.outcome, r.matched_by, r.reason) == (ss.POTENTIAL_MATCH, "name", "NAME_ONLY_MATCH")

    def test_one_name_two_distinct_identities_is_ambiguous(self, index):
        r = index.screen(name="Synth Family Care")
        assert r.outcome == ss.POTENTIAL_MATCH and r.distinct_identities == 2
        assert r.to_check_exclusions_data()["ambiguous"] is True

    def test_one_name_three_actions_one_identity_is_not_ambiguous(self, index):
        r = index.screen(name="Synth Alpha Clinic")
        assert r.outcome == ss.POTENTIAL_MATCH and r.distinct_identities == 1 and r.action_count == 3
        assert r.to_check_exclusions_data()["ambiguous"] is False

    def test_absence_by_identifier_is_weak_so_the_name_stage_still_runs(self, index):
        r = index.screen(uei="UEINOTINEXTRACT", npi="1234567893", name="Synth Alpha Clinic")
        assert r.outcome == ss.POTENTIAL_MATCH and r.matched_by == "name"
        assert r.provenance["identifiers_tried"] == ["uei", "npi", "name"]

    def test_no_record_is_a_no_hit(self, index):
        r = index.screen(name="Synth Perfectly Clean Hospital")
        assert (r.outcome, r.reason) == (ss.NO_HIT, "NO_RECORD_FOR_QUERY") and r.action_count == 0

    def test_nothing_to_search_on_is_incomplete_not_clear(self, index):
        r = index.screen()
        assert r.outcome == ss.INCOMPLETE and r.provenance["failure_class"] == ss.NO_IDENTIFIER

    def test_a_malformed_npi_is_ignored_not_searched(self, index):
        r = index.screen(npi="12345", name="Synth Perfectly Clean Hospital")
        assert r.outcome == ss.NO_HIT and "npi" not in r.provenance["identifiers_tried"]

    def test_individuals_are_screened_by_person_name_or_npi(self, index):
        assert index.screen(npi="1777777775", first="Pat", last="Synthperson", kind="individual").outcome == ss.IDENTIFIER_MATCH
        assert index.screen(first="Pat", last="Synthperson", kind="individual").outcome == ss.POTENTIAL_MATCH

    def test_provenance_never_contains_the_search_value_or_a_credential(self, index):
        r = index.screen(uei="UEIALPHA0001", npi="1888888884", name="Synth Alpha Clinic")
        blob = str(r.provenance)
        for secret in ("UEIALPHA0001", "1888888884", "Synth Alpha Clinic"):
            assert secret not in blob
        assert r.provenance["dataset_anchor"]["file"].endswith(".ZIP")
        assert r.provenance["channel"] == "DAILY_EXTRACT" and r.provenance["leg"] == "exclusion"


class TestPrototypeIsNotWired:
    def test_nothing_in_the_application_imports_the_extract_prototype(self):
        root = Path(__file__).resolve().parents[1] / "app"
        offenders = [str(p) for p in root.rglob("*.py")
                     if p.name != "sam_exclusions_extract.py" and re.search(
                         r"^\s*(from|import)\s+[\w.]*sam_exclusions_extract|^\s*from\s+app\.Tefca\s+import\s+.*sam_exclusions_extract",
                         p.read_text(encoding="utf-8", errors="ignore"), re.M)]
        assert offenders == [], f"the offline prototype must not be imported by application code: {offenders}"


class TestConnectorIsAdditive:
    def _conn(self, monkeypatch, behaviour):
        from app.Tefca import connectors as c
        monkeypatch.setenv("SAM_GOV_API_KEY", "k" * 40)

        async def fake(url, **kw):
            return behaviour(kw)
        monkeypatch.setattr(c, "_get_with_retry", fake)
        return c.SAMGovConnector()

    def test_exception_text_carrying_the_key_is_redacted_in_the_stored_error(self, monkeypatch):
        import asyncio

        def boom(kw):
            raise RuntimeError("connect failed https://api.sam.gov/x?api_key=" + kw["params"]["api_key"])
        r = asyncio.run(self._conn(monkeypatch, boom).check_exclusions(uei="UEIX00000001"))
        assert not r.success and "k" * 40 not in (r.error or "")

    def test_exclusion_outcome_and_provenance_added_without_changing_existing_keys(self, monkeypatch):
        import asyncio

        class Resp:
            status_code = 200
            text = "{}"

            def json(self):
                return {"totalRecords": 2, "excludedEntity": [{}, {}]}
        r = asyncio.run(self._conn(monkeypatch, lambda kw: Resp()).check_exclusions(legal_name="Some Name"))
        d = r.data
        assert d["excluded"] is True and d["ambiguous"] is True and d["matched_by"] == "name" and d["match_count"] == 2
        assert d["outcome"] == ss.POTENTIAL_MATCH and d["provenance"]["channel"] == "LIVE_API"
        assert "Some Name" not in str(d["provenance"]) and "k" * 40 not in str(d["provenance"])


class TestLabelsAndScope:
    def test_no_automated_path_emits_confirmed_match(self, index):
        outs = {index.screen(**kw).outcome for kw in (
            dict(uei="UEIALPHA0001"), dict(npi="1888888884", name="Synth Imaging Partners"),
            dict(name="Synth Family Care"), dict(name="Nobody Here"), dict()) }
        assert ss.CONFIRMED_MATCH not in outs and ss.IDENTIFIER_MATCH in outs

    def test_label_says_pending_adjudication(self):
        assert "pending adjudication" in ss.OUTCOME_LABELS[ss.IDENTIFIER_MATCH].lower()

    def test_no_hit_is_qualified_by_extract_date_and_scope(self, index):
        r = index.screen(name="Synth Perfectly Clean Hospital")
        sc = r.provenance["matching_scope"]
        assert sc["as_of_extract_date"] == "2026-10-06" and "no fuzzy" in sc["name_matching"]
        assert "not LEIE" in sc["list"] and sc["identifiers_tried"] == ["name"]


class TestIdentityKeysAndIndividuals:
    def _g(self, recs):
        return ss.group_identities(recs, normalize_name=lambda s: s.lower())

    def test_identity_keys_are_unique_within_a_call_and_stable_across_calls(self):
        a = dict(classification="Firm", name="A", uei="U1", state="NY")
        b = dict(classification="Firm", name="B", uei="U2", state="NY")
        c = dict(classification="Firm", name="C", state="CA", zip="90001")
        k1 = {i.identity_key for i in self._g([a, b, c])}
        k2 = {i.identity_key for i in self._g([c, b, a])}
        k3 = {i.identity_key for i in self._g([b])}
        assert len(k1) == 3 and k1 == k2 and next(iter(k3)) in k1

    def test_individuals_with_no_name_field_group_by_person_and_location(self):
        p = dict(classification="Individual", first="Pat", last="Doe", state="WA", zip="98001")
        recs = [dict(p, agency="HHS"), dict(p, agency="DOD"), dict(p, last="Roe", agency="HHS")]
        ids = self._g(recs)
        assert sorted(i.action_count for i in ids) == [1, 2] and sum(i.action_count for i in ids) == 3

    def test_nameless_unidentified_records_are_kept_not_dropped(self):
        ids = self._g([dict(classification="Firm"), dict(classification="Firm")])
        assert sum(i.action_count for i in ids) == 2
