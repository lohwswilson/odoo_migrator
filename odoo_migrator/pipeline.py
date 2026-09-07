"""Hop orchestration for odoo_migrator.

One hop (N -> N+1), aligned with OCA's documented OpenUpgrade process:

  pre-flight -> snapshot(SQL) -> backup -> uninstall(shell) -> auto_install(SQL)
  -> pre-SQL -> -u all on source -> copy DB -> copy filestore -> post-copy-SQL
  -> apply patches -> OpenUpgrade run -> restore patches -> log gate
  -> reinstall/replace(shell) -> verify -> persist state

Every odoo-bin subprocess appends to a single per-hop log file.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import analyze, config, logparse, odoo, pg, state as state_mod, verify
from .dbcfg import DatabaseConfig

logger = logging.getLogger(__name__)


class PipelineError(Exception):
    pass


@dataclass
class HopResult:
    from_ver: int
    to_ver: int
    ok: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    log_path: Path | None = None
    log_summary: dict = field(default_factory=dict)
    verify_summary: dict = field(default_factory=dict)

    def error(self, msg: str) -> None:
        self.errors.append(msg)
        logger.error(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)
        logger.warning(msg)


def _load_shell_script(name: str) -> str:
    path = config.shell_scripts_dir() / name
    if not path.exists():
        raise PipelineError(f"Shell script missing: {path}")
    return path.read_text()


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def pre_flight(cfg: DatabaseConfig, from_ver: int, to_ver: int) -> list[str]:
    """Hard problems that must be fixed before a hop (empty = ready)."""
    problems: list[str] = []
    source_db = cfg.db_name(from_ver)
    target_db = cfg.db_name(to_ver)

    if not pg.reachable():
        problems.append(
            f"PostgreSQL not reachable at {config.db_host()}:{config.db_port()} "
            f"(user {config.db_user()}, pg bin {config.pg_bin_dir()})"
        )
        return problems

    if not pg.database_exists(source_db):
        problems.append(f"Source database does not exist: {source_db}")
    if pg.database_exists(target_db):
        problems.append(f"Target database already exists: {target_db}")

    problems += odoo.check_version(from_ver)
    problems += odoo.check_version(to_ver)

    ou = config.openupgrade_scripts(to_ver)
    if not ou.exists():
        problems.append(f"OpenUpgrade scripts for target {to_ver} not found: {ou}")

    for script in ("uninstall.py", "install.py"):
        if not (config.shell_scripts_dir() / script).exists():
            problems.append(f"Shell script missing: {config.shell_scripts_dir() / script}")

    fs = config.filestore_dir(source_db)
    if not fs.exists():
        problems.append(
            f"Filestore missing for {source_db}: {fs} "
            "(restore it with `odoo-migrator restore` or copy manually)"
        )
    return problems


def run_hop(
    cfg: DatabaseConfig,
    from_ver: int,
    to_ver: int,
    *,
    dry_run: bool = False,
    skip_backup: bool = False,
    skip_update: bool = False,
    skip_filestore: bool = False,
    log_gate: bool = True,
) -> HopResult:
    result = HopResult(from_ver=from_ver, to_ver=to_ver)
    hop = cfg.get_hop(from_ver, to_ver)
    source_db = cfg.db_name(from_ver)
    target_db = cfg.db_name(to_ver)
    log_path = config.db_logs_dir(cfg.name) / f"{_stamp()}_{from_ver}_to_{to_ver}.log"

    logger.info("=" * 64)
    logger.info("HOP %s: Odoo %d -> %d (%s -> %s)", cfg.name, from_ver, to_ver, source_db, target_db)
    logger.info("Log: %s", log_path)

    problems = pre_flight(cfg, from_ver, to_ver)
    for p in problems:
        result.error(f"pre-flight: {p}")
    if problems:
        return result

    if dry_run:
        logger.info("DRY RUN — no changes will be made")
        logger.info("modules_to_remove: %s", hop.get("modules_to_remove", []))
        logger.info("modules_to_reinstall: %s", hop.get("modules_to_reinstall", []))
        logger.info("modules_to_replace: %s", hop.get("modules_to_replace", {}))
        logger.info("pre_sql: %d, post_copy_sql: %d, patches: %d",
                    len(hop.get("pre_sql", [])),
                    len(hop.get("post_copy_sql", [])),
                    len(hop.get("openupgrade_patches", [])))
        if hop.get("notes"):
            logger.info("notes:\n%s", hop["notes"].strip())
        result.ok = True
        return result

    # -- 1. Snapshot (SQL only) --------------------------------------------
    installed = pg.installed_modules(source_db)
    snapshot = {
        "modules": [m["name"] for m in installed],
        "tables": pg.table_counts(source_db),
        "model_modules": pg.model_modules(source_db),
    }
    state_mod.save_snapshot(cfg.name, from_ver, snapshot)
    logger.info("Snapshot: %d modules, %d tables",
                len(snapshot["modules"]), len(snapshot["tables"]))

    # -- 2. Backup ----------------------------------------------------------
    if not skip_backup:
        try:
            path = pg.backup_database(source_db)
            logger.info("Backup: %s", path)
        except pg.PGError as e:
            result.error(f"backup failed: {e}")
            return result

    # -- 3. Uninstall modules (odoo shell on source) ------------------------
    to_remove = hop.get("modules_to_remove", [])
    if to_remove:
        rc, _ = odoo.shell(
            from_ver, source_db,
            _load_shell_script("uninstall.py"),
            log_path=log_path,
            env_extra={"PW_MIGRATOR_MODULES": ",".join(to_remove)},
        )
        if rc != 0:
            result.error(f"module uninstall failed (exit {rc}) — see {log_path}")
            return result

    # -- 4. auto_install=False (plain SQL) ------------------------------------
    for name in hop.get("auto_install_false", []):
        pg.execute_sql(
            source_db,
            "UPDATE ir_module_module SET auto_install = false "
            f"WHERE name = '{name.replace(chr(39), chr(39) * 2)}';",
        )
        logger.info("auto_install=false: %s", name)

    # -- 5. Pre-SQL -----------------------------------------------------------
    for sql in hop.get("pre_sql", []):
        pg.execute_sql(source_db, sql)
        logger.info("pre_sql: %s", sql[:120])

    # -- 6. Full update on source --------------------------------------------
    if not skip_update:
        rc, _ = odoo.update_all(from_ver, source_db, log_path=log_path)
        if rc != 0:
            result.error(f"-u all on source failed (exit {rc}) — see {log_path}")
            return result

    # -- 7. Copy database + filestore ------------------------------------------
    try:
        pg.copy_database(source_db, target_db)
    except pg.PGError as e:
        result.error(f"database copy failed: {e}")
        return result

    if not skip_filestore:
        src_fs = config.filestore_dir(source_db)
        dst_fs = config.filestore_dir(target_db)
        if src_fs.exists():
            if dst_fs.exists():
                result.error(f"filestore destination already exists: {dst_fs}")
                return result
            shutil.copytree(src_fs, dst_fs)
            logger.info("Filestore copied: %s -> %s", src_fs, dst_fs)

    # -- 8. Post-copy SQL ------------------------------------------------------
    for sql in hop.get("post_copy_sql", []):
        pg.execute_sql(target_db, sql)
        logger.info("post_copy_sql: %s", sql[:120])

    # -- 9. OpenUpgrade patches -------------------------------------------------
    patch_backups: list[tuple[Path, Path]] = []
    for entry in hop.get("openupgrade_patches", []):
        if isinstance(entry, dict):
            src_rel, dst_rel = entry["src"], entry["dest"]
        else:
            src_rel, dst_rel = entry, entry.replace("__", "/")
        src = Path(src_rel)
        if not src.is_absolute():
            src = config.package_dir() / "patches" / src_rel
        dst = config.openupgrade_scripts(to_ver) / dst_rel
        if not src.exists():
            result.error(f"patch source not found: {src}")
            return result
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            bak = Path(str(dst) + ".pw2.bak")
            shutil.copy2(dst, bak)
            patch_backups.append((bak, dst))
        shutil.copy2(src, dst)
        logger.info("patch applied: %s -> %s", src, dst)

    # -- 10. OpenUpgrade run ------------------------------------------------------
    try:
        rc, _ = odoo.openupgrade_run(to_ver, target_db, log_path=log_path)
    except odoo.OdooRunError as e:
        result.error(str(e))
        return result
    finally:
        _restore_patches(patch_backups)

    if rc != 0:
        result.error(f"OpenUpgrade failed (exit {rc}) — rollback: drop {target_db}, "
                     f"pg_restore -d {source_db} <backup>")

    # -- 11. Log gate ---------------------------------------------------------------
    report = logparse.parse_log(log_path, exit_code=rc)
    result.log_summary = report.summary()
    for line in report.criticals[:20]:
        (result.errors if log_gate else result.warnings).append(f"CRITICAL: {line}")
    for line in report.errors[:20]:
        (result.errors if log_gate else result.warnings).append(f"ERROR: {line}")
    for line in report.warnings[:20]:
        result.warnings.append(f"log: {line}")

    # -- 12. Reinstall / replace (odoo shell on target) ---------------------------------
    reinstall = hop.get("modules_to_reinstall", [])
    replace = hop.get("modules_to_replace", {})
    if reinstall or replace:
        rc2, _ = odoo.shell(
            to_ver, target_db,
            _load_shell_script("install.py"),
            log_path=log_path,
            env_extra={
                "PW_MIGRATOR_INSTALL": ",".join(reinstall),
                "PW_MIGRATOR_REPLACE_OLD": ",".join(replace.keys()),
                "PW_MIGRATOR_REPLACE_NEW": ",".join(replace.values()),
            },
        )
        if rc2 != 0:
            result.warn(f"reinstall/replace exited {rc2} — see {log_path}")

    # -- 13. Verify --------------------------------------------------------------------
    try:
        v = verify.verify_hop(target_db, snapshot, expected_removed=to_remove)
        result.verify_summary = v.summary()
        result.errors.extend(v.errors)
        result.warnings.extend(v.warnings)
        result.warnings.extend(v.info[:10])
    except pg.PGError as e:
        result.warn(f"verification could not run: {e}")

    # -- 14. Persist state -----------------------------------------------------------------
    result.ok = not result.errors
    state_mod.record_hop(
        cfg.name, from_ver, to_ver,
        ok=result.ok, log=str(log_path),
        verify_summary={**result.log_summary, **result.verify_summary},
    )
    logger.info("HOP %s %d->%d %s", cfg.name, from_ver, to_ver,
                "COMPLETE" if result.ok else "FAILED")
    return result


def _restore_patches(patch_backups: list[tuple[Path, Path]]) -> None:
    for bak, dst in patch_backups:
        shutil.move(str(bak), str(dst))
        logger.info("patch restored: %s", dst)


def run_chain(
    cfg: DatabaseConfig,
    to_ver: int,
    *,
    auto: bool = False,
    dry_run: bool = False,
    **hop_flags,
) -> list[HopResult]:
    """Run hops from the persisted current version up to to_ver.

    Stops after every completed hop for manual verification unless auto=True.
    """
    current = state_mod.current_version(cfg.name)
    if current is None:
        current = cfg.current_version
    elif current != cfg.current_version:
        logger.warning(
            "State file says current=%d but %s says current_version=%d — "
            "trusting the state file", current, cfg.path.name, cfg.current_version
        )
    if current >= to_ver:
        logger.info("Already at version %d — nothing to do", current)
        return []

    results: list[HopResult] = []
    for v in range(current, to_ver):
        res = run_hop(cfg, v, v + 1, dry_run=dry_run, **hop_flags)
        results.append(res)
        if not res.ok:
            logger.error("Chain stopped at hop %d->%d", v, v + 1)
            break
        if v + 1 < to_ver and not auto:
            logger.info(
                "Hop %d->%d complete and verified. Run `odoo-migrator verify %s "
                "--version %d`, then re-run this command (or use --auto) to continue.",
                v, v + 1, cfg.name, v + 1,
            )
            break
    return results