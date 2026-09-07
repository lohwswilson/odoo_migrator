# odoo-migrator CLI Command Reference Manual 📖

`odoo-migrator` is an OpenUpgrade migration orchestrator v2 for the PerfectWork / ANSIS Odoo ecosystem. It manages database migrations one version hop at a time (N → N+1) through isolated subprocesses, automated snapshots, and dual-gate verification.

---

## 📑 Table of Contents

1. [Global Options](#global-options)
2. [`odoo-migrator doctor` - Environment & Prerequisite Diagnostics](#1-odoo-migrator-doctor)
3. [`odoo-migrator status` - Fleet Migration Status Dashboard](#2-odoo-migrator-status)
4. [`odoo-migrator restore` - Odoo Backup ZIP Restoration](#3-odoo-migrator-restore)
5. [`odoo-migrator analyze` - Addons Gap Analysis & Config Drafting](#4-odoo-migrator-analyze)
6. [`odoo-migrator hop` - Atomic Single-Hop Migration (N → N+1)](#5-odoo-migrator-hop)
7. [`odoo-migrator chain` - Multi-Hop Migration with Verify Gates](#6-odoo-migrator-chain)
8. [`odoo-migrator verify` - Standalone Snapshot & Table Verification](#7-odoo-migrator-verify)
9. [`odoo-migrator logs` - Migration Log Inspection & Tail](#8-odoo-migrator-logs)
10. [`odoo-migrator uninstall` - Piped Shell Module Uninstallation](#9-odoo-migrator-uninstall)
11. [`odoo-migrator install` - Piped Shell Module Installation](#10-odoo-migrator-install)
12. [`odoo-migrator set-auto-install` - SQL Auto-Install Disabler](#11-odoo-migrator-set-auto-install)
13. [`odoo-migrator backup` - Standalone Custom-Format PostgreSQL Dump](#12-odoo-migrator-backup)
14. [`odoo-migrator drop` - Guarded Database Dropper](#13-odoo-migrator-drop)
15. [`odoo-migrator version` - Version Information](#14-odoo-migrator-version)
16. [`odoo-migrator package` - Odoo-Format Deploy Zip Builder](#15-odoo-migrator-package)

---

## Global Options

```bash
odoo-migrator [OPTIONS] COMMAND [ARGS]...
```

- `--help`: Show top-level CLI help and list all registered commands.
- `--verbose, -v`: Enable verbose debug logging (`DEBUG` level) with millisecond timestamps.

---

## 1. `odoo-migrator doctor`

Inspects PostgreSQL connectivity and validates required runtime files for every supported Odoo version checkout (Odoo 13 through 19) and OpenUpgrade scripts directory.

```bash
odoo-migrator doctor [OPTIONS]
```

### Options

| Option | Type | Default | Description |
|---|---|---|---|
| `--verbose`, `-v` | Flag | `False` | Enable debug logging |

### Diagnostic Checks Performed

1. **PostgreSQL Connectivity**: Verifies TCP connection to host/port (`PW_DB_HOST:PW_DB_PORT`), role authentication (`PW_DB_USER`), and availability of binaries (`PW_PG_BIN`).
2. **Per-Version Runtime Layout**:
   - `odoo-bin` executable exists in `PW_ROOT/PW.{X}.0/`
   - `odoo.conf` configuration file exists in `PW_ROOT/PW.{X}.0/`
   - Python virtual environment exists in `PW_ROOT/PW.{X}.0/.venv/bin/python`
   - `openupgradelib` is importable inside the version's virtual environment
3. **OpenUpgrade Scripts**:
   - Checks presence of migration scripts in `PW_ROOT/OpenUpgrade_{X}.0/openupgrade_scripts/scripts/` for target versions 14 through 19.

### Example

```bash
uv run odoo-migrator doctor
```

---

## 2. `odoo-migrator status`

Displays an overview of all configured database migrations located in `databases/`. Reads live state from PostgreSQL and per-database state files (`databases/states/<DB>.state.yaml`).

```bash
odoo-migrator status [OPTIONS]
```

### Options

| Option | Type | Default | Description |
|---|---|---|---|
| `--verbose`, `-v` | Flag | `False` | Enable debug logging |

### Columns Reported

- **DB**: Config name (e.g. `MYDB`).
- **Configured**: Defined transition path (`current_version -> target_version`).
- **State**: State file status (`OK` if `.state.yaml` exists, else `none`).
- **Version**: Current active version according to `.state.yaml` (flags `(yaml stale)` if YAML diverges from recorded state).
- **DB exists**: Green `yes` if the PostgreSQL database for the current version exists; `no` otherwise.
- **Size**: Database size reported by PostgreSQL (e.g. `142 MB`).
- **Last hop**: Last recorded hop transition (e.g. `16->17`).

### Example

```bash
uv run odoo-migrator status
```

---

## 3. `odoo-migrator restore`

Restores an Odoo backup ZIP file (containing `dump.sql` and the `filestore/` directory) into local PostgreSQL and the shared filestore directory.

```bash
odoo-migrator restore [OPTIONS] NAME ZIP_PATH
```

### Arguments

- `NAME`: Configuration identifier (e.g. `MYDB`). Looks up `databases/<NAME>.yaml`.
- `ZIP_PATH`: Path to the `.zip` archive exported from Odoo web backup manager.

### Options

| Option | Type | Default | Description |
|---|---|---|---|
| `--version` | Integer | `current_version` | Target Odoo version to map the target database name |
| `--no-filestore` | Flag | `False` | Skip extracting and restoring the filestore directory |
| `--verbose`, `-v` | Flag | `False` | Enable debug logging |

### Safety Invariants

- **Refuses overwrite**: If the mapped target database already exists in PostgreSQL, the command halts immediately with an error.
- **Header inspection**: Validates SQL dump integrity and identifies the initial `base` version and installed module count.

### Example

```bash
uv run odoo-migrator restore MYDB ~/Downloads/backup_v16.zip --version 16
```

---

## 4. `odoo-migrator analyze`

Compares all modules currently installed in the source database against the target versions' addons paths (parsed directly from each version's `odoo.conf`). Detects missing modules, module relocations, and drafts hop removal configurations.

```bash
odoo-migrator analyze [OPTIONS] NAME
```

### Arguments

- `NAME`: Configuration identifier (e.g. `MYDB`).

### Options

| Option | Type | Default | Description |
|---|---|---|---|
| `--from` | Integer | `current_version` | Source Odoo major version |
| `--to` | Integer | `target_version` | Target Odoo major version |
| `--save-draft` | Flag | `False` | Write proposed configuration to `databases/<NAME>.draft.yaml` |
| `--verbose`, `-v` | Flag | `False` | Enable debug logging |

### Behavior

- Scans installed modules in `ir_module_module` where `state = 'installed'`.
- Accurately mirrors Odoo's native `get_modules()` algorithm (examines directory manifests, traversing symlinks such as `PW_ADDONS.18.0/ENTERPRISE -> 18.E`).
- Categorizes each module's origin (Core, Enterprise, OCA, Custom).
- Produces a draft YAML with recommended `modules_to_remove` for unmigratable modules.

### Example

```bash
uv run odoo-migrator analyze MYDB --from 16 --to 18 --save-draft
```

---

## 5. `odoo-migrator hop`

Executes an atomic migration hop for a single Odoo major version step (N → N+1) following the 14-step orchestration pipeline.

```bash
odoo-migrator hop [OPTIONS] NAME
```

### Arguments

- `NAME`: Configuration identifier (e.g. `MYDB`).

### Options

| Option | Type | Default | Description |
|---|---|---|---|
| `--from` | Integer | *(Required)* | Source Odoo major version |
| `--to` | Integer | *(Required)* | Target Odoo major version (must be `--from + 1`) |
| `--dry-run` | Flag | `False` | Simulate hop steps without modifying database or filestore |
| `--skip-backup` | Flag | `False` | Skip pre-hop `pg_dump` backup step |
| `--skip-update` | Flag | `False` | Skip preliminary `-u all` on source database |
| `--skip-filestore` | Flag | `False` | Skip copying the filestore directory |
| `--no-log-gate` | Flag | `False` | Downgrade CRITICAL/ERROR log findings from errors to warnings |
| `--verbose`, `-v` | Flag | `False` | Enable debug logging |

### Safety Invariants

- Rejects any hop where `to_ver != from_ver + 1` (OpenUpgrade only supports single-version increments).
- Target database and target filestore must not exist prior to running.
- In-flight OpenUpgrade patches are applied with automatic backup and guaranteed restoration upon exit (even on failure).
- Failure at any step (exit code, log gate, or record loss verification) prevents state recording and marks the hop failed.

### Example

```bash
# Dry-run preview
uv run odoo-migrator hop MYDB --from 16 --to 17 --dry-run

# Real execution
uv run odoo-migrator hop MYDB --from 16 --to 17
```

---

## 6. `odoo-migrator chain`

Sequentially runs migration hops from the current recorded version up to a target version. By default, halts after each successful hop for operator verification.

```bash
odoo-migrator chain [OPTIONS] NAME
```

### Arguments

- `NAME`: Configuration identifier (e.g. `MYDB`).

### Options

| Option | Type | Default | Description |
|---|---|---|---|
| `--to` | Integer | `target_version` | Final target Odoo major version |
| `--auto` | Flag | `False` | Automatically proceed to subsequent hops if verify gate passes |
| `--dry-run` | Flag | `False` | Dry-run all hops in the chain |
| `--skip-backup` | Flag | `False` | Skip backups before each hop |
| `--skip-update` | Flag | `False` | Skip source `-u all` updates |
| `--skip-filestore` | Flag | `False` | Skip filestore copies |
| `--no-log-gate` | Flag | `False` | Downgrade log gate errors to warnings |
| `--verbose`, `-v` | Flag | `False` | Enable debug logging |

### Behavior

- Reads current state from `databases/states/<NAME>.state.yaml`. If state says version 16 and target is 18, it executes `16 -> 17`, runs verification, stops (or continues if `--auto`), then executes `17 -> 18`.

### Example

```bash
# Run with stop-and-verify gate after each hop
uv run odoo-migrator chain MYDB --to 18

# Unattended continuous migration across clean hops
uv run odoo-migrator chain MYDB --to 18 --auto
```

---

## 7. `odoo-migrator verify`

Verifies an existing migrated database against the pre-hop snapshot recorded in `databases/states/<NAME>.snapshot.<v>.json`.

```bash
odoo-migrator verify [OPTIONS] NAME
```

### Arguments

- `NAME`: Configuration identifier (e.g. `MYDB`).

### Options

| Option | Type | Default | Description |
|---|---|---|---|
| `--version` | Integer | Current version | Version of the database to verify |
| `--verbose`, `-v` | Flag | `False` | Enable debug logging |

### Verification Dimensions

1. **Module Presence Diff**:
   - Planned removals confirmed.
   - Unplanned module loss detected and reported as cascade warnings.
2. **Table Record Count Diff**:
   - Compares exact `count(*)` per table against pre-hop snapshot.
   - Tables owned by removed modules that shrink or drop are logged as warnings.
   - Any record loss in a table owned by a **retained** module triggers a critical **ERROR**.

### Example

```bash
uv run odoo-migrator verify MYDB --version 17
```

---

## 8. `odoo-migrator logs`

Lists all migration log files for a database configuration, showing the status, message counts, and tailing the most recent execution log.

```bash
odoo-migrator logs [OPTIONS] NAME
```

### Arguments

- `NAME`: Configuration identifier (e.g. `MYDB`).

### Options

| Option | Type | Default | Description |
|---|---|---|---|
| `--lines`, `-n` | Integer | `40` | Number of tail lines to display |
| `--verbose`, `-v` | Flag | `False` | Enable debug logging |

### Output

- Lists all timestamps and hop logs found in `PW_DATA_DIR/migration_logs/<NAME>/`.
- Summarizes the newest log: count of `CRITICAL`, `ERROR`, `WARNING`, and OpenUpgrade progress tags.
- Renders the final N lines of the log.

### Example

```bash
uv run odoo-migrator logs MYDB -n 50
```

---

## 9. `odoo-migrator uninstall`

Uninstalls one or more Odoo modules safely with full dependency cascading using an isolated `odoo-bin shell` session.

```bash
odoo-migrator uninstall [OPTIONS] DB
```

### Arguments

- `DB`: Exact PostgreSQL database name (e.g. `mydb_16`).

### Options

| Option | Type | Default | Description |
|---|---|---|---|
| `--modules` | String | *(Required)* | Comma-separated list of technical module names |
| `--version` | Integer | *(Required)* | Odoo major version to use for the shell environment |
| `--verbose`, `-v` | Flag | `False` | Enable debug logging |

### Example

```bash
uv run odoo-migrator uninstall mydb_16 --modules "account_accountant,account_asset" --version 16
```

---

## 10. `odoo-migrator install`

Installs one or more Odoo modules using an isolated `odoo-bin shell` session.

```bash
odoo-migrator install [OPTIONS] DB
```

### Arguments

- `DB`: Exact PostgreSQL database name (e.g. `mydb_17`).

### Options

| Option | Type | Default | Description |
|---|---|---|---|
| `--modules` | String | *(Required)* | Comma-separated list of technical module names |
| `--version` | Integer | *(Required)* | Odoo major version to use for the shell environment |
| `--verbose`, `-v` | Flag | `False` | Enable debug logging |

### Example

```bash
uv run odoo-migrator install mydb_17 --modules "auto_database_backup" --version 17
```

---

## 11. `odoo-migrator set-auto-install`

Directly updates PostgreSQL `ir_module_module` via SQL to set `auto_install = false` on specified modules. Does not require booting an Odoo subprocess.

```bash
odoo-migrator set-auto-install [OPTIONS] DB
```

### Arguments

- `DB`: Exact PostgreSQL database name.

### Options

| Option | Type | Default | Description |
|---|---|---|---|
| `--modules` | String | *(Required)* | Comma-separated list of technical module names |
| `--verbose`, `-v` | Flag | `False` | Enable debug logging |

### Example

```bash
uv run odoo-migrator set-auto-install mydb_16 --modules "snailmail,iap"
```

---

## 12. `odoo-migrator backup`

Generates a standalone PostgreSQL custom-format (`pg_dump -Fc`) backup of a database into the shared backup directory (`PW_DATA_DIR/backups/`).

```bash
odoo-migrator backup [OPTIONS] DB
```

### Arguments

- `DB`: Exact PostgreSQL database name.

### Options

| Option | Type | Default | Description |
|---|---|---|---|
| `--verbose`, `-v` | Flag | `False` | Enable debug logging |

### Example

```bash
uv run odoo-migrator backup mydb_16
```

---

## 13. `odoo-migrator drop`

Drops a PostgreSQL database cleanly. Requires explicit confirmation via `--force`.

```bash
odoo-migrator drop [OPTIONS] DB
```

### Arguments

- `DB`: Exact PostgreSQL database name.

### Options

| Option | Type | Default | Description |
|---|---|---|---|
| `--force` | Flag | `False` | Confirm database destruction (required) |
| `--verbose`, `-v` | Flag | `False` | Enable debug logging |

### Safety Invariants

- Refuses execution if `--force` is omitted.
- Terminates active PostgreSQL backends on the target database before issuing `DROP DATABASE`.

### Example

```bash
uv run odoo-migrator drop mydb_17 --force
```

---

## 14. `odoo-migrator version`

Outputs the currently installed `odoo_migrator` package version.

```bash
odoo-migrator version
```

---

## 15. `odoo-migrator package`

Packages a database plus its filestore as an **Odoo-format backup ZIP** (`dump.sql` + `filestore/` members) — the deploy-ready inverse of `restore`. The output mirrors what `odoo.service.db.dump_db` and OCA `auto_database_backup` produce, so it uploads directly through the Odoo database manager or restores with `pg_restore`/`psql` + a filestore copy.

```bash
odoo-migrator package [OPTIONS] DB
```

### Arguments

- `DB`: Exact PostgreSQL database name (e.g. the final migrated database).

### Options

| Option | Type | Default | Description |
|---|---|---|---|
| `--out` | Path | `<backups_dir>/<DB>_deploy_<stamp>.zip` | Output zip path |
| `--no-filestore` | Flag | `False` | Build a DB-only zip (no `filestore/` members) |
| `--verbose`, `-v` | Flag | `False` | Enable debug logging |

### Format

- `dump.sql`: plain-format `pg_dump` (restorable via `psql`, like VPS backups produced by `auto_database_backup`).
- `filestore/<sha-prefix>/<file>`: mirrors the local filestore tree for the database.

### Safety Invariants

- Refuses to overwrite an existing output zip.
- Refuses to run when the database does not exist.

### Example

```bash
uv run odoo-migrator package BYQ8
# -> /opt/PW/data/backups/BYQ8_deploy_20260907_105426.zip
```

