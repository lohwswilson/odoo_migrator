"""PostgreSQL operations for odoo_migrator.

All binaries come from PW_PG_BIN (default /opt/homebrew/opt/postgresql@18/bin,
matching pg_path in the odoo.conf files) — never bare PATH names.
Connection failures raise PGError instead of masquerading as
"database does not exist" (a v1 flaw).
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from . import config

logger = logging.getLogger(__name__)


class PGError(Exception):
    """Raised when a PostgreSQL operation fails."""


def _psql_env() -> dict[str, str]:
    env = os.environ.copy()
    pw = config.db_password()
    if pw:
        env["PGPASSWORD"] = pw
    return env


def _base_args() -> list[str]:
    return [
        "-h", config.db_host(),
        "-p", str(config.db_port()),
        "-U", config.db_user(),
    ]


def _run(cmd: list[str], *, input_text: str | None = None,
         check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        cmd, input=input_text, capture_output=True, text=True, env=_psql_env()
    )
    if check and proc.returncode != 0:
        raise PGError(
            f"Command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr.strip()[:2000]}"
        )
    return proc


def _psql(args: list[str] | None = None, *, db: str | None = None, sql: str | None = None,
          input_text: str | None = None, check: bool = True
          ) -> subprocess.CompletedProcess:
    cmd = [str(config.pg_bin("psql"))] + _base_args() + list(args or [])
    if db:
        cmd += ["-d", db]
    if sql:
        cmd += ["-c", sql]
    return _run(cmd, input_text=input_text, check=check)


def reachable() -> bool:
    try:
        _psql(["-lqt"])
        return True
    except (PGError, config.ConfigError):
        return False


def list_databases() -> list[str]:
    proc = _psql(["-lqt"])
    names = []
    for line in proc.stdout.splitlines():
        name = line.split("|")[0].strip()
        if name:
            names.append(name)
    return names


def database_exists(db_name: str) -> bool:
    return db_name in list_databases()


def database_size(db_name: str) -> str:
    proc = _psql(
        ["-t", "-A"],
        db=db_name,
        sql=f"SELECT pg_size_pretty(pg_database_size('{db_name}'));",
    )
    return proc.stdout.strip()


def terminate_connections(db_name: str) -> None:
    _psql(
        db="postgres",
        sql=(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = '{db_name}' AND pid <> pg_backend_pid();"
        ),
    )


def copy_database(source: str, target: str) -> None:
    if database_exists(target):
        raise PGError(f"Target database already exists: {target}")
    terminate_connections(source)
    cmd = (
        [str(config.pg_bin("createdb"))] + _base_args() + ["-T", source, target]
    )
    _run(cmd)
    logger.info("Copied database %s -> %s", source, target)


def drop_database(db_name: str) -> None:
    cmd = [str(config.pg_bin("dropdb"))] + _base_args() + ["--if-exists", db_name]
    _run(cmd)
    logger.info("Dropped database %s", db_name)


def backup_database(db_name: str) -> Path:
    out_dir = config.backups_dir()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = out_dir / f"{db_name}_{stamp}.dump"
    cmd = (
        [str(config.pg_bin("pg_dump"))] + _base_args()
        + ["-F", "c", "-b", "-f", str(out_file), db_name]
    )
    _run(cmd)
    logger.info("Backup: %s (%.1f MB)", out_file, out_file.stat().st_size / 1e6)
    return out_file


def execute_sql(db_name: str, sql: str) -> str:
    proc = _psql(db=db_name, sql=sql)
    return proc.stdout


# ---------------------------------------------------------------------------
# Read-only state queries (no Odoo required)
# ---------------------------------------------------------------------------

def installed_modules(db_name: str) -> list[dict[str, str]]:
    """[{name, state, latest_version}] for all non-uninstalled modules."""
    proc = _psql(
        ["-A", "-t", "-F", "\t"],
        db=db_name,
        sql=(
            "SELECT name, state, COALESCE(latest_version, '') "
            "FROM ir_module_module "
            "WHERE state IN ('installed', 'to install', 'to upgrade', 'to remove') "
            "ORDER BY name;"
        ),
    )
    rows: list[dict[str, str]] = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0]:
            rows.append(
                {"name": parts[0], "state": parts[1], "latest_version": parts[2]}
            )
    return rows


def table_counts(db_name: str) -> dict[str, int]:
    """Exact row counts for every public table, in a single psql round trip."""
    proc = _psql(
        ["-A", "-t", "-F", "\t"],
        db=db_name,
        sql="SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1;",
    )
    tables = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if not tables:
        return {}
    unions = " UNION ALL ".join(
        f'SELECT {i + 1} AS n, \'{_esc(t)}\' AS tbl, '
        f'(SELECT count(*)::bigint FROM public."{_esc(t)}") AS cnt'
        for i, t in enumerate(tables)
    )
    proc = _psql(
        ["-A", "-t", "-F", "\t"],
        db=db_name,
        sql=f"SELECT tbl, cnt FROM ({unions}) q ORDER BY tbl;",
    )
    counts: dict[str, int] = {}
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 2 and parts[0]:
            counts[parts[0]] = int(parts[1])
    return counts


def _esc(ident: str) -> str:
    return ident.replace("'", "''")


def model_modules(db_name: str) -> dict[str, str]:
    """Map table name -> comma-separated owning modules.

    ir_model.modules ("In Apps") is a NON-STORED compute in Odoo 16/17/18
    (no DB column exists), so ownership is derived from ir_model_data —
    the same source the compute aggregates, restricted to installed
    modules: the defining module (xmlid "model_<table>" on ir.model) plus
    modules shipping data records for the model.
    """
    proc = _psql(
        ["-A", "-t", "-F", "\t"],
        db=db_name,
        sql=(
            "WITH inst AS (SELECT name FROM ir_module_module WHERE state = 'installed'),"
            "pairs AS ("
            "SELECT substring(d.name FROM 7) AS table_name, d.module "
            "FROM ir_model_data d JOIN inst ON inst.name = d.module "
            "WHERE d.model = 'ir.model' AND d.name LIKE 'model%' "
            "UNION "
            "SELECT replace(d.model, '.', '_'), d.module "
            "FROM ir_model_data d JOIN inst ON inst.name = d.module "
            "WHERE d.model IS NOT NULL AND d.model <> '') "
            "SELECT table_name, COALESCE(string_agg(DISTINCT module, ',' "
            "ORDER BY module), '') FROM pairs GROUP BY table_name;"
        ),
    )
    out: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 2 and parts[0]:
            out[parts[0]] = parts[1]
    return out


# ---------------------------------------------------------------------------
# Odoo zip backup restore (dump.sql + filestore/ — the format the VPS backups use)
# ---------------------------------------------------------------------------

def restore_from_zip(db_name: str, zip_path: Path, *, restore_filestore: bool = True) -> dict:
    zip_path = Path(zip_path)
    if not zip_path.exists():
        raise PGError(f"Backup zip not found: {zip_path}")
    if database_exists(db_name):
        raise PGError(
            f"Database already exists: {db_name} (drop it first if intentional)"
        )
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        if "dump.sql" not in names:
            raise PGError(
                f"{zip_path.name}: not an Odoo backup zip (no dump.sql). "
                f"First entries: {names[:5]}"
            )
        dump_sql = zf.read("dump.sql").decode("utf-8", errors="replace")
        filestore_members = [n for n in names if n.startswith("filestore/")]

    _run([str(config.pg_bin("createdb"))] + _base_args() + [db_name])

    # Plain Odoo dumps may contain harmless ownership/role statements for the
    # source server's roles; do NOT use ON_ERROR_STOP. Quality is gated by the
    # post-restore checks below.
    proc = _psql(db=db_name, input_text=dump_sql, check=False)
    error_lines = [
        l for l in proc.stderr.splitlines()
        if l.strip().startswith("ERROR")
    ]
    if proc.returncode not in (0, 1):
        raise PGError(
            f"Restore of {zip_path.name} failed (psql rc={proc.returncode}): "
            f"{proc.stderr.strip()[:2000]}"
        )

    installed = installed_modules(db_name)
    base_version = _psql(
        ["-t", "-A"],
        db=db_name,
        sql="SELECT latest_version FROM ir_module_module WHERE name = 'base';",
    ).stdout.strip()

    if not installed or not base_version:
        raise PGError(
            f"Restore of {zip_path.name} produced no module data — dump is unusable "
            f"({len(error_lines)} error lines, first: {error_lines[:3]})"
        )

    filestore_files = 0
    if restore_filestore and filestore_members:
        dest = config.filestore_dir(db_name)
        if dest.exists():
            raise PGError(
                f"Filestore destination already exists: {dest} (move it away first)"
            )
        with tempfile.TemporaryDirectory() as tmp:
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(tmp, members=filestore_members)
            staging = Path(tmp) / "filestore"
            dest.parent.mkdir(parents=True, exist_ok=True)
            staging.replace(dest)
        filestore_files = len(filestore_members)

    return {
        "database": db_name,
        "dump_error_lines": len(error_lines),
        "dump_error_samples": error_lines[:5],
        "installed_modules": len(installed),
        "base_version": base_version,
        "filestore_files": filestore_files,
    }


def package_zip(db_name: str, out_path: Path | None = None,
                *, include_filestore: bool = True) -> Path:
    """Build an Odoo-format backup zip (dump.sql + filestore/) for deployment.

    Mirrors what `odoo.service.db.dump_db` and auto_database_backup produce —
    the exact format `restore_from_zip` and the Odoo database manager consume.
    dump.sql is plain-format pg_dump (restorable via psql, like the VPS
    backups); filestore/ members mirror the local filestore tree.
    """
    if not database_exists(db_name):
        raise PGError(f"Database does not exist: {db_name}")
    if out_path is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = config.backups_dir() / f"{db_name}_deploy_{stamp}.zip"
    out_path = Path(out_path)
    if out_path.exists():
        raise PGError(f"Output already exists: {out_path}")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fs = config.filestore_dir(db_name)
    fs_members: list[Path] = []
    if include_filestore and fs.exists():
        fs_members = [f for f in sorted(fs.rglob("*")) if f.is_file()]

    with tempfile.TemporaryDirectory() as tmp:
        dump_sql = Path(tmp) / "dump.sql"
        # --no-owner/--no-privileges: objects become owned by whoever restores
        # the dump — portable across hosts where the source role doesn't exist
        # (avoids ALTER OWNER errors during the VPS restore).
        _run(
            [str(config.pg_bin("pg_dump"))] + _base_args()
            + ["--no-owner", "--no-privileges", "-f", str(dump_sql), db_name]
        )
        with zipfile.ZipFile(
            out_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True
        ) as zf:
            zf.write(dump_sql, "dump.sql")
            for f in fs_members:
                zf.write(f, Path("filestore") / f.relative_to(fs))

    logger.info(
        "Package: %s (%.1f MB, %d filestore files)",
        out_path, out_path.stat().st_size / 1e6, len(fs_members),
    )
    return out_path