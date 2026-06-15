"""
Section template for the Credit Analysis Report (FS-only Phase 1).

Sections that need commercial / web-research input we cannot satisfy on FS data
alone are skipped for now (Facility Context, Management & Governance,
Conditions Precedent, Covenants & Monitoring). Active FS-only sections are
numbered contiguously so the workspace and exported report do not look as if
sections failed to generate.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


# ---- Section definitions ------------------------------------------------------

SECTIONS_FS_ONLY: List[Dict[str, Any]] = [
    {
        "code": "executive_credit_view",
        "number": 1,
        "title": "Executive Credit View",
        "type": "llm",
        "guidance": (
            "Open with a one-sentence verdict (e.g. 'High credit risk / watchlist...' "
            "or 'Investment-grade quality...'). Then 2-3 concise bullets summarising "
            "profitability, liquidity, leverage, and cash runway. Finish with a one-"
            "sentence recommended action."
        ),
    },
    {
        "code": "borrower_profile",
        "number": 2,
        "title": "Borrower and Business Profile",
        "type": "llm",
        "guidance": (
            "Cover entity name, UEN, incorporation, primary SSIC activity, financial "
            "framework (SFRS / SFRS(I)), audit status, consolidation perimeter. "
            "1-2 short paragraphs. Do not invent commercial or product details "
            "the data doesn't reveal."
        ),
    },
    # Facility Context — skipped for FS-only scope
    {
        "code": "financial_snapshot",
        "number": 3,
        "title": "Financial Snapshot",
        "type": "deterministic_table",
    },
    {
        "code": "revenue_momentum",
        "number": 4,
        "title": "Revenue and Business Momentum",
        "type": "llm",
        "guidance": (
            "Describe revenue trajectory across the reviewed FYs with absolute "
            "and YoY % figures. Identify direction (growth / decline / stagnation) "
            "and call out any acceleration or step changes. Comment on whether "
            "scale is material vs SG SME size band (group sales ≤ S$100M)."
        ),
    },
    {
        "code": "profitability",
        "number": 5,
        "title": "Profitability Analysis",
        "type": "llm",
        "guidance": (
            "Cover gross margin, EBITDA margin, EBIT margin, PAT margin, ROA, ROE. "
            "Comment on direction and absolute levels. Compare to standard SG SME "
            "thresholds where useful (EBITDA margin > 10% is healthy).\n\n"
            "Open with a GFM markdown table summarising the key margin ratios "
            "across the reviewed FYs (one row per ratio, one column per FY, "
            "with the first column labelled exactly: 'Gross margin', "
            "'EBITDA margin', 'EBIT margin', 'PAT margin', 'Return on equity', "
            "'Return on assets'). Right-align the value columns with `|--:|`."
        ),
    },
    {
        "code": "liquidity_wc",
        "number": 6,
        "title": "Liquidity and Working Capital",
        "type": "llm",
        "guidance": (
            "Cover current ratio, quick ratio, cash position, working-capital days "
            "(receivable / payable / inventory days). Highlight stress points "
            "(current ratio < 1, days > 90). Tie back to short-term debt service.\n\n"
            "Open with a GFM markdown table summarising the key liquidity ratios "
            "across the reviewed FYs (one row per ratio, one column per FY, with "
            "the first column labelled exactly: 'Current ratio', 'Quick ratio', "
            "'Cash ratio', 'Receivable days', 'Payable days', 'Inventory days'). "
            "Right-align value columns with `|--:|`."
        ),
    },
    {
        "code": "cash_flow",
        "number": 7,
        "title": "Cash Flow Analysis",
        "type": "llm",
        "guidance": (
            "Operating cash flow, capex, free cash flow. Cash quality (CFO / PAT). "
            "FCF / debt-service capacity. Highlight whether CFO is structurally "
            "negative; if so, ask where the cash is coming from.\n\n"
            "Include a GFM markdown table for the cash-flow coverage ratios "
            "across the reviewed FYs (rows labelled 'CFO / debt' and "
            "'FCF / debt', one column per FY). Right-align value columns "
            "with `|--:|`."
        ),
    },
    {
        "code": "bs_capital",
        "number": 8,
        "title": "Balance Sheet and Capital Structure",
        "type": "llm",
        "guidance": (
            "Capital structure: equity, retained earnings / accumulated losses, "
            "debt mix (short / long term), debt / equity, debt / EBITDA, "
            "interest cover. Flag negative equity (capital deficiency) explicitly.\n\n"
            "Include a GFM markdown table for the leverage ratios across the "
            "reviewed FYs (rows labelled 'Debt / equity', 'Debt / EBITDA', "
            "'Interest coverage', one column per FY). Right-align value "
            "columns with `|--:|`."
        ),
    },
    {
        "code": "receivables_rpt",
        "number": 9,
        "title": "Receivables and Related-Party Risk",
        "type": "llm",
        "guidance": (
            "Receivable days, growth, concentration. If related-party balances are "
            "visible in the notes excerpt, comment on materiality and arm's-length "
            "concerns. Flag if trade receivables exceed cash significantly."
        ),
    },
    {
        "code": "payables",
        "number": 10,
        "title": "Payables and Accrued Liabilities",
        "type": "llm",
        "guidance": (
            "Payable days, growth in payables, accrued liabilities. Flag stretched "
            "payables (> 120 days) as a potential working-capital stress signal "
            "or supplier-funded growth."
        ),
    },
    {
        "code": "fx_geo",
        "number": 11,
        "title": "Foreign Currency and Geographic Exposure",
        "type": "llm",
        "guidance": (
            "If the notes excerpt provided mentions FX exposure, foreign "
            "subsidiaries, or non-SGD receivables / payables, summarise. "
            "Otherwise note explicitly that the FS extraction did not surface "
            "specific FX disclosures."
        ),
    },
    # Management & Governance — skipped for FS-only scope
    {
        "code": "strengths",
        "number": 12,
        "title": "Key Credit Strengths",
        "type": "llm",
        "guidance": (
            "Bullet list of 3-5 specific strengths backed by numbers from the "
            "data provided. If there are very few credit strengths, say so "
            "honestly — do not manufacture positives."
        ),
    },
    {
        "code": "weaknesses",
        "number": 13,
        "title": "Key Credit Weaknesses",
        "type": "llm",
        "guidance": (
            "Bullet list of 3-5 specific weaknesses backed by numbers. Be "
            "direct and evidence-led; tie each weakness to a numeric anchor."
        ),
    },
    {
        "code": "preliminary_rating",
        "number": 14,
        "title": "Preliminary Risk Rating",
        "type": "llm",
        "guidance": (
            "Assign one of: Low Risk / Moderate Risk / High Risk / Watchlist. "
            "Justify with 2-3 sentences referencing key drivers (leverage, "
            "liquidity, profitability, runway). For FS-only, lean conservative "
            "and acknowledge what bank conduct / bureau / management input "
            "would change."
        ),
    },
    {
        "code": "recommendation",
        "number": 15,
        "title": "Credit Decision Recommendation",
        "type": "llm",
        "guidance": (
            "Recommend one of: Decline / Refer Back / Approve with Conditions / "
            "Approve. For FS-only assessment, lean to a provisional "
            "recommendation acknowledging that bank conduct, bureau, and "
            "management review are still pending."
        ),
    },
    # CPs / Covenants — skipped for FS-only scope
    {
        "code": "questions_management",
        "number": 16,
        "title": "Questions for Management",
        "type": "llm",
        "guidance": (
            "5-8 specific, focused questions targeted at the issues raised. "
            "Probes that, if answered, would tip the credit assessment in "
            "either direction. Number them."
        ),
    },
    {
        "code": "final_opinion",
        "number": 17,
        "title": "Final Credit Opinion",
        "type": "llm",
        "guidance": (
            "1-2 paragraphs synthesising the full assessment into a final view. "
            "Should be quotable in a one-page summary for committee."
        ),
    },
]


# ---- Section context builder --------------------------------------------------

def _kv_lines(d: Dict[str, Any], keys: List[str]) -> List[str]:
    out = []
    for k in keys:
        v = d.get(k)
        if v is not None:
            out.append(f"- {k}: {v}")
    return out


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _fy_block(by_fy: Dict[str, Any], fy: str, fields: List[str], source: str = "raw") -> List[str]:
    lines = [f"### {fy}"]
    data = (by_fy.get(fy) or {}).get(source) or {}
    for k in fields:
        v = data.get(k)
        if v is not None:
            lines.append(f"- {k}: {v}")
    return lines


def _entity_basics(context: Dict[str, Any]) -> List[str]:
    analytics = context.get("analytics", {}) or {}
    acra = context.get("acra_profile", {}) or {}
    case_profile = context.get("case", {}) or {}
    ent = analytics.get("entity") or {}
    name = _first_non_empty(
        acra.get("entity_name"),
        ent.get("name"),
        case_profile.get("company_name"),
        "the Borrower",
    )
    ssic_code = _first_non_empty(
        acra.get("primary_ssic_code"),
        ent.get("ssic_code"),
        ent.get("ssic"),
        case_profile.get("primary_ssic_code"),
        "n/a",
    )
    ssic_desc = _first_non_empty(
        acra.get("primary_ssic_desc"),
        ent.get("ssic_description"),
        case_profile.get("primary_ssic_desc"),
        case_profile.get("industry_hint"),
        "n/a",
    )
    return [
        "## Entity basics",
        f"- Name: {name}",
        f"- UEN: {_first_non_empty(acra.get('uen'), ent.get('uen'), case_profile.get('uen'), case_profile.get('cin'), 'n/a')}",
        f"- Primary SSIC: {ssic_code} - {ssic_desc}",
        f"- Framework: {ent.get('framework') or acra.get('accounting_standards') or 'SFRS'}",
        f"- Audited: {ent.get('audited') or acra.get('audited')}",
        f"- Consolidated: {ent.get('consolidated', False)}",
        f"- FYs reviewed: {', '.join(analytics.get('fys', []))}",
    ]
    name = (
        acra.get("entity_name") or ent.get("name") or "the Borrower"
    )
    out = [
        "## Entity basics",
        f"- Name: {name}",
        f"- UEN: {acra.get('uen') or ent.get('uen') or 'n/a'}",
        f"- Primary SSIC: "
        f"{acra.get('primary_ssic_code') or ent.get('ssic_code') or 'n/a'} — "
        f"{acra.get('primary_ssic_desc') or ent.get('ssic_description') or 'n/a'}",
        f"- Framework: {ent.get('framework') or acra.get('accounting_standards') or 'SFRS'}",
        f"- Audited: {ent.get('audited') or acra.get('audited')}",
        f"- Consolidated: {ent.get('consolidated', False)}",
        f"- FYs reviewed: {', '.join(analytics.get('fys', []))}",
    ]
    return out


def _all_ratios_and_trends(analytics: Dict[str, Any]) -> List[str]:
    fys = analytics.get("fys", [])
    by_fy = analytics.get("by_fy", {})
    trends = analytics.get("trends", {})
    out = ["## Latest FY ratios"]
    latest = by_fy.get(fys[0], {}) if fys else {}
    for k, v in (latest.get("ratios") or {}).items():
        if v is not None:
            out.append(f"- {k}: {v}")
    out.append("")
    out.append("## YoY trends")
    for k, v in (trends or {}).items():
        if v is not None:
            out.append(f"- {k}: {v}")
    out.append("")
    out.append("## Raw figures across FYs")
    for fy in fys:
        out.append(f"### {fy}")
        for k, v in ((by_fy.get(fy, {}).get("raw") or {}).items()):
            if v is not None:
                out.append(f"- {k}: {v}")
        out.append("")
    return out


def _notes_excerpt(context: Dict[str, Any], keywords: List[str], limit: int = 3) -> List[str]:
    """Pull note-item markdowns whose title contains any of the keywords."""
    out: List[str] = []
    for doc in context.get("documents", []) or []:
        for block in doc.get("blocks", []) or []:
            if block.get("kind") != "notes":
                continue
            for item in block.get("items", []) or []:
                title = (item.get("title") or "").lower()
                if not any(k.lower() in title for k in keywords):
                    continue
                md = (item.get("markdown") or "").strip()
                if md:
                    out.append(f"### {item.get('title')}\n{md[:900]}")
                if len(out) >= limit:
                    return out
    return out


def build_section_context(section_def: Dict[str, Any], context: Dict[str, Any]) -> str:
    """Return the data slice + guidance for one section as plain markdown."""
    analytics = context.get("analytics", {})
    fys = analytics.get("fys", [])
    by_fy = analytics.get("by_fy", {})
    trends = analytics.get("trends", {})

    blob: List[str] = []
    blob.extend(_entity_basics(context))
    blob.append("")

    code = section_def["code"]

    if code in (
        "executive_credit_view",
        "strengths",
        "weaknesses",
        "preliminary_rating",
        "recommendation",
        "final_opinion",
        "questions_management",
    ):
        blob.extend(_all_ratios_and_trends(analytics))

    elif code == "revenue_momentum":
        blob.append("## Revenue and gross profit by FY")
        for fy in fys:
            r = (by_fy.get(fy, {}).get("raw") or {})
            blob.append(f"### {fy}")
            for k in ("revenue", "cost_of_sales", "gross_profit", "other_income"):
                v = r.get(k)
                if v is not None:
                    blob.append(f"- {k}: {v}")
        blob.append("")
        if trends.get("revenue_growth_yoy") is not None:
            blob.append(f"Revenue YoY change: {trends['revenue_growth_yoy'] * 100:.1f}%")

    elif code == "profitability":
        blob.append("## Margins by FY")
        for fy in fys:
            r = (by_fy.get(fy, {}).get("ratios") or {})
            blob.append(f"### {fy}")
            for m in ("gross_margin", "ebitda_margin", "ebit_margin", "pat_margin",
                      "return_on_assets", "return_on_equity"):
                v = r.get(m)
                if v is not None:
                    blob.append(f"- {m}: {v}")
        blob.append("\n## P&L lines by FY")
        for fy in fys:
            blob.extend(_fy_block(
                by_fy, fy,
                ["revenue", "gross_profit", "ebitda", "ebit", "pat", "tax", "interest_expense"],
            ))

    elif code == "liquidity_wc":
        blob.append("## Liquidity ratios by FY")
        for fy in fys:
            r = (by_fy.get(fy, {}).get("ratios") or {})
            blob.append(f"### {fy}")
            for m in ("current_ratio", "quick_ratio", "cash_ratio",
                      "receivable_days", "payable_days", "inventory_days"):
                v = r.get(m)
                if v is not None:
                    blob.append(f"- {m}: {v}")
        blob.append("\n## Balance sheet lines by FY")
        for fy in fys:
            blob.extend(_fy_block(
                by_fy, fy,
                ["cash", "trade_receivables", "trade_payables", "inventory",
                 "current_assets", "current_liabilities"],
            ))

    elif code == "cash_flow":
        blob.append("## Cash flow by FY")
        for fy in fys:
            blob.extend(_fy_block(
                by_fy, fy,
                ["cfo", "capex", "fcf", "interest_paid", "tax_paid", "pat"],
            ))
        blob.append("\n## CFO / FCF ratios")
        for fy in fys:
            r = (by_fy.get(fy, {}).get("ratios") or {})
            blob.append(f"### {fy}")
            for m in ("cfo_to_debt", "fcf_to_debt"):
                v = r.get(m)
                if v is not None:
                    blob.append(f"- {m}: {v}")

    elif code == "bs_capital":
        blob.append("## Capital structure by FY")
        for fy in fys:
            blob.extend(_fy_block(
                by_fy, fy,
                ["total_equity", "total_debt", "short_term_debt", "long_term_debt",
                 "total_assets", "total_liabilities"],
            ))
        blob.append("\n## Leverage ratios")
        for fy in fys:
            r = (by_fy.get(fy, {}).get("ratios") or {})
            blob.append(f"### {fy}")
            for m in ("debt_equity", "debt_ebitda", "interest_coverage"):
                v = r.get(m)
                if v is not None:
                    blob.append(f"- {m}: {v}")

    elif code == "receivables_rpt":
        blob.append("## Receivables by FY")
        for fy in fys:
            blob.extend(_fy_block(
                by_fy, fy,
                ["trade_receivables", "revenue", "current_assets"],
            ))
            r = (by_fy.get(fy, {}).get("ratios") or {})
            v = r.get("receivable_days")
            if v is not None:
                blob.append(f"- receivable_days: {v}")
        excerpts = _notes_excerpt(context, ["receivable", "related party", "related-party"])
        if excerpts:
            blob.append("\n## Related-party / receivable notes from the filings")
            blob.extend(excerpts)

    elif code == "payables":
        blob.append("## Payables by FY")
        for fy in fys:
            blob.extend(_fy_block(
                by_fy, fy,
                ["trade_payables", "cost_of_sales", "current_liabilities"],
            ))
            r = (by_fy.get(fy, {}).get("ratios") or {})
            v = r.get("payable_days")
            if v is not None:
                blob.append(f"- payable_days: {v}")

    elif code == "fx_geo":
        excerpts = _notes_excerpt(
            context,
            ["foreign", "currency", "geographic", "concentration", "subsid", "exposure"],
        )
        if excerpts:
            blob.append("## FX / Geographic notes from filings")
            blob.extend(excerpts)
        else:
            blob.append("(No specific FX / Geographic notes extracted from the filings.)")

    elif code == "borrower_profile":
        ap = context.get("acra_profile", {}) or {}
        case_profile = context.get("case", {}) or {}
        blob.append("## ACRA profile fields")
        for k in (
            "entity_type", "entity_status", "incorporation_date",
            "primary_ssic_code", "primary_ssic_desc",
            "company_type", "small_company_exemption",
            "audited", "accounting_standards", "consolidated_level",
        ):
            v = ap.get(k)
            if v is not None:
                blob.append(f"- {k}: {v}")
        if case_profile:
            blob.append("")
            blob.append("## Onboarding case profile fields")
            for k in (
                "company_name", "uen", "entity_type", "company_status",
                "incorporation_date", "fiscal_year_end",
                "primary_ssic_code", "primary_ssic_desc",
                "industry_hint", "registered_address",
                "country", "jurisdiction",
            ):
                v = case_profile.get(k)
                if v not in (None, ""):
                    blob.append(f"- {k}: {v}")
            blob.append("")
            blob.append("Use onboarding case profile fields when ACRA profile fields are blank.")

    # Append the section-specific guidance
    blob.append("")
    blob.append("## Section task")
    blob.append(section_def.get("guidance", ""))

    return "\n".join(blob)
