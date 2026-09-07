"""odoo_migrator CLI."""

from __future__ import annotations

import logging
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__, config, dbcfg, logparse, odoo, pg, pipeline, state as state_mod
from . import analyze as analyze_mod

app = typer.Typer(
    name="odoo-migrator",
    help="OpenUpgrade migration orchestrator v2 (no XML-RPC, shell-driven, gated).",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
console = Console()


def _setup(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )


def _load(name: str) -> dbcfg.DatabaseConfig:
    try:
        return dbcfg.load_database_config(name)
    except (dbcfg.ConfigError, config.ConfigError) as e:
        console.print(f"[red]Config error:[/red] {e}")
        raise typer.Exit(1)


def _pg_or_die():
    if not pg.reachable():
        console.print(
            f"[red]PostgreSQL not reachable at {config.db_host()}:{config.db_port()}. "
            f"Start it (brew services start postgresql@18) and retry.[/red]"
        )
        raise typer.Exit(1)


@app.command()
def version():
    """Show odoo_migrator version."""
    console.print(f"odoo_migrator {__version__}")


@app.command()
def doctor(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Check PostgreSQL, per-version odoo prerequisites and OpenUpgrade scripts."""
    _setup(verbose)
    problems: list[str] = []

    if pg.reachable():
        console.print(f"[green]PostgreSQL[/green] reachable at "
                      f"{config.db_host()}:{config.db_port()} (user {config.db_user()}, "
                      f"bin {config.pg_bin_dir()})")
    else:
        problems.append(f"PostgreSQL NOT reachable at {config.db_host()}:{config.db_port()}")

    table = Table(title="Odoo versions")
    for col in ("Version", "odoo-bin", "odoo.conf", "venv", "openupgradelib", "OpenUpgrade scripts"):
        table.add_column(col)
    for v in (13, 14, 15, 16, 17, 18, 19):
        if not config.version_dir(v).exists():
            continue
        probs = odoo.check_version(v)
        if v in config.OPENUPGRADE_DIRS:
            ou = config.openupgrade_scripts(v)
            ou_ok = "OK" if ou.exists() else "MISSING"
        else:
            ou_ok = "—"
        oulib = next((p for p in probs if "openupgradelib" in p), "OK")
        table.add_row(
            str(v),
            "OK" if config.odoo_bin(v).exists() else "MISSING",
            "OK" if config.odoo_conf(v).exists() else "MISSING",
            "OK" if config.venv_python(v).exists() else "MISSING",
            ("[red]" + oulib.split("—")[0].strip() + "[/red]") if oulib != "OK" else "OK",
            ou_ok,
        )
        problems.extend(probs)
    console.print(table)

    for p in problems:
        console.print(f"[red]PROBLEM:[/red] {p}")
    if not problems:
        console.print("[bold green]All checks passed.[/bold green]")


@app.command()
def status(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Show migration status of all configured databases (truth: PG + state files)."""
    _setup(verbose)
    names = dbcfg.list_database_configs()
    if not names:
        console.print("[yellow]No configs in %s[/yellow]" % dbcfg.databases_dir())
        return
    up = pg.reachable()

    table = Table(title="Migration status")
    for col in ("DB", "Configured", "State", "Version", "DB exists", "Size", "Last hop"):
        table.add_column(col)
    for name in names:
        try:
            cfg = dbcfg.load_database_config(name)
        except dbcfg.ConfigError as e:
            table.add_row(name, "[red]INVALID[/red]", "", "", "", str(e)[:60], "")
            continue
        st = state_mod.load_state(name)
        ver = st.get("current_version", cfg.current_version)
        mismatch = "" if st.get("current_version", cfg.current_version) == cfg.current_version else " [yellow](yaml stale)[/yellow]"
        db = cfg.db_name(ver)
        exists = pg.database_exists(db) if up else False
        size = pg.database_size(db) if exists else "—"
        last = st.get("history") or []
        last_hop = last[-1]["hop"] if last else "—"
        table.add_row(
            name, f"{cfg.current_version}->{cfg.target_version}",
            "OK" if st else "none", str(ver) + mismatch,
            ("[green]yes[/green]" if exists else "no"), size, last_hop,
        )
    console.print(table)


@app.command()
def restore(
    name: str = typer.Argument(..., help="Config name (e.g. MYDB)"),
    zip_path: Path = typer.Argument(..., help="Odoo backup zip (dump.sql + filestore/)"),
    version: int = typer.Option(None, "--version", help="Version the DB name maps to (default: current)"),
    no_filestore: bool = typer.Option(False, "--no-filestore"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Restore an Odoo zip backup into the local PostgreSQL + filestore."""
    _setup(verbose)
    cfg = _load(name)
    ver = version or cfg.current_version
    db = cfg.db_name(ver)
    _pg_or_die()
    try:
        summary = pg.restore_from_zip(db, zip_path, restore_filestore=not no_filestore)
    except (pg.PGError, config.ConfigError) as e:
        console.print(f"[red]Restore failed:[/red] {e}")
        raise typer.Exit(1)
    console.print(f"[green]Restored into {db}:[/green]")
    console.print(f"  modules installed : {summary['installed_modules']}")
    console.print(f"  base version      : {summary['base_version']}")
    console.print(f"  dump error lines  : {summary['dump_error_lines']} {summary['dump_error_samples'][:3]}")
    console.print(f"  filestore files   : {summary['filestore_files']}")


@app.command()
def analyze(
    name: str = typer.Argument(..., help="Config name (e.g. MYDB)"),
    from_ver: int = typer.Option(None, "--from", help="Source version (default: current)"),
    to_ver: int = typer.Option(None, "--to", help="Target version (default: target)"),
    save_draft: bool = typer.Option(False, "--save-draft", help="Write draft hop config file"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Compare installed modules against target addon trees; draft modules_to_remove."""
    _setup(verbose)
    cfg = _load(name)
    _pg_or_die()
    report = analyze_mod.analyze(cfg, from_ver, to_ver)

    table = Table(title=f"{report.db}: installed modules vs target trees ({report.from_ver}->{report.to_ver})")
    for col in ("Module", "v" + str(report.from_ver), *[f"v{v}" for v in range(report.from_ver + 1, report.to_ver + 1)],
                "Verdict"):
        table.add_column(col)
    for r in sorted(report.rows, key=lambda x: (x.verdict != "OK", x.name)):
        table.add_row(
            r.name, r.origin,
            *[r.availability.get(v, "?") for v in range(report.from_ver + 1, report.to_ver + 1)],
            r.verdict if r.verdict != "OK" else "[green]OK[/green]",
        )
    console.print(table)

    console.print("\n[bold]Draft hop config:[/bold]")
    draft = report.draft_yaml()
    console.print(draft)
    if save_draft:
        out = dbcfg.databases_dir() / f"{cfg.name}.draft.yaml"
        out.write_text(draft)
        console.print(f"[green]Draft written:[/green] {out}")


@app.command()
def hop(
    name: str = typer.Argument(...),
    from_ver: int = typer.Option(..., "--from", help="Source Odoo version"),
    to_ver: int = typer.Option(..., "--to", help="Target Odoo version (from+1)"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    skip_backup: bool = typer.Option(False, "--skip-backup"),
    skip_update: bool = typer.Option(False, "--skip-update", help="Skip -u all on source"),
    skip_filestore: bool = typer.Option(False, "--skip-filestore"),
    no_log_gate: bool = typer.Option(False, "--no-log-gate", help="Log errors downgrade to warnings"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Run one migration hop (N -> N+1)."""
    _setup(verbose)
    cfg = _load(name)
    if to_ver != from_ver + 1:
        console.print("[red]OpenUpgrade migrates one version at a time: --to must be --from + 1[/red]")
        raise typer.Exit(1)
    res = pipeline.run_hop(
        cfg, from_ver, to_ver,
        dry_run=dry_run, skip_backup=skip_backup, skip_update=skip_update,
        skip_filestore=skip_filestore, log_gate=not no_log_gate,
    )
    _print_result(res, f"hop {from_ver}->{to_ver}")
    if not res.ok:
        raise typer.Exit(1)


@app.command()
def chain(
    name: str = typer.Argument(...),
    to_ver: int = typer.Option(None, "--to", help="Target version (default: config target)"),
    auto: bool = typer.Option(False, "--auto", help="Continue past verify gates automatically"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    skip_backup: bool = typer.Option(False, "--skip-backup"),
    skip_update: bool = typer.Option(False, "--skip-update"),
    skip_filestore: bool = typer.Option(False, "--skip-filestore"),
    no_log_gate: bool = typer.Option(False, "--no-log-gate"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Run hops with verify gates (stops after each hop unless --auto)."""
    _setup(verbose)
    cfg = _load(name)
    target = to_ver or cfg.target_version
    results = pipeline.run_chain(
        cfg, target,
        auto=auto, dry_run=dry_run, skip_backup=skip_backup,
        skip_update=skip_update, skip_filestore=skip_filestore,
        log_gate=not no_log_gate,
    )
    for res in results:
        _print_result(res, f"hop {res.from_ver}->{res.to_ver}")
    if any(not r.ok for r in results):
        raise typer.Exit(1)


@app.command()
def verify(
    name: str = typer.Argument(...),
    version: int = typer.Option(None, "--version", help="Version to verify (default: current)"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Verify a database against its pre-hop snapshot."""
    _setup(verbose)
    cfg = _load(name)
    _pg_or_die()
    ver = version or (state_mod.current_version(cfg.name) or cfg.current_version)
    db = cfg.db_name(ver)
    from . import verify as verify_mod
    res = verify_mod.verify_version(db, ver, name=cfg.name)
    console.print(f"[bold]Verify {db} (v{ver})[/bold]")
    for e in res.errors:
        console.print(f"  [red]ERROR:[/red] {e}")
    for w in res.warnings:
        console.print(f"  [yellow]WARN:[/yellow] {w}")
    for i in res.info:
        console.print(f"  [dim]{i}[/dim]")
    console.print("[bold green]OK[/bold green]" if res.ok else "[bold red]FAILED[/bold red]")


@app.command()
def logs(
    name: str = typer.Argument(...),
    lines: int = typer.Option(40, "--lines", "-n"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """List hop logs; use --lines to tail the most recent one."""
    _setup(verbose)
    d = config.db_logs_dir(name)
    files = sorted(d.glob("*.log")) if d.exists() else []
    if not files:
        console.print(f"[yellow]No logs in {d}[/yellow]")
        return
    for f in files:
        console.print(f"  {f.name}")
    latest = files[-1]
    console.print(f"\n[bold]Tail of {latest.name} (last {lines} lines):[/bold]")
    report = logparse.parse_log(latest)
    console.print(f"  criticals={len(report.criticals)} errors={len(report.errors)} "
                  f"warnings={len(report.warnings)} openupgrade_lines={len(report.ou_lines)}")
    text = latest.read_text(errors="replace").splitlines()
    for line in text[-lines:]:
        console.print(f"  {line}", highlight=False)


@app.command()
def uninstall(
    db: str = typer.Argument(..., help="Database name"),
    modules: str = typer.Option(..., "--modules", help="Comma-separated"),
    version: int = typer.Option(..., "--version", help="Odoo version of the server code"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Uninstall modules (dependency-safe) via odoo shell."""
    _setup(verbose)
    _run_shell_util(version, db, "uninstall.py", PW_MIGRATOR_MODULES=modules)


@app.command()
def install(
    db: str = typer.Argument(...),
    modules: str = typer.Option(..., "--modules", help="Comma-separated"),
    version: int = typer.Option(..., "--version"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Install modules via odoo shell."""
    _setup(verbose)
    _run_shell_util(version, db, "install.py", PW_MIGRATOR_INSTALL=modules)


@app.command(name="set-auto-install")
def set_auto_install(
    db: str = typer.Argument(...),
    modules: str = typer.Option(..., "--modules", help="Comma-separated"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Set auto_install=false via SQL (no Odoo needed)."""
    _setup(verbose)
    _pg_or_die()
    for name in [m.strip() for m in modules.split(",") if m.strip()]:
        pg.execute_sql(db, f"UPDATE ir_module_module SET auto_install = false "
                           f"WHERE name = '{name.replace(chr(39), chr(39) * 2)}';")
        console.print(f"[green]auto_install=false:[/green] {name}")


def _run_shell_util(version: int, db: str, script: str, **env_extra: str):
    script_path = config.shell_scripts_dir() / script
    rc, _ = odoo.shell(version, db, script_path.read_text(),
                       log_path=config.db_logs_dir(db) / f"manual_{script}", env_extra=env_extra)
    console.print(f"[green]done[/green]" if rc == 0 else f"[red]failed (exit {rc})[/red]")
    if rc != 0:
        raise typer.Exit(1)


@app.command()
def backup(
    db: str = typer.Argument(..., help="Database name"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """pg_dump a database (custom format) into the backups dir."""
    _setup(verbose)
    _pg_or_die()
    try:
        path = pg.backup_database(db)
        console.print(f"[green]Backup:[/green] {path}")
    except pg.PGError as e:
        console.print(f"[red]Backup failed:[/red] {e}")
        raise typer.Exit(1)


@app.command()
def drop(
    db: str = typer.Argument(..., help="Database name"),
    force: bool = typer.Option(False, "--force", help="Actually drop (no prompting)"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Drop a database (requires --force)."""
    _setup(verbose)
    _pg_or_die()
    if not force:
        console.print("[yellow]Refusing to drop without --force[/yellow]")
        raise typer.Exit(1)
    pg.drop_database(db)
    console.print(f"[green]Dropped:[/green] {db}")


def _print_result(res: pipeline.HopResult, label: str) -> None:
    if res.ok:
        console.print(f"\n[bold green]SUCCESS:[/bold green] {label}")
    else:
        console.print(f"\n[bold red]FAILED:[/bold red] {label}")
        for e in res.errors:
            console.print(f"  [red]ERROR:[/red] {e}")
    for w in res.warnings[:15]:
        console.print(f"  [yellow]WARN:[/yellow] {w}")
    if res.log_path:
        console.print(f"  [dim]log: {res.log_path}[/dim]")


if __name__ == "__main__":
    app()