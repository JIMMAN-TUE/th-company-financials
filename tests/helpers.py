from __future__ import annotations

import tempfile
from pathlib import Path

from thfin.config import Config, FinancialsCfg, RegistryCfg, RuntimeCfg, Scope

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
REGISTRY_CSV = FIXTURES / "registry_sample.csv"
STATEMENT_HTML = FIXTURES / "statement_sample.html"
HOST = "datawarehouse.dbd.go.th"
URL = f"https://{HOST}/company/profile/{{jid}}"
ROBOTS = f"https://{HOST}/robots.txt"


class FakeClock:
    """Virtual time: sleep() advances the clock, so rate limiting is testable instantly."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class FakeTransport:
    """Serves canned (status, headers, body). A list value is consumed one item per call."""

    def __init__(self, routes: dict | None = None, default=(404, {}, b"")):
        self.routes = dict(routes or {})
        self.default = default
        self.calls: list[str] = []

    def get(self, url, headers, timeout):
        self.calls.append(url)
        r = self.routes.get(url, self.default)
        if isinstance(r, list):
            r = r.pop(0) if len(r) > 1 else r[0]
        return r


def make_cfg(tmp: str | Path | None = None, **scope_kwargs) -> Config:
    tmp = Path(tmp or tempfile.mkdtemp())
    scope_kwargs.setdefault("fiscal_years", [2566, 2567])
    return Config(
        scope=Scope(**scope_kwargs),
        registry=RegistryCfg(csv_paths=[str(REGISTRY_CSV)]),
        financials=FinancialsCfg(),
        runtime=RuntimeCfg(
            db_path=str(tmp / "t.sqlite"),
            cache_dir=str(tmp / "cache"),
            export_dir=str(tmp / "exports"),
            user_agent="thfin-test/0.1 (+mailto:test@example.org)",
            requests_per_second=5,
            max_retries=2,
            max_consecutive_errors=2,
        ),
    )
