"""Path, version-map and environment resolution for odoo_migrator.

All versions are Odoo major versions (13..19). No internal shorthand.
Credentials come from environment variables, never from configs.
"""

from __future__ import annotations

import os
from pathlib import Path

# Odoo major version -> workspace checkout directory
VERSION_DIRS = {
    13: "PW.3.0",
    14: "PW.4.0",
    15: "PW.5.0",
    16: "PW.6.0",
    17: "PW.7.0",
    18: "PW.18.0",
    19: "PW.19.0",
}

# OpenUpgrade checkout that migrates INTO the given version
# (the hop 16->17 runs OpenUpgrade_17.0 scripts on the target database).
OPENUPGRADE_DIRS = {
    14: "OpenUpgrade_14.0",
    15: "OpenUpgrade_15.0",
    16: "OpenUpgrade_16.0",
    17: "OpenUpgrade_17.0",
    18: "OpenUpgrade_18.0",
    19: "OpenUpgrade_19.0",
}

# Odoo versions that can be migrated INTO with OpenUpgrade (native -u all otherwise).
OPENUPGRADE_TARGETS = sorted(OPENUPGRADE_DIRS)

SUPPORTED_VERSIONS = sorted(VERSION_DIRS)

# Top-level enterprise checkout per version (e.g. 16.E), if present on disk.
ENTERPRISE_DIRS = {v: f"{v}.E" for v in (13, 14, 15, 16, 17, 18, 19)}


class ConfigError(Exception):
    """Raised for any path/version resolution problem."""


def pw_root() -> Path:
    return Path(os.environ.get("PW_ROOT", "/opt/PW"))


def version_dir(version: int) -> Path:
    try:
        return pw_root() / VERSION_DIRS[version]
    except KeyError:
        raise ConfigError(
            f"Unsupported Odoo version: {version}. Supported: {SUPPORTED_VERSIONS}"
        )


def odoo_bin(version: int) -> Path:
    return version_dir(version) / "odoo-bin"


def odoo_conf(version: int) -> Path:
    return version_dir(version) / "odoo.conf"


def odoo_venv(version: int) -> Path:
    return version_dir(version) / ".venv"


def venv_python(version: int) -> Path:
    return odoo_venv(version) / "bin" / "python"


def openupgrade_scripts(version: int) -> Path:
    try:
        return pw_root() / OPENUPGRADE_DIRS[version] / "openupgrade_scripts" / "scripts"
    except KeyError:
        raise ConfigError(
            f"No OpenUpgrade checkout migrates into version {version}. "
            f"Available targets: {OPENUPGRADE_TARGETS}"
        )


def enterprise_dir(version: int) -> Path:
    return pw_root() / ENTERPRISE_DIRS[version]


def data_dir() -> Path:
    return Path(os.environ.get("PW_DATA_DIR", "/opt/PW/data"))


def filestore_dir(db_name: str) -> Path:
    return data_dir() / "filestore" / db_name


def backups_dir() -> Path:
    d = data_dir() / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def logs_dir() -> Path:
    d = data_dir() / "migration_logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_logs_dir(db_name: str) -> Path:
    d = logs_dir() / db_name
    d.mkdir(parents=True, exist_ok=True)
    return d


def package_dir() -> Path:
    """Directory holding databases/ configs, shell scripts and states."""
    return Path(__file__).resolve().parent.parent


def databases_dir() -> Path:
    override = os.environ.get("PW_MIGRATOR_CONFIG_DIR")
    return Path(override) if override else package_dir() / "databases"


def shell_scripts_dir() -> Path:
    return package_dir() / "shell"


def pg_bin_dir() -> Path:
    """PostgreSQL client binaries — matches pg_path in the odoo.conf files."""
    return Path(os.environ.get("PW_PG_BIN", "/opt/homebrew/opt/postgresql@18/bin"))


def pg_bin(name: str) -> Path:
    p = pg_bin_dir() / name
    if not p.exists():
        raise ConfigError(
            f"PostgreSQL binary not found: {p} (set PW_PG_BIN to your pg_path)"
        )
    return p


def db_user() -> str:
    return os.environ.get("PW_DB_USER", "wsloh")


def db_password() -> str | None:
    return os.environ.get("PW_DB_PASSWORD") or None


def db_host() -> str:
    return os.environ.get("PW_DB_HOST", "127.0.0.1")


def db_port() -> int:
    return int(os.environ.get("PW_DB_PORT", "5432"))