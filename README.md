# odoo_migrator 🚀

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![Package Manager](https://img.shields.io/badge/uv-fast-green.svg)](https://docs.astral.sh/uv/)
[![Build Backend](https://img.shields.io/badge/build-hatchling-orange.svg)](https://hatch.pypa.io/)
[![CLI Framework](https://img.shields.io/badge/cli-typer-red.svg)](https://typer.tiangolo.com/)
[![Release](https://img.shields.io/badge/release-v2.0.0-blue.svg)]()
[![License](https://img.shields.io/badge/license-Proprietary-red.svg)]()

**`odoo_migrator`** is an OpenUpgrade migration orchestrator v2 for the PerfectWork / ANSIS Odoo ecosystem. It migrates enterprise Odoo databases one major version hop at a time (N → N+1) through isolated subprocesses, automated database snapshots, piped shell operations, in-flight OpenUpgrade patching, and dual-gate verification.

> [!NOTE]
> Supersedes legacy v1 `pw_migrate`. It replaces brittle live XML-RPC server connections with short-lived `--no-http` subprocesses, provides strict schema validation, accurate gap analysis, and per-table record loss verification.

---

## 📑 Table of Contents

- [Key Highlights](#-key-highlights)
- [System Architecture](#-system-architecture)
- [Installation & Setup](#-installation--setup)
- [Command Hierarchy](#-command-hierarchy)
- [Quickstart Workflows](#-quickstart-workflows)
  - [0. Preflight Health Checks](#0-preflight-health-checks)
  - [1. Restore Production Backup](#1-restore-production-backup)
  - [2. Addons Gap Analysis & Config Drafting](#2-addons-gap-analysis--config-drafting)
  - [3. Single-Hop Execution (Dry Run & Apply)](#3-single-hop-execution-dry-run--apply)
  - [4. Verification & Log Tailing](#4-verification--log-tailing)
  - [5. Automated Multi-Hop Chains](#5-automated-multi-hop-chains)
  - [6. Manual Utilities](#6-manual-utilities)
- [Configuration & State Layout](#-configuration--state-layout)
- [Environment Variables](#-environment-variables)
- [Testing & Quality Assurance](#-testing--quality-assurance)
- [Documentation Suite](#-documentation-suite)

---

## ✨ Key Highlights

- **⚡ Zero Port Collisions (`--no-http`)**: Every Odoo operation runs via short-lived subprocesses appending `--no-http` and `--stop-after-init`, completely eliminating port 8069 contention.
- **🛡️ 14-Step Safe Hop Pipeline**: Atomic execution covering pre-flight checks, SQL snapshots, automated `pg_dump` backups, piped shell uninstalls, template DB cloning (`createdb -T`), in-flight patch management, and dual-gate verification.
- **🔍 Strict Addons Gap Analysis**: Accurately mirrors Odoo's native `get_modules()` discovery algorithm to identify installed modules against target version trees (handling symlinks such as `PW_ADDONS.18.0/ENTERPRISE -> 18.E`).
- **📊 Dual-Gate Verification**: Hard log gate (parsing `CRITICAL` / `ERROR` messages) combined with exact table-level record count diffing that halts the chain immediately if data is lost in retained modules.
- **🔁 Resumable Multi-Hop Chains**: State-backed persistence (`.state.yaml`) tracks real-time progress across major Odoo versions (e.g. 16 → 17 → 18), allowing safe stop-and-verify inspection after each hop.
- **🧩 In-Flight OpenUpgrade Patching**: Dynamically applies temporary patches to OpenUpgrade migration scripts with guaranteed `.pw2.bak` cleanup in `finally` blocks.

---

## 🏛️ System Architecture

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

## 📦 Installation & Setup

### Prerequisites
- **Python 3.10+** (managed via `.python-version`)
- [**`uv`**](https://docs.astral.sh/uv/) (recommended package manager)
- **PostgreSQL 18** client binaries (via Homebrew: `/opt/homebrew/opt/postgresql@18/bin`)

### Installation

```bash
# Navigate to the workspace
cd /opt/PW/odoo_migrator   # or /Users/wsloh/perfectwork/odoo_migrator

# Install in editable mode with development & test extras
uv pip install -e ".[test]"

# Verify CLI installation
uv run odoo-migrator --help
```

---

## 🛠️ Command Hierarchy

```
odoo-migrator
├── doctor            # Pre-flight environment and runtime diagnostic checks
├── status            # Database migration dashboard (active versions & sizes)
├── version           # Show odoo_migrator package version
├── restore           # Restore Odoo zip backup (dump.sql + filestore) into PostgreSQL
├── package           # Build Odoo-format deploy zip (dump.sql + filestore) from a DB
├── analyze           # Compare installed modules against target addons paths
├── hop               # Run a single migration hop (N -> N+1) through 14-step pipeline
├── chain             # Run multi-hop migrations with stop-and-verify gates
├── verify            # Verify database against pre-hop snapshot (record count diff)
├── logs              # Inspect and tail migration execution logs
├── uninstall         # Uninstall modules safely via odoo-bin shell
├── install           # Install modules via odoo-bin shell
├── set-auto-install  # Disable module auto_install flag directly via SQL
├── backup            # pg_dump custom format database backup
├── cleanup           # Housekeeping: drop intermediate databases/artifacts below current version
└── drop              # Guarded database drop utility (requires --force)
```

---

## 🚀 Quickstart Workflows

### 0. Preflight Health Checks

Verify PostgreSQL service status, virtualenv health, and OpenUpgrade scripts:

```bash
brew services start postgresql@18
uv run odoo-migrator doctor
```

### 1. Restore Production Backup

Restore a production backup archive (`dump.sql` + filestore) locally:

```bash
uv run odoo-migrator restore MYDB ~/Downloads/backup_v16.zip --version 16
```

### 2. Addons Gap Analysis & Config Drafting

Compare installed modules against target version checkouts (16 → 18) and generate a draft migration configuration:

```bash
uv run odoo-migrator analyze MYDB --from 16 --to 18 --save-draft
```

### 3. Single-Hop Execution (Dry Run & Apply)

Execute a single-step migration hop (16 → 17):

```bash
# 1. Preview changes in dry-run mode
uv run odoo-migrator hop MYDB --from 16 --to 17 --dry-run

# 2. Execute the 14-step hop
uv run odoo-migrator hop MYDB --from 16 --to 17
```

### 4. Verification & Log Tailing

Inspect migration logs and verify row-count integrity against the snapshot:

```bash
# Verify database record counts and module sets
uv run odoo-migrator verify MYDB --version 17

# Tail the last 50 lines of the latest migration log
uv run odoo-migrator logs MYDB -n 50
```

### 5. Automated Multi-Hop Chains

Migrate across multiple major versions sequentially (e.g. 16 → 17 → 18):

```bash
# Stops after each hop for manual verification:
uv run odoo-migrator chain MYDB --to 18

# Unattended continuous migration across verified hops:
uv run odoo-migrator chain MYDB --to 18 --auto
```

### 6. Manual Utilities

```bash
# Uninstall conflicting modules in Odoo shell
uv run odoo-migrator uninstall mydb_16 --modules "account_accountant,account_asset" --version 16

# Disable auto_install via SQL
uv run odoo-migrator set-auto-install mydb_16 --modules "snailmail,iap"

# Standalone custom-format pg_dump backup
uv run odoo-migrator backup mydb_16

# Housekeeping after a confirmed deploy: drop intermediate DBs/filestores/dumps
uv run odoo-migrator cleanup MYDB          # plan only
uv run odoo-migrator cleanup MYDB --force  # execute
```

### 7. Package the Result for Deployment

Build an Odoo-format backup ZIP (`dump.sql` + `filestore/`) from the migrated database — uploadable via the Odoo database manager, the inverse of `restore`:

```bash
uv run odoo-migrator package mydb_18
```

---

## 📂 Configuration & State Layout

All migration definitions and state files reside in `databases/`:

```
databases/
├── <DB>.yaml                  # Database migration definition (e.g. MYDB.yaml)
├── <DB>.draft.yaml            # Draft generated by `odoo-migrator analyze`
└── states/                    # Migration state & snapshots (persisted truth)
    ├── <DB>.state.yaml        # Current recorded version and hop log history
    ├── <DB>.snapshot.16.json  # Pre-hop 16 snapshot (modules, table row counts)
    └── <DB>.snapshot.17.json  # Pre-hop 17 snapshot
```

---

## 🌐 Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `PW_ROOT` | `/opt/PW` | Workspace root holding version checkouts and OpenUpgrade trees |
| `PW_PG_BIN` | `/opt/homebrew/opt/postgresql@18/bin` | Directory containing PostgreSQL client binaries |
| `PW_DATA_DIR` | `/opt/PW/data` | Shared directory for filestores, backups, and logs |
| `PW_MIGRATOR_CONFIG_DIR` | `<package>/databases` | Override path for the database YAML configurations directory |
| `PW_DB_USER` / `PW_DB_HOST` / `PW_DB_PORT` | `wsloh` / `127.0.0.1` / `5432` | PostgreSQL database connection settings |
| `PW_DB_PASSWORD` | *(None)* | PostgreSQL server password |

---

## 🧪 Testing & Quality Assurance

```bash
# Run Ruff lint checks
uv run ruff check .

# Validate environment and script availability
uv run odoo-migrator doctor

# Dry-run migration pipeline
uv run odoo-migrator hop MYDB --from 16 --to 17 --dry-run
```

---

## 📚 Documentation Suite

- 🏛️ **[System Architecture (ARCHITECTURE.md)](ARCHITECTURE.md)**: Deep dive into subprocess isolation, 14-step hop lifecycle, and dual-gate verification.
- 🗺️ **[Strategic Roadmap (ROADMAP.md)](ROADMAP.md)**: Phased milestones, development tracks, and future capabilities.
- 📖 **[CLI Command Reference (docs/cli-reference.md)](docs/cli-reference.md)**: Complete manual for all 14 CLI commands with full parameter details.
- ⚙️ **[Configuration Guide (docs/configuration-guide.md)](docs/configuration-guide.md)**: Complete YAML schema reference, patch format, and state management.
- 🤝 **[Developer & Contributing Guide (CONTRIBUTING.md)](CONTRIBUTING.md)**: Contribution standards, code style, and invariant rules.
- 🤖 **[AI Agent Guidance (AGENTS.md)](AGENTS.md)**: Canonical operating instructions for AI assistants.