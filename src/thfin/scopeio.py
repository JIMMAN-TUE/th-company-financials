"""Read/write the `scope:` section of a config file without touching the other sections."""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

from .config import Config, ConfigError, Scope


def scope_dict(scope: Scope) -> dict:
    return scope.model_dump(mode="python")


def scope_block(scope: Scope) -> str:
    """The YAML text of a `scope:` section (stable key order, Thai kept readable)."""
    body = yaml.safe_dump(scope_dict(scope), allow_unicode=True, sort_keys=False, default_flow_style=None, width=100)
    return "scope:\n" + "".join(f"  {line}\n" if line else "\n" for line in body.rstrip("\n").split("\n"))


_TOP_LEVEL = re.compile(r"^\S")


def replace_scope_block(text: str, scope: Scope) -> str:
    """Return `text` with its top-level `scope:` section replaced (or appended if missing)."""
    block = scope_block(scope)
    lines = text.splitlines(keepends=True)
    start = next((i for i, ln in enumerate(lines) if re.match(r"^scope:", ln)), None)
    if start is None:
        sep = "" if (not text or text.endswith("\n\n")) else ("\n" if text.endswith("\n") else "\n\n")
        return text + sep + block
    end = start + 1
    while end < len(lines) and (not lines[end].strip() or not _TOP_LEVEL.match(lines[end])):
        end += 1
    # keep blank lines that separate sections
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    tail = "".join(lines[end:])
    return "".join(lines[:start]) + block + ("\n" if tail and not tail.startswith("\n") else "") + tail


def write_scope_block(path: str | Path, scope: Scope, base_text: str | None = None) -> Path:
    """Write the scope into `path` (created from `base_text` when missing). Validates before saving."""
    p = Path(path)
    if p.exists():
        text = p.read_text(encoding="utf-8")
    elif base_text is not None:
        text = base_text
    else:
        text = ""
    new_text = replace_scope_block(text, scope)
    try:
        raw = yaml.safe_load(new_text) or {}
        Config.model_validate(raw)
    except Exception as e:
        raise ConfigError(f"refusing to write an invalid config: {e}") from e
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    os.replace(tmp, p)  # atomic: a crash never leaves a half-written config
    return p


def plan_command(config_path: str | Path) -> str:
    return f"python -m thfin plan --config {config_path}"
