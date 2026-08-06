"""Outlook-safe email HTML for the FCC bulletin.

Outlook Classic renders mail with the MSWord HTML engine, not a browser engine.
That engine predates almost everything a modern email would use, so this module
is written to a deliberately narrow subset:

  USED                              NOT USED
  table/tr/td layout                flexbox, grid, float-based layout
  inline styles on every element    <style> blocks, external CSS, CSS variables
  explicit pixel widths             percentage widths, max-width
  bgcolor attribute on <td>         CSS background shorthand
  longhand CSS (border-top: ...)    shorthand (border: ...)
  MSO conditional comments          media queries
  &nbsp; and spacer rows            margin on <p>

The MSO conditional wrapper is what pins the layout to 600px in Outlook, which
ignores max-width. Everything outside the conditional is what other clients see.

Everything here is deliberately plain string building rather than a template
engine: an autoescaping engine would fight the inline styles, and a
non-autoescaping one would be an injection risk on content scraped from feeds.
Values are escaped explicitly at the point of use instead.
"""

from __future__ import annotations

from html import escape
from typing import Any, Dict, Iterable, List, Optional

NAVY = "#003087"
LIGHT = "#f0f4f8"
RULE = "#e0e0e0"
BODY_TEXT = "#333333"
MUTED = "#888888"
PAGE_BG = "#f4f4f4"
WIDTH = 600

FONT = "Arial,Helvetica,sans-serif"


def _esc(value: Any) -> str:
    return escape(str(value or ""), quote=True)


def _field(article: Any, name: str, default: str = "") -> Any:
    if isinstance(article, dict):
        return article.get(name, default)
    return getattr(article, name, default)


def _text_of(article: Any, name: str) -> str:
    """Plain text for a cell — HTML from feeds must never reach the email body."""
    try:
        from app.bulletin_intelligence.excel_export import strip_html
        return strip_html(_field(article, name, ""))
    except Exception:
        return str(_field(article, name, "") or "")


def _relevance(article: Any) -> str:
    try:
        score = float(_field(article, "relevance_score", 0) or 0)
    except (TypeError, ValueError):
        return "Low"
    if score >= 0.75:
        return "High"
    return "Medium" if score >= 0.45 else "Low"


def _title_with_tags(article: Any) -> str:
    title = _text_of(article, "title")
    kind = str(_field(article, "article_type", "") or "").lower()
    if kind in ("opinion", "editorial") and "[Opinion]" not in title:
        title = f"{title} [Opinion]"
    if bool(_field(article, "is_paywalled", False)) and "[Subscription Required]" not in title:
        title = f"{title} [Subscription Required]"
    return title


def _article_row(article: Any) -> str:
    url = _text_of(article, "url")
    title = _esc(_title_with_tags(article))
    summary = _esc(_text_of(article, "summary"))
    source = _esc(_text_of(article, "outlet") or _text_of(article, "source"))
    relevance = _esc(_relevance(article))

    # A bare title when there is no usable link — an <a> with an empty href
    # renders as a dead link in Outlook rather than as plain text.
    if url.startswith(("http://", "https://")):
        headline = (
            f'<a href="{_esc(url)}" style="color:{NAVY};font-family:{FONT};'
            f'font-size:14px;font-weight:bold;text-decoration:none;">{title}</a>'
        )
    else:
        headline = (
            f'<span style="color:{NAVY};font-family:{FONT};font-size:14px;'
            f'font-weight:bold;">{title}</span>'
        )

    return (
        f'<tr><td style="padding-top:15px;padding-right:20px;padding-bottom:15px;'
        f'padding-left:20px;border-bottom-width:1px;border-bottom-style:solid;'
        f'border-bottom-color:{RULE};">'
        f'{headline}<br/>'
        f'<span style="color:{BODY_TEXT};font-family:{FONT};font-size:13px;'
        f'line-height:1.5;">{summary}</span><br/>'
        f'<span style="color:{MUTED};font-family:{FONT};font-size:11px;">'
        f'{source} | {relevance} Relevance</span>'
        f'</td></tr>'
    )


