from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Company:
    juristic_id: str
    name_th: str = ""
    name_en: str = ""
    juristic_type: str = ""
    status: str = ""
    province: str = ""
    tsic_code: str = ""
    tsic_desc: str = ""
    registered_capital: float | None = None
    registered_date: str = ""


FINANCIAL_FIELDS = (
    "revenue_total",
    "revenue_sales",
    "cost_of_sales",
    "total_expenses",
    "net_profit",
    "total_assets",
)


@dataclass(slots=True)
class Financials:
    juristic_id: str
    fiscal_year: int  # Buddhist Era, as reported by DBD
    revenue_total: float | None = None
    revenue_sales: float | None = None
    cost_of_sales: float | None = None
    total_expenses: float | None = None
    net_profit: float | None = None
    total_assets: float | None = None
    source: str = ""
