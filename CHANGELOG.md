# Changelog

## Unreleased — 2026-09-07

Hardening fixes surfaced and verified during the first real production
migration run (16 → 17 → 18). Each fix was found by an actual failure or
false-positive gate, not by review.

### Added

- **`package` command + `pg.package_zip()`** — build an Odoo-format deploy
  zip (`dump.sql` plain pg_dump + `filestore/` members) from any database:
  the deploy-ready inverse of `restore`, uploadable via the Odoo database
  manager or `auto_database_backup`-compatible flows. Refuses to overwrite
  an existing output.

### Fixed

- **`pg.py` `model_modules()`** — table-ownership attribution was built on
  `ir_model.modules`, which is a *non-stored compute* in Odoo 16/17/18 (the
  column never exists in the DB), so every snapshot crashed with
  `column "modules" does not exist`. Ownership is now derived from
  `ir_model_data` (defining module via the `model_<table>` xmlid on
  `ir.model`, union data-record modules), restricted to installed modules —
  the same source Odoo's own `_in_modules` compute aggregates.
- **`odoo.py` `run()`** — subcommands (`shell`) must be the *first* argument
  after `odoo-bin`; they were appended last, so odoo's server parser rejected
  them (`unrecognized parameters: 'shell'`).
- **`odoo.py` `run()`** — set `PGHOST`/`PGPORT`/`PGUSER` libpq fallbacks for
  `odoo.conf` files with `db_host = False`; without them psycopg2 tries the
  unix socket, which Homebrew PostgreSQL does not provide.
- **`cli.py`** — `hop` and `chain` now exit 1 on failure. Previously a failed
  hop printed `FAILED:` and still exited 0, breaking scripted pipelines.

### Changed

- **`pipeline.py`** — the SQL snapshot (verify baseline) is taken *after*
  uninstall / pre-SQL / `-u all` instead of before them. The baseline is now
  "the source as handed to OpenUpgrade", so planned churn (e.g. xmlids deleted
  by configured module removals) is no longer misreported as record loss by
  the verify gate.
- **`pipeline.py`** — fail-fast: when the OpenUpgrade run itself fails, the hop
  records `ok=False` and returns immediately instead of continuing to
  reinstall/verify against a half-migrated database.
- **`odoo.py` `check_version()`** — pre-flight now detects stale
  `openupgradelib` installs (missing the `environment_namespec` kwarg that
  OpenUpgrade 17+ scripts pass to `update_module_names`). Note for operators:
  plain `uv pip install --upgrade openupgradelib` will *not* replace a
  git-pinned dev build whose version string parses as newer — use
  `uv pip install --reinstall-package openupgradelib openupgradelib`.

### Operational notes (learned the hard way)

- Module upgrades silently no-op when the *target* checkout's manifest says
  `'installable': False` — directory presence is not availability. Third-party
  trees (e.g. muk-it/odoo-modules `18.0`) ship ports with the flag off until
  the vendor blesses a release; the `analyze` command's availability verdict
  checks directory presence only and cannot see this.
- Uninstalling modules stuck in `to upgrade` requires normalizing their state
  to `installed` first (they never completed the upgrade, but they are
  effectively running the previous version's code); the ORM uninstall then
  removes them cleanly.
- The strict config schema requires `target_version > current_version`, so a
  *completed* migration keeps `current_version` one hop behind in the YAML and
  the state file (`states/<DB>.state.yaml`) carries the final version — that
  is the designed truth source (`status` shows the "(yaml stale)" marker).