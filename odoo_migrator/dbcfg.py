"""Strict per-database YAML config loading and validation for odoo_migrator.

Rules (enforced, unlike v1):
- All versions are Odoo major numbers (13..19). Internal shorthand is rejected.
- `databases` must contain an explicit entry for every version the migration
  touches — db_name() NEVER falls back to a generated name.
- hop keys must be `{N}_to_{M}` with M == N + 1, and both N and M in `databases`.
- Unknown keys anywhere are errors (typo protection).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from .config import SUPPORTED_VERSIONS, databases_dir


class ConfigError(Exception):
    """Raised when a database config is invalid or ambiguous."""


ROOT_KEYS = {
    "name", "full_name", "databases", "current_version", "target_version",
    "hops", "enterprise_in_addons_path", "notes",
}
HOP_KEYS = {
    "modules_to_remove", "auto_install_false", "pre_sql", "post_copy_sql",
    "openupgrade_patches", "modules_to_reinstall", "modules_to_replace", "notes",
}
HOP_KEY_RE = re.compile(r"^(\d+)_to_(\d+)$")

_STR_LIST_ERR = "{path}: '{key}' must be a list of strings"


def _check_str_list(value: Any, key: str, path: str) -> None:
    if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() for x in value):
        raise ConfigError(_STR_LIST_ERR.format(path=path, key=key))


class DatabaseConfig:
    def __init__(self, data: dict[str, Any], path: Path):
        self._data = data
        self.path = path
        self.name: str = data["name"]
        self.full_name: str = data.get("full_name", self.name)
        self.databases: dict[int, str] = data["databases"]
        self.current_version: int = data["current_version"]
        self.target_version: int = data["target_version"]
        self.hops: dict[str, dict] = data.get("hops", {})
        self.enterprise_in_addons_path: dict[int, bool] = data.get(
            "enterprise_in_addons_path", {}
        )
        self.notes: str = data.get("notes", "")

    # -- lookups -----------------------------------------------------------

    def db_name(self, version: int) -> str:
        try:
            name = self.databases[version]
        except KeyError:
            raise ConfigError(
                f"{self.path.name}: no database name mapped for Odoo {version}. "
                f"Add `databases: {{{version}: <dbname>}}` — v2 never guesses."
            )
        if not name or not isinstance(name, str):
            raise ConfigError(f"{self.path.name}: invalid database name for {version}: {name!r}")
        return name

    def get_hop(self, from_ver: int, to_ver: int) -> dict:
        return self.hops.get(f"{from_ver}_to_{to_ver}", {})

    def versions_between(self, from_ver: int, to_ver: int) -> list[int]:
        return list(range(from_ver, to_ver))

    def raw(self) -> dict[str, Any]:
        return self._data

    # -- validation -------------------------------------------------------

    def validate(self) -> None:
        p = self.path.name
        problems: list[str] = []

        unknown = set(self._data) - ROOT_KEYS
        if unknown:
            problems.append(f"unknown top-level keys: {sorted(unknown)}")

        if not self.databases or not isinstance(self.databases, dict):
            problems.append("'databases' must be a non-empty {version: dbname} map")
        else:
            for key, name in self.databases.items():
                if not isinstance(key, int) or key not in SUPPORTED_VERSIONS:
                    problems.append(
                        f"databases key {key!r} is not a supported Odoo version "
                        f"(use Odoo majors {SUPPORTED_VERSIONS}; no shorthand)"
                    )
                if not isinstance(name, str) or not name.strip():
                    problems.append(f"databases[{key!r}] must be a non-empty string")

        for attr in ("current_version", "target_version"):
            v = getattr(self, attr)
            if v not in self.databases:
                problems.append(
                    f"{attr} ({v}) must be one of the mapped database versions "
                    f"{sorted(self.databases)}"
                )
        if self.current_version >= self.target_version:
            problems.append(
                f"target_version ({self.target_version}) must be greater than "
                f"current_version ({self.current_version})"
            )

        for key, hop in self.hops.items():
            m = HOP_KEY_RE.match(key)
            if not m:
                problems.append(
                    f"hops key {key!r} must look like '16_to_17' "
                    "(Odoo majors, target = source + 1)"
                )
                continue
            a, b = int(m.group(1)), int(m.group(2))
            if b != a + 1:
                problems.append(f"hops key {key!r}: target must be source + 1")
            for v in (a, b):
                if v not in self.databases:
                    problems.append(f"hops key {key!r}: version {v} is not in `databases`")
            if not isinstance(hop, dict):
                problems.append(f"hops.{key}: must be a mapping")
                continue
            bad = set(hop) - HOP_KEYS
            if bad:
                problems.append(f"hops.{key}: unknown keys {sorted(bad)}")
            for listkey in ("modules_to_remove", "auto_install_false",
                            "modules_to_reinstall", "pre_sql", "post_copy_sql"):
                if listkey in hop:
                    _check_str_list(hop[listkey], listkey, f"hops.{key}")
            if "modules_to_replace" in hop:
                rep = hop["modules_to_replace"]
                if not isinstance(rep, dict) or any(
                    not isinstance(k, str) or not isinstance(v, str) for k, v in rep.items()
                ):
                    problems.append(f"hops.{key}: 'modules_to_replace' must be {old: new} strings")
            if "openupgrade_patches" in hop:
                patches = hop["openupgrade_patches"]
                if not isinstance(patches, list):
                    problems.append(f"hops.{key}: 'openupgrade_patches' must be a list")
                else:
                    for entry in patches:
                        ok = isinstance(entry, dict) and {"src", "dest"} <= set(entry)
                        if not ok and not isinstance(entry, str):
                            problems.append(
                                f"hops.{key}: patch entries must be 'src/dest' dicts or strings"
                            )

        if problems:
            raise ConfigError(f"{p}: invalid config:\n  - " + "\n  - ".join(problems))


def config_path(name: str) -> Path:
    return databases_dir() / f"{name}.yaml"


def list_database_configs() -> list[str]:
    d = databases_dir()
    if not d.exists():
        return []
    return sorted(
        p.stem for p in d.glob("*.yaml") if ".draft" not in p.name
    )


def load_database_config(name: str) -> DatabaseConfig:
    path = config_path(name)
    if not path.exists():
        available = ", ".join(list_database_configs()) or "(none)"
        raise ConfigError(f"No config file {path}. Available: {available}")
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict) or "name" not in data:
        raise ConfigError(f"{path.name}: not a valid odoo_migrator config (missing 'name')")
    if data.get("name") != name:
        raise ConfigError(
            f"{path.name}: config 'name' is {data.get('name')!r} but file is {name}.yaml"
        )
    for required in ("databases", "current_version", "target_version"):
        if required not in data:
            raise ConfigError(f"{path.name}: missing required key '{required}'")
    cfg = DatabaseConfig(data, path)
    cfg.validate()
    return cfg