"""Short-lived odoo-bin subprocess runner for odoo_migrator.

Every Odoo interaction is a subprocess with --no-http (verified present in
Odoo 16/17/18) so nothing ever binds port 8069 and no XML-RPC server is
required. `odoo-bin shell` executes piped stdin in all three versions
(odoo/cli/shell.py: exec(sys.stdin.read(), local_vars)).

Every run is captured to a log file while also streaming to the console.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from . import config

logger = logging.getLogger(__name__)


class OdooRunError(Exception):
    """Raised when an odoo-bin subprocess fails or its prerequisites are missing."""


def _resolve(version: int) -> tuple[Path, Path, Path, Path]:
    venv = config.odoo_venv(version)
    python = config.venv_python(version)
    binp = config.odoo_bin(version)
    conf = config.odoo_conf(version)
    missing = [str(p) for p in (python, binp, conf) if not p.exists()]
    if missing:
        raise OdooRunError(
            f"Odoo {version} prerequisites missing: {missing} "
            f"(check {config.version_dir(version)})"
        )
    return venv, python, binp, conf


def check_version(version: int) -> list[str]:
    """Pre-flight problems for an Odoo version (empty list = healthy)."""
    problems: list[str] = []
    for p, what in (
        (config.odoo_bin(version), "odoo-bin"),
        (config.odoo_conf(version), "odoo.conf"),
        (config.venv_python(version), "venv python"),
    ):
        if not p.exists():
            problems.append(f"Odoo {version}: missing {what}: {p}")

    conf = config.odoo_conf(version)
    python = config.venv_python(version)
    if conf.exists() and "openupgrade_framework" in conf.read_text() and python.exists():
        proc = subprocess.run(
            [str(python), "-c", "import openupgradelib"],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            problems.append(
                f"Odoo {version}: odoo.conf loads openupgrade_framework "
                f"but 'import openupgradelib' fails in {config.odoo_venv(version)} "
                f"— pip install openupgradelib into that venv"
            )
        else:
            # OU 17+ scripts call update_module_names(..., environment_namespec=True);
            # stale/divergent openupgradelib builds lack the kwarg and explode mid-run.
            proc = subprocess.run(
                [str(python), "-c",
                 "import pathlib, sys, openupgradelib; "
                 "p = pathlib.Path(openupgradelib.__file__).parent / 'openupgrade.py'; "
                 "sys.exit(0 if 'environment_namespec' in p.read_text() else 1)"],
                capture_output=True, text=True,
            )
            if proc.returncode != 0:
                problems.append(
                    f"Odoo {version}: openupgradelib too old for OU 17+ scripts "
                    f"(no environment_namespec) — "
                    f"uv pip install --python {python} --upgrade openupgradelib"
                )
    return problems


def run(
    version: int,
    db: str,
    args: list[str],
    log_path: Path | None = None,
    *,
    env_extra: dict[str, str] | None = None,
    input_text: str | None = None,
) -> tuple[int, Path | None]:
    """Run odoo-bin (version's venv) with args; capture to log_path; return (rc, log)."""
    venv, python, binp, conf = _resolve(version)
    # Odoo's CLI dispatches subcommands (shell, scaffold, ...) only from the
    # first argument position — the command token must sit right after
    # odoo-bin, before the global options (odoo-bin shell -d db ...).
    cmd = [str(python), str(binp)]
    if args and args[0] == "shell":
        cmd += ["shell", "-c", str(conf), "-d", db] + args[1:]
    else:
        cmd += ["-c", str(conf), "-d", db] + args
    if "--no-http" not in args:
        cmd += ["--no-http"]

    env = os.environ.copy()
    env["PATH"] = f"{venv / 'bin'}:{env.get('PATH', '')}"
    env["VIRTUAL_ENV"] = str(venv)
    # libpq fallbacks for odoo.conf files with db_host = False (PW.7.0):
    # without these psycopg2 tries the unix socket /tmp/.s.PGSQL.5432,
    # which Homebrew PostgreSQL does not provide.
    env["PGHOST"] = str(config.db_host())
    env["PGPORT"] = str(config.db_port())
    env["PGUSER"] = config.db_user()
    if env_extra:
        env.update(env_extra)

    logger.info("Running Odoo %d: %s", version, " ".join(cmd))
    log_file = None
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(log_path, "a", encoding="utf-8")

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE if input_text is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    if input_text is not None:
        proc.stdin.write(input_text)
        proc.stdin.close()

    try:
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            if log_file:
                log_file.write(line)
    finally:
        proc.wait()
        if log_file:
            log_file.close()

    if proc.returncode != 0:
        logger.error("odoo-bin exited with code %d (log: %s)", proc.returncode, log_path)
    return proc.returncode, log_path


def shell(
    version: int,
    db: str,
    script_text: str,
    log_path: Path | None = None,
    *,
    env_extra: dict[str, str] | None = None,
) -> tuple[int, Path | None]:
    """Run a Python script inside `odoo-bin shell` (ORM access via `env`)."""
    return run(
        version, db,
        ["shell"],  # --no-http appended by run()
        log_path=log_path,
        env_extra=env_extra,
        input_text=script_text,
    )


def update_all(version: int, db: str, log_path: Path | None = None) -> tuple[int, Path | None]:
    """odoo-bin -u all --stop-after-init on the source version."""
    return run(
        version, db,
        ["-u", "all", "--stop-after-init"],
        log_path=log_path,
    )


def openupgrade_run(version: int, db: str, log_path: Path | None = None) -> tuple[int, Path | None]:
    """Run OpenUpgrade on `db`, migrating INTO `version` (the target)."""
    ou_scripts = config.openupgrade_scripts(version)
    if not ou_scripts.exists():
        raise OdooRunError(
            f"OpenUpgrade scripts for target {version} not found: {ou_scripts}"
        )
    return run(
        version, db,
        [
            "-u", "all", "--stop-after-init",
            f"--upgrade-path={ou_scripts}",
            "--load=base,web,openupgrade_framework",
        ],
        log_path=log_path,
    )