"""Create company FS Excel template."""

from pathlib import Path
import pandas as pd

out = Path("templates/company_fs_template.xlsx")
out.parent.mkdir(exist_ok=True)

meta = pd.DataFrame([
    ["Company Name", "Sample Corp Pte Ltd"],
    ["Currency", "SGD"],
    ["Auditor", "Sample Auditor LLP"],
    ["Consolidated", "No"],
])

pnl_labels = [
    "Revenue", "Cost of Sales", "Gross Profit", "Operating Expenses",
    "EBITDA", "Depreciation", "EBIT", "Interest Expense",
    "Profit Before Tax", "Tax", "Profit After Tax",
]
pnl = pd.DataFrame({"Line Item": pnl_labels})
for fy in ["FY2022", "FY2023", "FY2024"]:
    pnl[fy] = [10000000, 6000000, 4000000, 2500000, 1500000, 300000, 1200000,
               200000, 1000000, 150000, 850000][:len(pnl_labels)]

bs_labels = [
    "Total Assets", "Current Assets", "Cash and Equivalents",
    "Trade Receivables", "Inventory", "Total Liabilities",
    "Current Liabilities", "Trade Payables", "Total Debt",
    "Short Term Debt", "Long Term Debt", "Total Equity",
]
bs = pd.DataFrame({"Line Item": bs_labels})
for fy in ["FY2022", "FY2023", "FY2024"]:
    bs[fy] = [8000000 + i * 500000 for i in range(len(bs_labels))]

cf_labels = ["Cash Flow from Operating", "Cash Flow from Investing",
             "Cash Flow from Financing", "Capital Expenditure"]
cf = pd.DataFrame({"Line Item": cf_labels})
for fy in ["FY2022", "FY2023", "FY2024"]:
    cf[fy] = [900000, -200000, -150000, 180000]

with pd.ExcelWriter(out, engine="openpyxl") as w:
    meta.to_excel(w, sheet_name="Meta", index=False, header=False)
    pnl.to_excel(w, sheet_name="PnL", index=False)
    bs.to_excel(w, sheet_name="BalanceSheet", index=False)
    cf.to_excel(w, sheet_name="CashFlow", index=False)

print(f"Created {out}")
