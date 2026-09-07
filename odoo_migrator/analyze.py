"""Module gap analysis: what's installed on the source DB vs what exists in the
target versions' addon trees.

Discovery mirrors Odoo's own `get_modules()` exactly (odoo/modules/module.py):
ONE level per addons_path entry, `os.path.isfile(<entry>/__manifest__.py)` —
which follows symlinks. This matters in this workspace: PW_ADDONS.{6,7,18}.0/
ENTERPRISE are symlinks to the {16,17,18}.E checkouts and customers/ vendors
some EE modules via symlinks — Odoo discovers all of them, so the analyzer
must too (depth-3 globs over- and under-report; `find` without -L misses
symlinked trees entirely).

Enterprise modules found on a hop path are *available as code*, but OpenUpgrade
ships no migration scripts for them (community project) — EE data does not
migrate. Precedent (SEQ/ANSIS hops) uninstalls EE before the first hop, so EE
modules are drafted as first-hop removals with an explanatory verdict.
"""

from __future__ import annotations

import configparser
import logging
from dataclasses import dataclass, field
from pathlib import Path

from . import config, pg

logger = logging.getLogger(__name__)

_OCA_MARKERS = ("OCA", "oca")


def read_addons_paths(version: int) -> list[Path]:
    """Parse addons_path from the version's odoo.conf ([options] section)."""
    conf = config.odoo_conf(version)
    if not conf.exists():
        return []
    parser = configparser.ConfigParser()
    try:
        parser.read(conf)
    except configparser.Error as e:
        logger.warning("Cannot parse %s: %s", conf, e)
        return []
    raw = parser.get("options", "addons_path", fallback="")
    return [Path(p.strip()) for p in raw.split(",") if p.strip()]


def _classify_root(root: Path) -> str:
    name = root.name
    if name in _OCA_MARKERS:
        return "oca"
    if name in ("ansis", "perfectwork", "customers", "ai", "extra-addons",
                "moneta_finance", "openeducat", "ansis_conductor"):
        return "custom"
    if name == "ENTERPRISE":
        return "enterprise"
    if name.startswith("OpenUpgrade"):
        return "openupgrade"
    if name == "addons":
        return "core"
    return f"other:{name}"


def index_modules(paths: list[Path], *, origin: str | None = None) -> dict[str, str]:
    """{module_name: origin} — one level per path entry, symlinks followed.

    Exactly what Odoo's get_modules() would discover (listdir + isfile).
    """
    index: dict[str, str] = {}
    for root in paths:
        if not root.exists():
            logger.debug("Skipping missing addons path: %s", root)
            continue
        label = origin or _classify_root(root)
        try:
            entries = list(root.iterdir())
        except (NotADirectoryError, PermissionError) as e:
            logger.warning("Cannot list %s: %s", root, e)
            continue
        for entry in entries:
            if (entry / "__manifest__.py").is_file():
                index.setdefault(entry.name, label)
    return index


def source_index(version: int) -> dict[str, str]:
    """All modules the source version can see. odoo/addons first (core priority)."""
    paths = [config.version_dir(version) / "odoo" / "addons"]
    paths += read_addons_paths(version)
    idx = index_modules(paths)
    # top-level {v}.E checkout — classification only (may or may not be on path)
    ent = config.enterprise_dir(version)
    if ent.exists():
        for name in index_modules([ent]):
            idx.setdefault(name, "enterprise")
    return idx


def target_index(version: int) -> dict[str, str]:
    """Modules the target version can actually load: odoo/addons + conf paths."""
    paths = [config.version_dir(version) / "odoo" / "addons"]
    paths += read_addons_paths(version)
    return index_modules(paths)


def has_openupgrade_script(version: int, module: str) -> bool:
    return (config.openupgrade_scripts(version) / module).exists()


@dataclass
class ModuleRow:
    name: str
    state: str
    latest_version: str
    origin: str
    availability: dict[int, str] = field(default_factory=dict)
    ou_scripts: dict[int, bool] = field(default_factory=dict)
    verdict: str = ""


def _first_missing(row: ModuleRow) -> int | None:
    for v in sorted(row.availability):
        if row.availability[v] == "-":
            return v
    return None


@dataclass
class AnalyzeReport:
    db: str
    from_ver: int
    to_ver: int
    rows: list[ModuleRow] = field(default_factory=list)

    def removals_per_hop(self) -> dict[tuple[int, int], list[str]]:
        """{ (from, to): [modules to uninstall before this hop] }.

        - a module absent from the target path goes on the hop INTO its first
          missing version;
        - enterprise-origin modules go on the FIRST hop: their code may exist
          on the hop paths (ENTERPRISE symlinks) but OpenUpgrade cannot migrate
          EE data — SEQ/ANSIS precedent uninstalls them before hopping.
        """
        out: dict[tuple[int, int], list[str]] = {}
        first = (self.from_ver, self.from_ver + 1)
        for hop_from in range(self.from_ver, self.to_ver):
            hop_to = hop_from + 1
            out[(hop_from, hop_to)] = sorted(
                r.name for r in self.rows
                if (
                    (r.origin == "enterprise" and (hop_from, hop_to) == first)
                    or (r.availability.get(hop_to) == "-" and _first_missing(r) == hop_to)
                )
            )
        return out

    def draft_yaml(self) -> str:
        removals = self.removals_per_hop()
        lines = [f"# Draft hop config — generated by odoo-migrator analyze "
                 f"({self.db}, {self.from_ver} -> {self.to_ver})",
                 "# Review, then merge into databases/{name}.yaml 'hops:' section.", "hops:"]
        for (a, b), mods in sorted(removals.items()):
            lines.append(f"  {a}_to_{b}:")
            if mods:
                lines.append("    modules_to_remove:")
                lines.extend(f"      - {m}" for m in mods)
            else:
                lines.append("    modules_to_remove: []")
            lines.append("    notes: |")
            lines.append("      (add manual pre/post steps, module renames, etc.)")
            lines.append("")
        return "\n".join(lines)


def analyze(cfg, from_ver: int | None = None, to_ver: int | None = None) -> AnalyzeReport:
    """Compare installed modules on the source DB against target trees."""
    from_ver = from_ver or cfg.current_version
    to_ver = to_ver or cfg.target_version
    source_db = cfg.db_name(from_ver)

    installed = pg.installed_modules(source_db)
    src_idx = source_index(from_ver)

    targets: dict[int, dict[str, str]] = {
        v: target_index(v) for v in range(from_ver + 1, to_ver + 1)
    }

    report = AnalyzeReport(db=source_db, from_ver=from_ver, to_ver=to_ver)
    for mod in installed:
        row = ModuleRow(
            name=mod["name"],
            state=mod["state"],
            latest_version=mod["latest_version"],
            origin=src_idx.get(mod["name"], "NOT-IN-SOURCE-TREE"),
        )
        for v in sorted(targets):
            idx = targets[v]
            row.availability[v] = idx.get(mod["name"], "-")
            row.ou_scripts[v] = has_openupgrade_script(v, mod["name"])

        if row.origin == "NOT-IN-SOURCE-TREE":
            row.verdict = "GHOST: installed but code not in source addons trees"
        elif row.origin == "enterprise":
            row.verdict = (
                "ENTERPRISE — OpenUpgrade cannot migrate EE data; "
                "uninstall before first hop (SEQ/ANSIS precedent)"
            )
        else:
            first_missing = _first_missing(row)
            if first_missing is None:
                row.verdict = "OK"
            else:
                row.verdict = f"missing in v{first_missing} — uninstall before {first_missing - 1}->{first_missing}"
        report.rows.append(row)

    return report