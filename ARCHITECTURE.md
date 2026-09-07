# odoo_migrator System Architecture 🏛️

This document describes the internal architecture, design principles, and subsystem implementations of **`odoo_migrator` v2**.

---

## 📐 Core Design Principles

1. **Subprocess Isolation & Zero Port Collision**:
   - Migration orchestrators must never clash with active development environments or services.
   - Every Odoo operation is executed as a short-lived subprocess passing `--no-http` and `--stop-after-init`.
   - The CLI never binds TCP port 8069 or runs background HTTP daemons.
2. **Strict Schema & Major Version Invariants**:
   - All versions are represented strictly as Odoo major numbers (`13` through `19`). Internal project shorthands are forbidden.
   - Database names must be explicitly defined in configuration. The system **never invents or guesses** database names.
   - Unknown configuration keys are rejected at load time to prevent silent typos.
3. **Strict 14-Step Idempotent Hop Orchestration**:
   - Single-version hops (N → N+1) execute across 14 deterministic phases, matching OpenUpgrade's official upgrade methodology.
   - Source databases are protected by mandatory pre-hop backups and non-destructive DB cloning (`createdb -T`).
4. **Dual-Gate Verification**:
   - Log gate: Stream parsing flags any `CRITICAL` or `ERROR` line from the log as an immediate hop failure.
   - Data gate: Row-level table count diffing catches record loss in retained modules and halts chains immediately.
5. **State Persistence as Ground Truth**:
   - Ground-truth migration progress is stored in `databases/states/<DB>.state.yaml` and `<DB>.snapshot.<v>.json`.
   - Multi-hop chains trust the persisted state file over static YAML configuration files.

---

## 🧩 System Architecture Overview

```
                      ┌─────────────────────────────────────────┐
                      │           odoo-migrator CLI             │
                      │       (Typer Multi-Command App)         │
                      └────────────────────┬────────────────────┘
                                           │
                 ┌─────────────────────────┼─────────────────────────┐
                 ▼                         ▼                         ▼
       ┌──────────────────┐      ┌──────────────────┐      ┌──────────────────┐
       │  Config Manager  │      │ Database Config  │      │  State Manager   │
       │   (config.py)    │      │   (dbcfg.py)     │      │   (state.py)     │
       └────────┬─────────┘      └────────┬─────────┘      └────────┬─────────┘
                │                         │                         │
    ┌───────────┴─────────────────────────┴─────────────────────────┴───────────┐
    │                                                                           │
    ▼                                      ▼                                    ▼
┌───────────────────────┐      ┌───────────────────────┐      ┌───────────────────────┐
│ Addons Gap Analyzer   │      │ 14-Step Hop Pipeline  │      │ Dual-Gate Verification│
│     (analyze.py)      │      │     (pipeline.py)     │      │  (verify & logparse)  │
└───────────────────────┘      └───────────┬───────────┘      └───────────────────────┘
                                           │
                      ┌────────────────────┴────────────────────┐
                      ▼                                         ▼
            ┌───────────────────┐                     ┌───────────────────┐
            │  Odoo Subprocess  │                     │ PostgreSQL Runner │
            │     (odoo.py)     │                     │      (pg.py)      │
            └─────────┬─────────┘                     └─────────┬─────────┘
                      │                                         │
                      ▼                                         ▼
            ┌───────────────────┐                     ┌───────────────────┐
            │  odoo-bin shell   │                     │  psql / createdb  │
            │  (piped stdin)    │                     │  pg_dump custom   │
            └───────────────────┘                     └───────────────────┘
```

---

## ⚙️ Subsystem Deep Dives

### 1. Configuration & Path Resolution (`config.py` & `dbcfg.py`)

`odoo_migrator` resolves all directory paths dynamically relative to workspace roots without hardcoded relative traversal:

- **Version Directory Mapping**: Maps Odoo major version to checkout paths under `PW_ROOT` (`/opt/PW`):
  - `16` → `PW.6.0/`
  - `17` → `PW.7.0/`
  - `18` → `PW.18.0/`
- **OpenUpgrade Script Mapping**: Maps target versions to script checkouts:
  - `17` → `OpenUpgrade_17.0/openupgrade_scripts/scripts/`
  - `18` → `OpenUpgrade_18.0/openupgrade_scripts/scripts/`
- **Strict Validation**:
  - `DatabaseConfig.validate()` checks required keys (`name`, `databases`, `current_version`, `target_version`, `hops`).
  - Hops must follow `{N}_to_{M}` where `M == N + 1`.
  - All referenced versions must exist in the `databases` dictionary.

---

### 2. Isolated Odoo Subprocess Engine (`odoo.py`)

The engine manages isolated child processes executing `odoo-bin` with exact version virtualenvs:

- **Environment Isolation**: Executes Python using `PW_ROOT/PW.{X}.0/.venv/bin/python`.
- **Subprocess Arguments**: Every invocation automatically appends:
  - `--config <path-to-odoo.conf>`
  - `-d <dbname>`
  - `--no-http` (never binds port 8069)
  - `--stop-after-init` (terminates immediately once operations complete)
