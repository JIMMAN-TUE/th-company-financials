"""Company universe: read DBD open-data CSV exports and apply the configured scope.

Column names in the real exports are not guaranteed, so headers are auto-detected from
ALIASES and can be overridden in config (`registry.columns`). Use `thfin registry-headers`
to see what the files actually contain.
"""

from __future__ import annotations

import csv
import glob
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config, ConfigError, Scope
from ..models import Company
from ..parsers.numbers import parse_number

ALIASES: dict[str, list[str]] = {
    "juristic_id": [
        "juristic_id", "juristic_person_id", "jp_no", "registration_no", "regis_no",
        "เลขทะเบียนนิติบุคคล", "เลขทะเบียน", "เลขที่นิติบุคคล", "เลขประจำตัวนิติบุคคล",
    ],
    "name_th": ["juristic_name_th", "juristic_name", "name_th", "ชื่อนิติบุคคล", "ชื่อนิติบุคคลไทย"],
    "name_en": ["juristic_name_en", "name_en", "ชื่อนิติบุคคลอังกฤษ", "ชื่อภาษาอังกฤษ"],
    "juristic_type": ["juristic_type", "juristic_type_name", "type", "ประเภทนิติบุคคล", "ประเภท"],
    "status": ["juristic_status", "status", "สถานะนิติบุคคล", "สถานะ"],
    "province": ["province", "province_name", "จังหวัด", "จังหวัดที่ตั้ง"],
    "tsic_code": ["tsic", "tsic_code", "objective_code", "รหัสtsic", "รหัสวัตถุประสงค์", "รหัสประเภทธุรกิจ"],
    "tsic_desc": ["tsic_name", "tsic_desc", "objective_name", "วัตถุประสงค์", "ประเภทธุรกิจ"],
    "registered_capital": ["registered_capital", "capital", "ทุนจดทะเบียน", "ทุน"],
    "registered_date": ["registered_date", "registration_date", "วันที่จดทะเบียน"],
}

PROVINCE_ALIASES = {
    "กรุงเทพ": "กรุงเทพมหานคร",
    "กรุงเทพฯ": "กรุงเทพมหานคร",
    "กทม": "กรุงเทพมหานคร",
    "กทม.": "กรุงเทพมหานคร",
    "bangkok": "กรุงเทพมหานคร",
}


def norm_header(h: str) -> str:
    return re.sub(r"[\s_\-().]", "", h.lower())


def norm_province(s: str) -> str:
    s = re.sub(r"^(จังหวัด|จ\.)\s*", "", s.strip().lower()).replace(" ", "")
    return PROVINCE_ALIASES.get(s, s)


def tsic_digits(s: str) -> str:
    m = re.match(r"\s*(\d+)", s or "")
    return m.group(1) if m else ""


def resolve_columns(headers: list[str], overrides: dict[str, str]) -> dict[str, str]:
    by_norm = {norm_header(h): h for h in headers}
    mapping: dict[str, str] = {}
    for fld, aliases in ALIASES.items():
        if fld in overrides:
            if overrides[fld] not in headers:
                raise ConfigError(
                    f"registry.columns.{fld}={overrides[fld]!r} is not a header in the file. "
                    f"Available: {headers}"
                )
            mapping[fld] = overrides[fld]
            continue
        for alias in aliases:
            hit = by_norm.get(norm_header(alias))
            if hit:
                mapping[fld] = hit
                break
    return mapping


def _row_to_company(row: dict[str, str], mapping: dict[str, str]) -> Company | None:
    def get(fld: str) -> str:
        col = mapping.get(fld)
        return (row.get(col) or "").strip() if col else ""

    jid = get("juristic_id")
    jid = jid.zfill(13) if jid.isdigit() and len(jid) < 13 else jid  # Excel strips leading zeros
    if not re.fullmatch(r"\d{13}", jid):
        return None
    return Company(
        juristic_id=jid,
        name_th=get("name_th"),
        name_en=get("name_en"),
        juristic_type=get("juristic_type"),
        status=get("status"),
        province=get("province"),
        tsic_code=tsic_digits(get("tsic_code")),
        tsic_desc=get("tsic_desc"),
        registered_capital=parse_number(get("registered_capital")),
        registered_date=get("registered_date"),
    )


class Matcher:
    """Applies Scope filters to a Company. Empty filter = pass."""

    def __init__(self, scope: Scope):
        self.s = scope
        self.provinces = {norm_province(p) for p in scope.provinces}
        self.exclude = set(scope.exclude_ids)

    def required_fields(self) -> list[str]:
        s, need = self.s, []
        if s.provinces:
            need.append("province")
        if s.tsic_prefixes:
            need.append("tsic_code")
        if s.juristic_types:
            need.append("juristic_type")
        if s.statuses:
            need.append("status")
        if s.name_contains:
            need.append("name_th")
        if s.capital_min is not None or s.capital_max is not None:
            need.append("registered_capital")
        return need

    def __call__(self, c: Company) -> bool:
        s = self.s
        if c.juristic_id in self.exclude:
            return False
        if self.provinces and norm_province(c.province) not in self.provinces:
            return False
        if s.tsic_prefixes and not any(c.tsic_code.startswith(p) for p in s.tsic_prefixes):
            return False
        if s.juristic_types and not any(t in c.juristic_type for t in s.juristic_types):
            return False
        if s.statuses and not any(t in c.status for t in s.statuses):
            return False
        if s.name_contains:
            name = f"{c.name_th} {c.name_en}".lower()
            if not any(t.lower() in name for t in s.name_contains):
                return False
        cap = c.registered_capital
        if s.capital_min is not None and (cap is None or cap < s.capital_min):
            return False
        if s.capital_max is not None and (cap is None or cap > s.capital_max):
            return False
        return True


