"""Post-hop verification for odoo_migrator.

Layers:
1. Module-set diff   — installed modules now vs pre-hop snapshot.
2. Record-count diff — exact per-table counts now vs snapshot; record LOSS in
   tables owned by modules that were NOT on the removal list is an ERROR;
   drops/shrinkage attributable to removed modules are warnings.

(openupgrade_records analysis is retired upstream — log harvesting in
logparse.py covers the "what did OpenUpgrade do" dimension.)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from . import pg, state as state_mod

logger = logging.getLogger(__name__)


@dataclass
class VerifyResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> dict:
        return {"errors": len(self.errors), "warnings": len(self.warnings), "info": len(self.info)}


def _owners(table: str, model_modules: dict[str, str]) -> set[str]:
    mods = model_modules.get(table, "")
    return {m.strip() for m in mods.split(",") if m.strip()}


def verify_hop(target_db: str, snapshot: dict, expected_removed: list[str]) -> VerifyResult:
    result = VerifyResult()
    removed = set(expected_removed)

    now_installed = {m["name"] for m in pg.installed_modules(target_db)}
    snap_modules = set(snapshot.get("modules", []))

    lost = sorted(snap_modules - now_installed)
    for name in lost:
        if name in removed:
            result.info.append(f"module removed as planned: {name}")
        else:
            result.warnings.append(
                f"module lost during hop (dependency cascade?): {name}"
            )
    gained = sorted(now_installed - snap_modules)
    for name in gained:
        result.info.append(f"module added during hop: {name}")

    snap_counts: dict[str, int] = snapshot.get("tables", {})
    model_modules: dict[str, str] = snapshot.get("model_modules", {})
    now_counts = pg.table_counts(target_db)

    dropped = sorted(set(snap_counts) - set(now_counts))
    for table in dropped:
        owners = _owners(table, model_modules)
        if owners & removed:
            result.info.append(
                f"table dropped with removed module {sorted(owners & removed)}: {table}"
            )
        else:
            result.warnings.append(f"table dropped (no removed module owns it): {table}")

    shrunk = 0
    for table, before in snap_counts.items():
        after = now_counts.get(table)
        if after is None or after >= before:
            continue
        shrunk += 1
        owners = _owners(table, model_modules)
        detail = f"table shrank {before} -> {after}: {table}"
        if owners & removed:
            result.warnings.append(f"{detail} (module {sorted(owners & removed)} was removed)")
        elif not owners:
            result.warnings.append(f"{detail} (owning module unknown)")
        else:
            result.errors.append(f"RECORD LOSS in retained module {sorted(owners)}: {detail}")

    result.info.insert(0, f"{len(now_installed)} installed modules, {len(now_counts)} tables")

    return result


def verify_version(db_name: str, version: int, *, name: str | None = None) -> VerifyResult:
    """Standalone check of a database against its stored snapshot (if any)."""
    result = VerifyResult()
    snapshot = state_mod.load_snapshot(name or db_name, version) if name else None
    if not snapshot:
        result.warnings.append(
            f"No stored snapshot for {name or db_name} v{version} — basic checks only"
        )
        installed = pg.installed_modules(db_name)
        result.info.append(f"{len(installed)} installed modules on {db_name}")
        return result
    # Compare against snapshot with nothing expected removed
    return verify_hop(db_name, snapshot, expected_removed=[])