# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`odoo_migrator` v2 — an OpenUpgrade migration orchestrator (Python 3.10+, typer/pyyaml/rich) for the PerfectWork/ANSIS workspace. It migrates Odoo databases one hop at a time (N → N+1) using `pg_dump`/`createdb` copies plus short-lived `odoo-bin` subprocesses.

- **Supersedes v1 `pw_migrate`** (kept untouched as reference in `../pw_migrate/`). Note: the workspace-level `perfectwork/CLAUDE.md` still documents `pw-migrate` — the command here is **`odoo-migrator`**, and per-DB YAMLs live in `databases/` here, not `PW.Python.Modules/pw_migrate/databases/`.
- No XML-RPC, no live server: every Odoo interaction is a subprocess with `--no-http` (appended automatically in `odoo.py`) and `--stop-after-init`, so the tool never binds port 8069. Preserve this invariant in any new code path.
- `/opt/PW/odoo_migrator` and `/Users/wsloh/perfectwork/odoo_migrator` are the **same directory** (bind/symlink) — edits from either path apply to both.

## Commands

```bash
# Install (editable, with test extras — same convention as cstation)
uv pip install -e ".[test]"

# Run CLI
uv run odoo-migrator --help

# Preflight before any migration work
brew services start postgresql@18
uv run odoo-migrator doctor   # PG + per-version odoo-bin/conf/venv/openupgradelib checks
```

Package manager is **uv**; build backend **hatchling**; entry point `odoo-migrator = "odoo_migrator.cli:app"`. There are **no tests yet** in this repo (the `test` extra pre-installs pytest/ruff per the cstation convention) — verify changes with `doctor` and `--dry-run` hops. Full workflow example (restore → analyze → hop → verify → chain) is in `README.md`.

Environment: `PW_ROOT` (`/opt/PW`), `PW_PG_BIN` (`/opt/homebrew/opt/postgresql@18/bin`), `PW_DATA_DIR` (`/opt/PW/data`), `PW_DB_USER/HOST/PORT/PASSWORD`, `PW_MIGRATOR_CONFIG_DIR`. All defaults in `config.py`.

## Architecture

Layered, single package (`odoo_migrator/`):

| Module | Role |
|---|---|
| `cli.py` | Typer app; 13 commands. All DB-name args are config names (e.g. `MYDB`), not database names |
| `config.py` | Path/env resolution. `VERSION_DIRS` maps Odoo major → workspace checkout (**`PW.6.0` = Odoo 16**, legacy naming); `OPENUPGRADE_DIRS` maps target version → `OpenUpgrade_{X}.0` scripts checkout. Unsupported versions are hard errors, never guessed |
| `dbcfg.py` | Strict YAML load/validate: unknown keys rejected, hop keys must be `N_to_M` with M = N+1, and `db_name(v)` raises if unmapped — **v2 never invents DB names** |
| `pipeline.py` | `run_hop()` — the 14-step orchestration below; `run_chain()` — hops with stop-and-verify gates |
| `odoo.py` | `odoo-bin` subprocess runner (per-version venv, piped-stdin `shell` scripts, `-u all`, `openupgrade_run` with `--upgrade-path`) |
| `pg.py` | psql/createdb/pg_dump/dropdb via `PW_PG_BIN` (never bare PATH); read-only state queries; `restore_from_zip` (Odoo zip = dump.sql + filestore) |
| `state.py` | Persistence under `databases/states/`: `<DB>.state.yaml` (current_version + hop history) and `<DB>.snapshot.<v>.json` (pre-hop snapshot) |
| `verify.py` | Module-set diff + exact per-table count diff vs snapshot |
| `logparse.py` | Log gate: classifies CRITICAL/ERROR/WARNING + openupgrade-tagged lines from the hop log |
| `analyze.py` | Gap analysis: installed modules vs target addon trees (parsed from odoo.conf), drafts hop configs (`--save-draft`) |
| `shell/` | `uninstall.py` / `install.py` — executed **inside** `odoo-bin shell` via stdin; parameterized by env vars (`PW_MIGRATOR_MODULES`, `PW_MIGRATOR_INSTALL`, `PW_MIGRATOR_REPLACE_OLD/NEW`); report progress with `om:` markers; scripts commit themselves |

**Hop order** (`pipeline.run_hop`, keep the order when refactoring): pre-flight → SQL snapshot (modules, table counts, model→module owners) → `pg_dump` backup → uninstall via shell → `auto_install=false` SQL → pre-SQL → `-u all` on source → `createdb -T` copy → filestore copytree → post-copy SQL → apply OpenUpgrade patches (backed up, restored in `finally`) → OpenUpgrade run on target → log gate → reinstall/replace via shell → verify → persist state.

**Where things live outside the repo** (all under `PW_ROOT`): version checkouts `PW.{X}.0/`, OpenUpgrade checkouts, enterprise `{X}.E/`, shared `data/` (filestore, backups, `migration_logs/<DB>/`). `patches/` under this package holds `openupgrade_patches` sources (dir is created on demand).

## Invariants and gotchas

- **The state file is the truth** for `current_version` (written after every hop; YAML `current_version` is the declared start point). `run_chain` trusts the state file and warns on mismatch — don't "fix" this to trust YAML.
- **Verify semantics**: record LOSS in a table owned by a module *not* on the removal list is an **ERROR** (fails the hop); shrinkage from removed modules is a warning; lost-module detection catches dependency cascades. Verification failure fails the hop and stops chains.
- **Log gate**: any CRITICAL or ERROR line in the hop log fails the hop (`--no-log-gate` downgrades to warnings). Exit code alone is not sufficient.
- **Never allow target overwrite**: `pre_flight` refuses when the target DB or its filestore already exists; `restore_from_zip` refuses when the DB exists. Keep these refusals.
- **`analyze.py` mirrors Odoo's `get_modules()` exactly** — one level per addons_path entry, `isfile(<entry>/__manifest__.py)`, symlinks followed. This matters because `PW_ADDONS.{6,7,18}.0/ENTERPRISE` are symlinks to `{16,17,18}.E` checkouts. Depth-3 globs or `find` without `-L` break correctness here.
- **Enterprise data does not migrate**: EE modules may be on the addons path (code available) but OpenUpgrade ships no EE scripts. Precedent: uninstall EE before hop 1 (see `databases/example.yaml` notes); reinstalling on 18 means fresh configuration.
- **Credentials come from env vars only**, never from YAML configs.
- `databases/*.draft.yaml` files are analyze output; `dbcfg.list_database_configs()` excludes them — keep the `.draft` naming convention.