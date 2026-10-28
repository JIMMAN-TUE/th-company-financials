"""SQLite storage + export. Idempotent upserts make every run resumable."""

from __future__ import annotations

import csv
import sqlite3
import weakref
from datetime import datetime, timezone
from pathlib import Path

from .config import Scope
from .models import FINANCIAL_FIELDS, Company, Financials

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
  juristic_id TEXT PRIMARY KEY, name_th TEXT, name_en TEXT, juristic_type TEXT, status TEXT,
  province TEXT, tsic_code TEXT, tsic_desc TEXT, registered_capital REAL, registered_date TEXT
);
CREATE TABLE IF NOT EXISTS financials (
  juristic_id TEXT NOT NULL, fiscal_year INTEGER NOT NULL,
  revenue_total REAL, revenue_sales REAL, cost_of_sales REAL, total_expenses REAL,
  net_profit REAL, total_assets REAL, source TEXT, fetched_at TEXT,
  PRIMARY KEY (juristic_id, fiscal_year)
);
CREATE TABLE IF NOT EXISTS fetch_log (
  juristic_id TEXT PRIMARY KEY, status TEXT NOT NULL, detail TEXT, fetched_at TEXT,
  years TEXT  -- comma-separated fiscal years that were requested when this row was written
);
"""

DONE_STATUSES = ("ok", "not_found", "parse_empty")

EXPORT_COLUMNS = [
    "juristic_id", "name_th", "juristic_type", "province", "tsic_code", "registered_capital",
    "fiscal_year", "revenue_total", "revenue_sales", "cost_of_sales", "total_expenses",
    "net_profit", "total_assets", "gross_profit", "gross_margin_pct", "net_margin_pct", "source",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        weakref.finalize(self, self.db.close)  # never leak the connection, even if close() is forgotten
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(fetch_log)")}
        if "years" not in cols:  # database created by an older version
            self.db.execute("ALTER TABLE fetch_log ADD COLUMN years TEXT")
            self.db.commit()

    def close(self) -> None:
        self.db.close()

    def upsert_companies(self, companies: list[Company]) -> None:
        self.db.executemany(
            """INSERT INTO companies VALUES (:juristic_id,:name_th,:name_en,:juristic_type,:status,
               :province,:tsic_code,:tsic_desc,:registered_capital,:registered_date)
               ON CONFLICT(juristic_id) DO UPDATE SET
               name_th=excluded.name_th, name_en=excluded.name_en,
               juristic_type=excluded.juristic_type, status=excluded.status,
               province=excluded.province, tsic_code=excluded.tsic_code,
               tsic_desc=excluded.tsic_desc, registered_capital=excluded.registered_capital,
               registered_date=excluded.registered_date""",
            [
                {k: getattr(c, k) for k in c.__slots__}
                for c in companies
            ],
        )
        self.db.commit()

    def upsert_financials(self, rows: list[Financials]) -> None:
        cols = ", ".join(FINANCIAL_FIELDS)
        marks = ", ".join(f":{f}" for f in FINANCIAL_FIELDS)
        updates = ", ".join(f"{f}=excluded.{f}" for f in FINANCIAL_FIELDS)
        self.db.executemany(
            f"""INSERT INTO financials (juristic_id, fiscal_year, {cols}, source, fetched_at)
                VALUES (:juristic_id, :fiscal_year, {marks}, :source, :fetched_at)
                ON CONFLICT(juristic_id, fiscal_year) DO UPDATE SET {updates},
                source=excluded.source, fetched_at=excluded.fetched_at""",
            [{**{k: getattr(r, k) for k in r.__slots__}, "fetched_at": _now()} for r in rows],
        )
        self.db.commit()

    def mark(self, juristic_id: str, status: str, detail: str = "", years: list[int] | None = None) -> None:
        self.db.execute(
            """INSERT INTO fetch_log (juristic_id, status, detail, fetched_at, years) VALUES (?,?,?,?,?)
               ON CONFLICT(juristic_id) DO UPDATE SET status=excluded.status,
               detail=excluded.detail, fetched_at=excluded.fetched_at, years=excluded.years""",
            (juristic_id, status, detail[:500], _now(), ",".join(str(y) for y in sorted(years or []))),
        )
        self.db.commit()

    def done_ids(self, years: list[int] | None = None) -> set[str]:
        """Companies that need no more work. An `ok` company only counts as done if it was collected
        for every requested year, so widening years_back later re-processes it (from the HTTP cache
        when the page is still cached, so usually without new network calls)."""
        marks = ",".join("?" * len(DONE_STATUSES))
        cur = self.db.execute(
            f"SELECT juristic_id, status, years FROM fetch_log WHERE status IN ({marks})", DONE_STATUSES
        )
        want = set(years or [])
        done: set[str] = set()
        for jid, status, have in cur:
            if status != "ok" or not want:
                done.add(jid)
            elif want <= {int(y) for y in (have or "").split(",") if y.strip().isdigit()}:
                done.add(jid)
        return done

    def status_counts(self) -> dict[str, int]:
        return {r[0]: r[1] for r in self.db.execute("SELECT status, COUNT(*) FROM fetch_log GROUP BY status")}

    def counts(self) -> dict[str, int]:
        q = self.db.execute
        return {
            "companies": q("SELECT COUNT(*) FROM companies").fetchone()[0],
            "financial_rows": q("SELECT COUNT(*) FROM financials").fetchone()[0],
            "companies_with_financials": q("SELECT COUNT(DISTINCT juristic_id) FROM financials").fetchone()[0],
        }

    def export_rows(self, scope: Scope) -> list[dict]:
        """Financial rows joined to company info, filtered by the scope's years and revenue band."""
        where, params = [], []
        years = scope.effective_years()
        where.append(f"f.fiscal_year IN ({','.join('?' * len(years))})")
        params += years
        if scope.revenue_min is not None:
            where.append("f.revenue_total >= ?")
            params.append(scope.revenue_min)
        if scope.revenue_max is not None:
            where.append("f.revenue_total <= ?")
            params.append(scope.revenue_max)
        sql = (
            "SELECT c.name_th, c.juristic_type, c.province, c.tsic_code, c.registered_capital, f.* "
            "FROM financials f LEFT JOIN companies c USING (juristic_id)"
            + (" WHERE " + " AND ".join(where) if where else "")
            + " ORDER BY f.juristic_id, f.fiscal_year"
        )
        out = []
        for r in self.db.execute(sql, params):
            d = dict(r)
            rev, cost, net = d.get("revenue_total"), d.get("cost_of_sales"), d.get("net_profit")
            base = rev if rev else d.get("revenue_sales")
            d["gross_profit"] = (base - cost) if (base is not None and cost is not None) else None
            d["gross_margin_pct"] = round(100 * d["gross_profit"] / base, 2) if d["gross_profit"] is not None and base else None
            d["net_margin_pct"] = round(100 * net / rev, 2) if (net is not None and rev) else None
            out.append({k: d.get(k) for k in EXPORT_COLUMNS})
        return out


def write_csv(rows: list[dict], path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8-sig", newline="") as fh:  # BOM so Excel shows Thai correctly
        w = csv.DictWriter(fh, fieldnames=EXPORT_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return p


def write_xlsx(rows: list[dict], path: str | Path) -> Path:
    from openpyxl import Workbook

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "financials"
    ws.append(EXPORT_COLUMNS)
    for r in rows:
        ws.append([r.get(c) for c in EXPORT_COLUMNS])
    ws.freeze_panes = "A2"
    wb.save(p)
    return p
