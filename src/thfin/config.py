"""Configuration models.

Everything a teammate may want to change lives in YAML (see config/scope.yaml).
Unknown keys are rejected so typos fail loudly instead of silently widening the scope.
"""

from __future__ import annotations

import copy
import os
import re
from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

ID_RE = re.compile(r"^\d{13}$")


class ConfigError(Exception):
    """Raised for any problem with configuration or scope."""


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _as_list(v: Any) -> Any:
    """Allow `--set scope.provinces=A,B` (comma string) and empty values."""
    if v is None:
        return []
    if isinstance(v, str):
        return [p.strip() for p in v.split(",") if p.strip()]
    return v


class Scope(_Base):
    """Which companies and fiscal years to collect. Empty list = no filter on that field."""

    provinces: list[str] = Field(default_factory=list)
    tsic_prefixes: list[str] = Field(default_factory=list)
    juristic_types: list[str] = Field(default_factory=lambda: ["บริษัทจำกัด", "บริษัทมหาชนจำกัด"])
    statuses: list[str] = Field(default_factory=lambda: ["ดำเนินกิจการอยู่"])
    name_contains: list[str] = Field(default_factory=list)
    capital_min: float | None = None
    capital_max: float | None = None
    # Years to collect. Either list them explicitly (fiscal_years wins when non-empty) or say
    # "the last N years" with years_back. latest_fiscal_year null = last completed year
    # (current Buddhist-Era year minus 1). Set it by hand if the newest year is not filed yet.
    fiscal_years: list[int] = Field(default_factory=list)
    years_back: int = Field(3, ge=1, le=15)
    latest_fiscal_year: int | None = None
    revenue_min: float | None = None
    revenue_max: float | None = None
    include_ids: list[str] = Field(default_factory=list)
    exclude_ids: list[str] = Field(default_factory=list)
    max_companies: int = Field(200, ge=1)

    @field_validator(
        "provinces",
        "tsic_prefixes",
        "juristic_types",
        "statuses",
        "name_contains",
        "fiscal_years",
        "include_ids",
        "exclude_ids",
        mode="before",
    )
    @classmethod
    def _listify(cls, v: Any) -> Any:
        return _as_list(v)

    @field_validator("include_ids", "exclude_ids", mode="before")
    @classmethod
    def _ids_must_be_strings(cls, v: Any) -> Any:
        v = _as_list(v)
        for x in v:
            if not isinstance(x, str):
                raise ValueError(
                    "juristic IDs must be quoted strings in YAML (e.g. '0105536001234'): "
                    "unquoted numbers lose their leading zero"
                )
            if not ID_RE.match(x):
                raise ValueError(f"juristic ID must be exactly 13 digits: {x!r}")
        return v

    @field_validator("tsic_prefixes", mode="before")
    @classmethod
    def _tsic_strings(cls, v: Any) -> Any:
        return [str(x) for x in _as_list(v)]

    @field_validator("fiscal_years")
    @classmethod
    def _years_to_buddhist_era(cls, v: list[int]) -> list[int]:
        # DBD reports in Buddhist Era. Accept Gregorian years too and convert.
        return sorted({y + 543 if y < 2400 else y for y in v})

    @field_validator("latest_fiscal_year")
    @classmethod
    def _latest_to_buddhist_era(cls, v: int | None) -> int | None:
        return None if v is None else (v + 543 if v < 2400 else v)

    def effective_years(self, today: date | None = None) -> list[int]:
        """The fiscal years (B.E.) this scope collects, oldest first."""
        if self.fiscal_years:
            return list(self.fiscal_years)
        today = today or date.today()
        latest = self.latest_fiscal_year or (today.year + 543 - 1)
        return list(range(latest - self.years_back + 1, latest + 1))


class RegistryCfg(_Base):
    """Where the company universe comes from (DBD open-data CSV exports)."""

    csv_paths: list[str] = Field(default_factory=lambda: ["data/registry/*.csv"])
    encoding: str = "utf-8-sig"
    delimiter: str = ","
    # field -> exact CSV header. Only needed when auto-detection fails (see `registry-headers`).
    columns: dict[str, str] = Field(default_factory=dict)

    @field_validator("csv_paths", mode="before")
    @classmethod
    def _listify(cls, v: Any) -> Any:
        return _as_list(v)


class FinancialsCfg(_Base):
    mode: Literal["web", "files"] = "web"
    # UNVERIFIED placeholder. Confirm the real URL pattern with `thfin probe` (docs/HANDOVER.md).
    url_template: str = "https://datawarehouse.dbd.go.th/company/profile/{juristic_id}"
    files_dir: str = "data/statements"
    unit_multiplier: float = 1.0  # set to 1000 if a source reports in thousands of baht


class RuntimeCfg(_Base):
    transport: Literal["urllib", "chromium"] = "urllib"
    chromium_executable: str | None = None
    chromium_headless: bool = True
    chromium_wait_selector: str = "table"
    db_path: str = "data/thfin.sqlite"
    cache_dir: str = ".cache/http"
    export_dir: str = "data/exports"
    # Put a real contact so site owners can reach you. `run` refuses to start with the placeholder.
    user_agent: str = "thfin-research/0.1 (+mailto:CHANGE-ME@example.com)"
    requests_per_second: float = Field(0.5, gt=0, le=5)
    timeout_seconds: float = Field(30, gt=0)
    max_retries: int = Field(3, ge=0, le=10)
    respect_robots: bool = True
    # Safety net: the client refuses any host not listed here.
    allowed_hosts: list[str] = Field(default_factory=lambda: ["datawarehouse.dbd.go.th"])
    max_consecutive_errors: int = Field(10, ge=1)

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def _listify(cls, v: Any) -> Any:
        return _as_list(v)


class Config(_Base):
    scope: Scope = Field(default_factory=Scope)
    registry: RegistryCfg = Field(default_factory=RegistryCfg)
    financials: FinancialsCfg = Field(default_factory=FinancialsCfg)
    runtime: RuntimeCfg = Field(default_factory=RuntimeCfg)


def apply_overrides(raw: dict[str, Any], overrides: list[str]) -> dict[str, Any]:
    """Apply `dotted.key=value` overrides (value parsed as YAML) to a raw config dict."""
    out = copy.deepcopy(raw)
    for item in overrides:
        key, sep, val = item.partition("=")
        if not sep or not key.strip():
            raise ConfigError(f"--set expects key=value, got {item!r}")
        parts = key.strip().split(".")
        node: Any = out
        for p in parts[:-1]:
            node = node.setdefault(p, {})
            if not isinstance(node, dict):
                raise ConfigError(f"--set {key!r}: {p!r} is not a section")
        node[parts[-1]] = yaml.safe_load(val)
    return out


def load_config(path: str | Path, overrides: list[str] | None = None) -> Config:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"config file not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{p}: top level must be a mapping")
    # Precedence: --set  >  environment variable  >  YAML. Keeps personal contact out of git.
    env_ua = os.environ.get("THFIN_USER_AGENT", "").strip()
    if env_ua:
        if not isinstance(raw.get("runtime"), dict):
            raw["runtime"] = {}
        raw["runtime"]["user_agent"] = env_ua
    raw = apply_overrides(raw, overrides or [])
    try:
        return Config.model_validate(raw)
    except Exception as e:  # pydantic.ValidationError -> friendlier message
        raise ConfigError(f"invalid config {p}:\n{e}") from e
