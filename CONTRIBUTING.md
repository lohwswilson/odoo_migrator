# Contributing to odoo_migrator 🤝

Thank you for contributing to **`odoo_migrator`**! This document provides technical guidelines and developer conventions for extending, maintaining, and testing the codebase.

---

## 🛠️ Development Setup

`odoo_migrator` requires **Python 3.10+** (managed via `.python-version`) and uses [**`uv`**](https://docs.astral.sh/uv/) for high-performance dependency resolution.

### 1. Environment Installation

```bash
# Clone or navigate to the workspace
cd /opt/PW/odoo_migrator   # or /Users/wsloh/perfectwork/odoo_migrator

# Install in editable mode with development and test dependencies
uv pip install -e ".[test]"

# Verify the CLI entrypoint
uv run odoo-migrator --help

# Run preflight diagnostics
uv run odoo-migrator doctor
```

> [!NOTE]
> `/opt/PW/odoo_migrator` and `/Users/wsloh/perfectwork/odoo_migrator` reference the **exact same directory** via filesystem bind/symlink. Edits made in either path apply immediately to both.

---

## 🏛️ Core Architectural Invariants

When contributing code, you must preserve these foundational invariants:

1. **Zero Port Collisions (`--no-http`)**:
   - Every `odoo-bin` invocation must include `--no-http` and `--stop-after-init`.
   - Never write code paths that bind port 8069 or attempt live XML-RPC connections.
2. **Never Guess or Invent Database Names**:
   - Database names must be explicitly provided in `databases` mapping in YAML.
   - If an unmapped version is requested, fail with an explicit `ConfigError`.
3. **The State File is Ground Truth**:
   - The current version of a database during a migration chain is determined by `databases/states/<DB>.state.yaml`, not the static YAML config.
4. **Exact Module Discovery Parity**:
   - `analyze.py` must mirror Odoo's native `get_modules()` logic (examining directory manifests and traversing symlinks). Avoid naive glob searches or tools that ignore symlinks.
5. **Target Protection**:
   - Hop pre-flight checks and `restore` commands must refuse to overwrite existing databases or filestores without explicit manual operator intervention.
6. **In-Flight Patch Safety**:
   - Any modification to files in `OpenUpgrade_*` must be backed up (`.pw2.bak`) and restored within a `finally` block to prevent dirty checkouts.

---

## 🧪 Testing & Quality Assurance

### Verification & Linting

```bash
# Run Ruff lint checks
uv run ruff check .

# Run pre-flight health diagnostics
uv run odoo-migrator doctor

# Validate a database configuration via dry-run hop
uv run odoo-migrator hop MYDB --from 16 --to 17 --dry-run
```

### Writing Automated Tests

When adding unit tests under `tests/`:
- Use `pytest` fixtures to mock `pg.execute_sql` and `odoo.shell` to avoid requiring a live PostgreSQL instance.
- Test both successful transitions and error boundaries (e.g. unmapped version keys, syntax errors in YAML, corrupted logs).

---

## 📝 Code Style & Conventions

- **Type Annotations**: All function signatures must include full type annotations (`from __future__ import annotations`).
- **Path Manipulation**: Always use `pathlib.Path` instead of `os.path` operations.
- **Logging**: Use standard Python `logging.getLogger(__name__)`. Do not use bare `print()` statements outside of CLI user presentation in `cli.py`.
- **User Presentation**: Use `rich.console.Console` and `rich.table.Table` in `cli.py` for structured terminal tables and alerts.

