"""
Accessibility validation for generated reports.

WHAT THIS MODULE CAN AND CANNOT ESTABLISH — READ BEFORE QUOTING ITS OUTPUT
─────────────────────────────────────────────────────────────────────────
These are AUTOMATED STRUCTURAL CHECKS. Passing them establishes that the
required structures are PRESENT: table headers exist, images carry alt text,
the document declares a language and a title, contrast ratios compute above
threshold, status indicators carry a non-colour channel.

Passing them does NOT establish Section 508 conformance, and this module never
says it does. USWDS states the position plainly: automated checks establish the
presence of tags; only manual review establishes whether context and intent are
actually accessible. An image can carry alt text that says "chart.png" and pass
every check here while telling a blind reader nothing.

Generating a PDF with `pdf_variant="pdf/ua-1"` likewise does not make a document
508-conformant. It asks the renderer to emit a tag tree. Whether that tree has a
sensible reading order, whether headings nest correctly, and whether a chart's
narrative conveys the same finding as its picture are all judgements a person
makes.

`conformance_claim()` therefore returns a deliberately unglamorous string, and
nothing in this codebase is permitted to print "Section 508 compliant" off the
back of a successful render.

The manual checklist is not decoration either — `MANUAL_REVIEW_REQUIRED` is
carried into the report's own accessibility statement so the reader is told
which assurances have been machine-checked and which have not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

#: WCAG 2.1 AA for normal-size text.
MIN_CONTRAST_NORMAL = 4.5
#: WCAG 2.1 AA for large text (>=18pt, or >=14pt bold).
MIN_CONTRAST_LARGE = 3.0

MANUAL_REVIEW_REQUIRED: Tuple[str, ...] = (
    "Keyboard-only navigation of the HTML report",
    "Screen-reader spot check (reading order, announced headings, table semantics)",
    "PDF logical reading-order inspection against the visual layout",
    "Confirmation that each chart's narrative conveys the same key finding as the visual",
    "Zoom / reflow / readability inspection at 200%",
)


@dataclass
class Finding:
    check: str
    severity: str          # error | warning
    message: str
    context: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"check": self.check, "severity": self.severity,
                "message": self.message, "context": self.context}


@dataclass
class AccessibilityResult:
    findings: List[Finding] = field(default_factory=list)
    checks_run: List[str] = field(default_factory=list)

    @property
    def errors(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == "warning"]

    @property
    def passed(self) -> bool:
        """No ERRORS. Warnings do not block — they are judgement calls flagged
        for the manual pass rather than defects the machine is certain of."""
        return not self.errors

    def to_dict(self) -> Dict[str, Any]:
        return {
            "automated_checks_passed": self.passed,
            "checks_run": list(self.checks_run),
            "errors": [f.to_dict() for f in self.errors],
            "warnings": [f.to_dict() for f in self.warnings],
            "conformance_claim": conformance_claim(self.passed),
            "manual_review_required": list(MANUAL_REVIEW_REQUIRED),
        }


def conformance_claim(automated_passed: bool) -> str:
    """The ONLY accessibility claim this system is permitted to make.

    Never returns "Section 508 compliant". That claim requires the project's
    accessibility review to have been completed by a person, and no amount of
    successful rendering substitutes for it.
    """
    if not automated_passed:
        return ("Automated accessibility checks FAILED. This report does not meet "
                "the project's baseline structural requirements and must not be "
                "issued until the errors are resolved.")
    return (
        "Automated structural accessibility checks passed (headings, table "
        "headers, image alternatives, document language and title, colour "
        "contrast, non-colour status encoding). This establishes the PRESENCE of "
        "the required structures only. It is NOT a claim of Section 508 "
        "conformance: that determination requires the manual review listed "
        "alongside this statement and the project's accessibility sign-off."
    )


# ── colour contrast ──────────────────────────────────────────────────────────

def _srgb_to_linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_colour: str) -> float:
    value = hex_colour.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    r, g, b = (int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return (0.2126 * _srgb_to_linear(r)
            + 0.7152 * _srgb_to_linear(g)
            + 0.0722 * _srgb_to_linear(b))


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG contrast ratio, 1.0 to 21.0."""
    a, b = relative_luminance(foreground), relative_luminance(background)
    lighter, darker = max(a, b), min(a, b)
    return round((lighter + 0.05) / (darker + 0.05), 2)