def registry_files(cfg: Config) -> list[Path]:
    files: list[Path] = []
    for pattern in cfg.registry.csv_paths:
        files.extend(Path(p) for p in sorted(glob.glob(pattern, recursive=True)))
    return files


def read_headers(path: Path, cfg: Config) -> list[str]:
    with path.open(encoding=cfg.registry.encoding, newline="") as fh:
        return next(csv.reader(fh, delimiter=cfg.registry.delimiter), [])


def iter_companies(cfg: Config, check_scope: bool = True):
    """Yield every parseable Company from all registry files (no scope filtering).

    check_scope=True fails loudly when the scope filters on a column the file does not have.
    """
    files = registry_files(cfg)
    if not files:
        raise ConfigError(
            f"no registry CSV found for {cfg.registry.csv_paths}. Download the DBD open-data "
            "files into data/registry/ (see docs/DATA_SOURCES.md)."
        )
    for path in files:
        with path.open(encoding=cfg.registry.encoding, newline="") as fh:
            reader = csv.DictReader(fh, delimiter=cfg.registry.delimiter)
            headers = list(reader.fieldnames or [])
            mapping = resolve_columns(headers, cfg.registry.columns)
            if "juristic_id" not in mapping:
                raise ConfigError(
                    f"{path}: cannot find the juristic ID column. Headers: {headers}. "
                    "Set registry.columns.juristic_id in the config."
                )
            missing = [f for f in Matcher(cfg.scope).required_fields() if f not in mapping]
            if missing and check_scope:
                raise ConfigError(
                    f"{path}: scope filters on {missing} but those columns were not detected. "
                    f"Headers: {headers}. Map them in registry.columns or relax the scope."
                )
            for row in reader:
                company = _row_to_company(row, mapping)
                if company:
                    yield company


@dataclass
class Universe:
    companies: list[Company]
    total_matched: int
    truncated: bool
    by_province: Counter = field(default_factory=Counter)
    by_tsic2: Counter = field(default_factory=Counter)


def load_all(cfg: Config) -> list[Company]:
    """Every company in the registry files, de-duplicated (later files win). Used by the UI."""
    by_id: dict[str, Company] = {}
    for c in iter_companies(cfg, check_scope=False):
        by_id[c.juristic_id] = c
    return list(by_id.values())


def load_universe(cfg: Config, companies: Iterable[Company] | None = None) -> Universe:
    """Apply the scope. `include_ids` (if set) means exactly those companies, other filters off.

    `companies` lets callers (the UI) pass an already-loaded registry instead of re-reading the CSVs.
    """
    scope = cfg.scope

    def source() -> Iterable[Company]:
        return companies if companies is not None else iter_companies(cfg)

    if scope.include_ids:
        wanted = set(scope.include_ids) - set(scope.exclude_ids)
        found: dict[str, Company] = {}
        try:
            for c in source():
                if c.juristic_id in wanted:
                    found[c.juristic_id] = c
        except ConfigError:
            pass  # registry optional when IDs are listed explicitly
        for jid in wanted:
            found.setdefault(jid, Company(juristic_id=jid))
        matched = dict(found)
    else:
        match = Matcher(scope)
        matched = {}
        for c in source():
            if match(c):
                matched[c.juristic_id] = c  # later files win on duplicates

    ordered = [matched[k] for k in sorted(matched)]
    capped = ordered[: scope.max_companies]
    return Universe(
        companies=capped,
        total_matched=len(ordered),
        truncated=len(ordered) > len(capped),
        by_province=Counter(c.province or "?" for c in ordered),
        by_tsic2=Counter((c.tsic_code[:2] or "?") for c in ordered),
    )


def registry_options(companies: Iterable[Company]) -> dict:
    """Distinct values + counts for the UI's pickers, and which fields the registry really has."""
    prov: Counter = Counter()
    tsic2: Counter = Counter()
    types: Counter = Counter()
    statuses: Counter = Counter()
    present: set[str] = set()
    total = 0
    for c in companies:
        total += 1
        if c.province:
            prov[c.province] += 1
            present.add("province")
        if c.tsic_code:
            tsic2[c.tsic_code[:2]] += 1
            present.add("tsic_code")
        if c.juristic_type:
            types[c.juristic_type] += 1
            present.add("juristic_type")
        if c.status:
            statuses[c.status] += 1
            present.add("status")
        if c.registered_capital is not None:
            present.add("registered_capital")
        if c.name_th or c.name_en:
            present.add("name_th")
    return {
        "total": total,
        "present_fields": sorted(present),
        "provinces": prov.most_common(),
        "tsic2": sorted(tsic2.items()),
        "types": types.most_common(),
        "statuses": statuses.most_common(),
    }