def _category_row(name: str, count: int) -> str:
    label = _esc(name)
    plural = "article" if count == 1 else "articles"
    return (
        f'<tr><td bgcolor="{LIGHT}" style="padding-top:12px;padding-right:20px;'
        f'padding-bottom:12px;padding-left:20px;border-bottom-width:2px;'
        f'border-bottom-style:solid;border-bottom-color:{NAVY};">'
        f'<span style="color:{NAVY};font-family:{FONT};font-size:16px;'
        f'font-weight:bold;">{label} ({count} {plural})</span>'
        f'</td></tr>'
    )


def build_email_html(articles: Iterable[Any], *, briefing_date: str,
                     section_of=None, agency_name: str = "FCC",
                     contract: str = "273FCC26F0061") -> str:
    """Return Outlook-Classic-safe HTML for the briefing."""
    rows: List[Any] = list(articles or [])

    def _cat(a: Any) -> str:
        if section_of is not None:
            try:
                return str(section_of(a) or "Other")
            except Exception:
                pass
        return str(_field(a, "section", "") or _field(a, "topic", "") or "Other")

    grouped: Dict[str, List[Any]] = {}
    for a in rows:
        grouped.setdefault(_cat(a), []).append(a)
    ordered = sorted(grouped.items(), key=lambda kv: (-len(kv[1]), kv[0].lower()))

    count = len(rows)
    header_sub = f"{_esc(briefing_date)} | {count} {'Article' if count == 1 else 'Articles'}"

    body: List[str] = []
    body.append(
        f'<tr><td bgcolor="{NAVY}" style="padding-top:20px;padding-right:20px;'
        f'padding-bottom:20px;padding-left:20px;text-align:center;">'
        f'<span style="color:#ffffff;font-family:{FONT};font-size:18px;'
        f'font-weight:bold;">{_esc(agency_name)} Daily News Summary</span><br/>'
        f'<span style="color:#ffffff;font-family:{FONT};font-size:14px;">'
        f'{header_sub}</span></td></tr>'
    )

    if not rows:
        # An empty briefing must say so. A bare header with nothing under it
        # reads as a broken send.
        body.append(
            f'<tr><td style="padding-top:24px;padding-right:20px;'
            f'padding-bottom:24px;padding-left:20px;">'
            f'<span style="color:{BODY_TEXT};font-family:{FONT};font-size:13px;">'
            f'No articles met the criteria for this briefing.</span></td></tr>'
        )
    else:
        for name, items in ordered:
            body.append(_category_row(name, len(items)))
            for a in items:
                body.append(_article_row(a))

    body.append(
        f'<tr><td bgcolor="{NAVY}" style="padding-top:15px;padding-right:20px;'
        f'padding-bottom:15px;padding-left:20px;text-align:center;">'
        f'<span style="color:#ffffff;font-family:{FONT};font-size:11px;">'
        f'Prepared by Alliance Global Tech, Inc. | Contract {_esc(contract)}'
        f'</span></td></tr>'
    )

    return (
        '<html>\n'
        f'<body style="margin:0;padding:0;background-color:{PAGE_BG};">\n'
        # Outlook ignores max-width, so the width is pinned inside a conditional
        # table that only Outlook sees.
        f'<!--[if mso]><table width="{WIDTH}" cellpadding="0" cellspacing="0" '
        'border="0" align="center"><tr><td><![endif]-->\n'
        f'<table width="{WIDTH}" cellpadding="0" cellspacing="0" border="0" '
        f'align="center" style="width:{WIDTH}px;font-family:{FONT};">\n'
        + "\n".join(body) + "\n"
        '</table>\n'
        '<!--[if mso]></td></tr></table><![endif]-->\n'
        '</body>\n'
        '</html>'
    )


def build_subject(briefing_date: str, agency_name: str = "FCC") -> str:
    return f"{agency_name} Daily News Summary – {briefing_date}"