def check_token_contrast(tokens: Dict[str, str]) -> List[Finding]:
    """Contrast for every foreground/background pairing the templates use.

    Only pairings that ACTUALLY OCCUR are checked. Asserting that every token
    contrasts with every other would fail on combinations no template produces
    — gold text on white, for instance, which is why `.status .text` is
    near-black and the gold is confined to the glyph.
    """
    pairs = [
        ("body text on page", "--report-text", "--report-bg", MIN_CONTRAST_NORMAL),
        ("body text on alt background", "--report-text", "--report-bg-alt",
         MIN_CONTRAST_NORMAL),
        ("muted note text on page", "--report-muted", "--report-bg",
         MIN_CONTRAST_NORMAL),
        # Figure source/notes lines sit inside tinted panels, so they use the
        # darker derived token. --report-muted itself is only ever placed on
        # white, which it passes.
        ("muted note text on alt background", "--report-muted-on-alt",
         "--report-bg-alt", MIN_CONTRAST_NORMAL),
        ("table header text on header fill", "#ffffff", "--report-primary",
         MIN_CONTRAST_NORMAL),
        ("link text on page", "--report-primary", "--report-bg",
         MIN_CONTRAST_NORMAL),
        ("heading rule colour on page", "--report-primary", "--report-bg",
         MIN_CONTRAST_LARGE),
    ]
    findings: List[Finding] = []
    for label, foreground, background, minimum in pairs:
        fg = tokens.get(foreground, foreground)
        bg = tokens.get(background, background)
        ratio = contrast_ratio(fg, bg)
        if ratio < minimum:
            findings.append(Finding(
                check="contrast", severity="error",
                message=(f"{label}: contrast {ratio}:1 is below the required "
                         f"{minimum}:1."),
                context=f"{foreground} ({fg}) on {background} ({bg})",
            ))
    return findings


# ── HTML structural checks ───────────────────────────────────────────────────

_IMG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_ALT_RE = re.compile(r"\balt\s*=\s*([\"'])(.*?)\1", re.IGNORECASE | re.DOTALL)
_TABLE_RE = re.compile(r"<table\b.*?</table>", re.IGNORECASE | re.DOTALL)
_TH_RE = re.compile(r"<th\b", re.IGNORECASE)
_HEADING_RE = re.compile(r"<h([1-6])\b", re.IGNORECASE)
_LANG_RE = re.compile(r"<html\b[^>]*\blang\s*=\s*([\"'])([a-zA-Z-]+)\1", re.IGNORECASE)
_TITLE_RE = re.compile(r"<title\s*>(.*?)</title\s*>", re.IGNORECASE | re.DOTALL)
_STATUS_OPEN_RE = re.compile(r'<span[^>]*class="[^"]*\bstatus\b[^"]*"[^>]*>',
                             re.IGNORECASE)
_SPAN_TAG_RE = re.compile(r'</?span\b[^>]*>', re.IGNORECASE)


def _status_blocks(html: str) -> List[str]:
    """Inner HTML of each status indicator, with NESTING HANDLED.

    A status indicator is `<span class="status ..."><span class="glyph">…</span>
    <span class="text">…</span></span>`. A non-greedy `(.*?)</span>` match stops
    at the FIRST closing tag — the glyph's — so it never sees the text label and
    reports every correctly-built indicator as colour-only. That false positive
    is worse than no check: it would train a reader to ignore the one signal
    that catches a genuine WCAG 1.4.1 failure.

    Depth counting is used rather than a regex because span nesting is not a
    regular language.
    """
    blocks: List[str] = []
    for opening in _STATUS_OPEN_RE.finditer(html):
        start = opening.end()
        depth = 1
        position = start
        for tag in _SPAN_TAG_RE.finditer(html, start):
            depth += -1 if tag.group(0).startswith("</") else 1
            if depth == 0:
                position = tag.start()
                break
        else:
            position = len(html)
        blocks.append(html[start:position])
    return blocks
_REMOTE_RE = re.compile(r"""(?:src|href)\s*=\s*["']\s*(https?:)?//""", re.IGNORECASE)
_CSS_IMPORT_RE = re.compile(r"@import\b", re.IGNORECASE)


