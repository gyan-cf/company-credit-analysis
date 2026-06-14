"""
Markdown → HTML rendering pipeline for the credit report.

LLM produces markdown. We render to HTML once with `markdown-it-py` (GFM
tables enabled), sanitize with `bleach` + `tinycss2` so table-cell
`text-align:right` survives, and use that single HTML for both the React
workspace (`dangerouslySetInnerHTML`) and the .docx export (htmldocx).

Why this exists:
- One styling source of truth across the web view + the Word export
- DOCX export via htmldocx preserves nested lists + GFM tables + bold
  runs that the previous hand-rolled markdown→DOCX converter flattened
- Future-proofs citation chips, inline SVGs, footnotes, etc.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

import bleach
from bleach.css_sanitizer import CSSSanitizer
from markdown_it import MarkdownIt


# Allow-list — generous for credit-memo content; LLM output isn't analyst
# input so the XSS risk surface is small.
_ALLOWED_TAGS = {
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "br", "hr",
    "strong", "em", "b", "i", "u", "s", "del",
    "ul", "ol", "li",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td",
    "blockquote", "pre", "code",
    "a", "span", "div",
}

# `style` is allowed on table cells specifically so the right-alignment
# markdown-it emits for `|--:|` columns survives sanitization. Limited to
# `text-align` only via the CSSSanitizer below.
_ALLOWED_ATTRS = {
    "a":     ["href", "title", "target", "rel"],
    "span":  ["class"],
    "div":   ["class"],
    "table": ["class"],
    "th":    ["align", "style", "colspan", "rowspan"],
    "td":    ["align", "style", "colspan", "rowspan"],
}

_CSS_SANITIZER = CSSSanitizer(allowed_css_properties=["text-align", "background-color"])


@lru_cache(maxsize=1)
def _md_renderer() -> MarkdownIt:
    """Singleton markdown renderer with GFM tables + strikethrough enabled."""
    return MarkdownIt(
        "default",
        {"html": False, "breaks": False, "linkify": False, "typographer": True},
    ).enable(["table", "strikethrough"])


def markdown_to_html(md: Optional[str]) -> str:
    """Render markdown → sanitized HTML safe for `dangerouslySetInnerHTML`."""
    if not md:
        return ""
    raw = _md_renderer().render(md)
    safe = bleach.clean(
        raw,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        css_sanitizer=_CSS_SANITIZER,
        strip=True,
    )
    return _colorize_ratio_tables(safe)


# ---- Ratio table colorization ------------------------------------------------

def _colorize_ratio_tables(html: str) -> str:
    """
    Walk the HTML; for tables whose first column matches known ratio
    labels (Current ratio, Debt/EBITDA, Interest cover, margins, …),
    colour the FY value cells per policy band (Pass / Watch / Risk).

    Colours go in both a CSS class (for the React workspace) and an
    inline `background-color` style (so htmldocx preserves the fill
    in the .docx export).
    """
    if "<table" not in html:
        return html
    from bs4 import BeautifulSoup
    from .ratio_policy import (
        match_ratio_key, parse_ratio_value, policy_status, STATUS_BG,
    )

    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        # Choose where the data rows live. Tables that use thead/tbody have
        # the header in thead; tables that don't, treat the first row as a
        # header by convention.
        body = table.find("tbody")
        rows = body.find_all("tr") if body else table.find_all("tr")[1:]
        any_match = False
        for row in rows:
            cells = row.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            label_text = cells[0].get_text()
            ratio_key = match_ratio_key(label_text)
            if not ratio_key:
                continue
            any_match = True
            for cell in cells[1:]:
                text = cell.get_text()
                value = parse_ratio_value(text)
                status = policy_status(ratio_key, value)
                if not status:
                    continue
                existing_classes = cell.get("class", [])
                if isinstance(existing_classes, str):
                    existing_classes = existing_classes.split()
                existing_classes.append(f"ratio-{status}")
                cell["class"] = list(dict.fromkeys(existing_classes))
                # Merge inline style — keep any existing text-align.
                existing_style = (cell.get("style") or "").strip().rstrip(";")
                bg = f"background-color: {STATUS_BG[status]}"
                cell["style"] = "; ".join(filter(None, [existing_style, bg]))
        if any_match:
            table_classes = table.get("class", [])
            if isinstance(table_classes, str):
                table_classes = table_classes.split()
            if "ratio-table-colored" not in table_classes:
                table_classes.append("ratio-table-colored")
                table["class"] = table_classes
    return str(soup)