- **Piped Stdin Shell Execution**: Rather than relying on live RPC or temporary Python files, `odoo.shell()` pipes scripts (`shell/uninstall.py`, `shell/install.py`) directly to `odoo-bin shell` standard input, parameterized via environment variables (`PW_MIGRATOR_MODULES`, `PW_MIGRATOR_INSTALL`).
- **Progress Tracking**: Piped scripts emit structured stdout markers (`om:uninstall:begin`, `om:uninstall:done`) which are parsed to track real-time execution status.

---

### 3. PostgreSQL & Storage Engine (`pg.py`)

The PostgreSQL layer isolates all database operations using client binaries identified by `PW_PG_BIN`:

- **Path Enforcement**: Explicitly executes `/opt/homebrew/opt/postgresql@18/bin/{psql,createdb,dropdb,pg_dump}` to prevent system PATH conflicts.
- **Atomic Database Cloning**: Hop target databases are created using PostgreSQL template copies:
  ```bash
  createdb -h 127.0.0.1 -p 5432 -U wsloh -T <source_db> <target_db>
  ```
- **Odoo Backup ZIP Restoration**: Parses `.zip` backups containing `dump.sql` and the `filestore/` directory:
  - Restores schema and table data directly via `psql`.
  - Unpacks the filestore directly into `PW_DATA_DIR/filestore/<dbname>`.
  - Inspects initial migration state (`base` version, installed modules) from the restored database.

---

### 4. 14-Step Hop Pipeline Lifecycle (`pipeline.py`)

Each single-version migration hop follows a strictly sequenced 14-step orchestration:

```
 1. Pre-Flight Checks (DB existence, venv, scripts, filestore)
    │
 2. SQL Baseline Snapshot (installed modules, table row counts, model owners)
    │
 3. Database Backup (pg_dump -Fc custom format)
    │
 4. Shell Uninstall (odoo-bin shell on source database)
    │
 5. Auto-Install SQL Update (UPDATE ir_module_module SET auto_install=false)
    │
 6. Pre-SQL Hooks (clean stale XML views, drop conflicting triggers)
    │
 7. Source Full Update (-u all on source database)
    │
 8. Database & Filestore Copy (createdb -T + shutil.copytree)
    │
 9. Post-Copy SQL Hooks (environment adjustments on target database)
    │
10. Apply In-Flight Patches (patch files copied with .pw2.bak backups)
    │
11. OpenUpgrade Run (-u all --upgrade-path on target database)
    │
12. Log Gate Evaluation (parse CRITICAL/ERROR lines; restore patches in finally block)
    │
13. Reinstall & Replace Modules (odoo-bin shell on target database)
    │
14. Dual Verification & State Persistence (verify row counts, write state.yaml)
```

If any step fails, execution halts immediately with rollback instructions.

---

### 5. Addons Gap Analysis Engine (`analyze.py`)

To eliminate migration guesswork, `analyze.py` inspects the exact module state before running hops:

- **Manifest Inspection**: Scans directories parsed from `addons_path` in `odoo.conf` across all target versions.
- **Accurate Addon Discovery**: Matches Odoo's native `get_modules()` algorithm, traversing symlinks (`PW_ADDONS.18.0/ENTERPRISE -> 18.E`) to determine whether module source code is available in the target version.
- **Gap Classification**:
  - `Core`: Standard Odoo community module.
  - `Enterprise`: Odoo Enterprise addon.
  - `OCA`: Community association repository addon.
  - `Custom`: In-house proprietary addon.
- **Draft Generator**: Emits a clean YAML specification with recommended `modules_to_remove` for missing addons.

---

### 6. Verification & Log Gate Engine (`verify.py` & `logparse.py`)

Post-hop validation uses two complementary inspection layers:

1. **Log Parsing Gate**:
   - Parses the combined migration log file generated across all subprocesses.
   - Classifies log records into `CRITICAL`, `ERROR`, `WARNING`, and OpenUpgrade progress tags (`openupgrade:`).
   - Any log entry categorized as `CRITICAL` or `ERROR` fails the hop unless explicitly downgraded with `--no-log-gate`.
2. **Snapshot Verification Gate**:
   - Compares installed modules against the pre-hop snapshot:
     - Expected removals confirmed.
     - Unexpected missing modules flagged as cascade errors.
   - Compares exact table row counts (`SELECT count(*) FROM <table>`):
     - Table drops or row shrinkage in tables owned by removed modules are expected (reported as warnings/info).
     - **Record loss in any table owned by a retained module is treated as a critical ERROR and halts the chain.**

---

### 7. State & Snapshot Store (`state.py`)

State persistence decouples the orchestrator from volatile runtime memory:

- **State File (`databases/states/<DB>.state.yaml`)**:
  - Stores `current_version` and an append-only `history` list of all executed hops with timestamp, log file path, and verification summaries.
- **Snapshot Files (`databases/states/<DB>.snapshot.<v>.json`)**:
  - Contains full JSON serialization of pre-hop database state (modules list, per-table row counts, model module mappings).
  - Used for standalone verification (`odoo-migrator verify <DB> --version <v>`).