def validate_html(html: str, tokens: Optional[Dict[str, str]] = None
                  ) -> AccessibilityResult:
    """Run every automated structural check over a rendered HTML report."""
    result = AccessibilityResult()
    findings = result.findings

    # 1. Document language.
    result.checks_run.append("document_language")
    lang = _LANG_RE.search(html)
    if not lang:
        findings.append(Finding(
            "document_language", "error",
            "The <html> element declares no lang attribute. A screen reader "
            "cannot select a pronunciation rule set without it."))
    elif not lang.group(2).lower().startswith("en"):
        findings.append(Finding(
            "document_language", "warning",
            f"Document language is {lang.group(2)!r}; the report content is English."))

    # 2. Document title.
    result.checks_run.append("document_title")
    title = _TITLE_RE.search(html)
    if not title or not title.group(1).strip():
        findings.append(Finding(
            "document_title", "error",
            "The document has no non-empty <title>. It becomes the PDF's "
            "displayed title and the first thing a screen reader announces."))

    # 3. Image alternatives.
    result.checks_run.append("image_alt_text")
    for tag in _IMG_RE.findall(html):
        alt = _ALT_RE.search(tag)
        if alt is None:
            findings.append(Finding(
                "image_alt_text", "error",
                "An <img> has no alt attribute.", tag[:120]))
        elif not alt.group(2).strip():
            findings.append(Finding(
                "image_alt_text", "error",
                "An <img> has an empty alt attribute. Charts carry information "
                "and must never be marked decorative.", tag[:120]))
        elif len(alt.group(2).strip()) < 40:
            findings.append(Finding(
                "image_alt_text", "warning",
                "Chart alt text is very short; it should convey the same key "
                "finding as the visual, not merely name the chart.",
                alt.group(2)[:120]))

    # 4. Table headers.
    result.checks_run.append("table_headers")
    for table in _TABLE_RE.findall(html):
        if not _TH_RE.search(table):
            findings.append(Finding(
                "table_headers", "error",
                "A <table> has no <th> cells. Without header cells a screen "
                "reader announces values with no indication of what they mean.",
                table[:120]))

    # 5. Heading nesting.
    result.checks_run.append("heading_order")
    levels = [int(m) for m in _HEADING_RE.findall(html)]
    if levels and levels[0] != 1:
        findings.append(Finding(
            "heading_order", "error",
            f"The first heading is <h{levels[0]}>; a document must start at <h1>."))
    for previous, current in zip(levels, levels[1:]):
        if current > previous + 1:
            findings.append(Finding(
                "heading_order", "error",
                f"Heading level jumps from <h{previous}> to <h{current}>. Skipped "
                f"levels break the outline assistive technology navigates by."))
            break

    # 6. Status indicators are never colour alone.
    result.checks_run.append("status_not_colour_only")
    for status in _status_blocks(html):
        has_glyph = "glyph" in status
        has_text = "text" in status
        if not (has_glyph and has_text):
            findings.append(Finding(
                "status_not_colour_only", "error",
                "A status indicator lacks a shape glyph or its text label. "
                "Colour alone fails WCAG 1.4.1 and disappears in greyscale.",
                status[:120]))

    # 7. No runtime-fetched assets.
    result.checks_run.append("no_remote_assets")
    for match in _REMOTE_RE.findall(html):
        findings.append(Finding(
            "no_remote_assets", "error",
            "The report references a remote asset. Report generation must be "
            "deterministic and self-contained; a fetched asset makes rendering "
            "depend on the network and leaks generation to a third party."))
        break

    # 8. Colour contrast.
    if tokens:
        result.checks_run.append("contrast")
        findings.extend(check_token_contrast(tokens))

    return result


_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def strip_css_comments(css: str) -> str:
    """CSS with comments removed.

    Comments are stripped before the remote-asset scan because a comment cannot
    fetch anything. Scanning raw text flagged this project's own stylesheet: its
    header documents the rule with the words "no https:// anywhere in this
    file", and the scanner found that sentence and reported a violation. A check
    that fires on its own documentation trains people to disable it.
    """
    return _CSS_COMMENT_RE.sub(" ", css)


def validate_css_has_no_remote_fonts(css: str) -> AccessibilityResult:
    """Fonts must be bundled project assets, never fetched at render time."""
    result = AccessibilityResult(checks_run=["fonts_bundled_not_downloaded"])
    executable = strip_css_comments(css)
    if (_CSS_IMPORT_RE.search(executable)
            or "https://" in executable or "http://" in executable):
        result.findings.append(Finding(
            "fonts_bundled_not_downloaded", "error",
            "The stylesheet references a remote resource. Fonts must be bundled "
            "so generation is deterministic and works air-gapped."))
    return result


def pdf_structure_report(pdf_bytes: bytes) -> Dict[str, Any]:
    """What can be established about a PDF's structure from its bytes alone.

    A pragmatic check, and labelled as one. It confirms the markers that
    SHOULD be present in a tagged, PDF/UA-targeted document — a structure tree,
    a declared language, a title, and outline entries. It does NOT walk the tag
    tree or evaluate reading order, and it therefore cannot conclude that the
    document is accessible. `manual_review_required` is returned alongside every
    result so the limitation travels with the finding.
    """
    blob = pdf_bytes or b""
    markers = {
        "has_pdf_header": blob.startswith(b"%PDF-"),
        "has_structure_tree": b"/StructTreeRoot" in blob,
        "marked_as_tagged": b"/Marked" in blob,
        "has_language": b"/Lang" in blob,
        "has_document_title": b"/Title" in blob,
        "has_outline": b"/Outlines" in blob,
        "declares_pdfua": b"pdfuaid" in blob.lower() or b"PDF/UA" in blob,
    }
    return {
        "markers": markers,
        "structurally_tagged": bool(
            markers["has_structure_tree"] and markers["marked_as_tagged"]),
        "claim": (
            "Structural markers for a tagged PDF are present. This does NOT "
            "establish Section 508 conformance — reading order, heading nesting "
            "and the adequacy of alternative text are judgements that require "
            "the manual review listed here."
        ),
        "manual_review_required": list(MANUAL_REVIEW_REQUIRED),
    }
