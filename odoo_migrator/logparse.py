"""OpenUpgrade / Odoo log harvesting for odoo_migrator.

openupgrade_records analysis is retired upstream (verified: no such module in
the OpenUpgrade 17.0/18.0 checkouts), so the migration-log output of the
OpenUpgrade framework and openupgradelib is the record of what happened during
the hop. This module classifies it:

- CRITICAL lines          -> hop failure
- ERROR lines             -> hop failure (use --no-log-gate to downgrade)
- WARNING lines           -> collected for the report
- openupgrade-tagged lines -> collected as migration-relevant detail
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# Odoo log lines look like:
#   2026-09-07 14:22:01,123 123 ERROR mydb_17 odoo.module.module: ...
_LOG_LEVEL_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2},\d{3}\s+\d+\s+(?P<level>CRITICAL|ERROR|WARNING)\b"
)
_FALLBACK_ERR_RE = re.compile(r"^(?:CRITICAL|ERROR):\s+|Traceback \(most recent call last\):")
_OU_RE = re.compile(r"openupgrade", re.IGNORECASE)


@dataclass
class LogReport:
    log_path: Path | None
    exit_code: int | None
    criticals: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ou_lines: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.criticals and not self.errors

    def summary(self) -> dict:
        return {
            "log": str(self.log_path) if self.log_path else None,
            "exit_code": self.exit_code,
            "criticals": len(self.criticals),
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            "openupgrade_lines": len(self.ou_lines),
        }


def parse_log(log_path: Path, exit_code: int | None = None) -> LogReport:
    report = LogReport(log_path=log_path, exit_code=exit_code)
    if not log_path or not Path(log_path).exists():
        report.criticals.append(f"Log file missing: {log_path}")
        return report

    seen: set[int] = set()
    for line in Path(log_path).read_text(errors="replace").splitlines():
        key = hash(line)
        if key in seen:
            continue

        m = _LOG_LEVEL_RE.match(line)
        if m:
            lvl = m.group("level")
            if lvl == "CRITICAL":
                report.criticals.append(line.strip())
            elif lvl == "ERROR":
                report.errors.append(line.strip())
            elif lvl == "WARNING":
                report.warnings.append(line.strip())
            seen.add(key)
        elif _FALLBACK_ERR_RE.search(line):
            report.errors.append(line.strip())
            seen.add(key)

        if _OU_RE.search(line):
            report.ou_lines.append(line.strip())

    # Cap memory-heavy lists but keep counts honest in the summary.
    for attr in ("criticals", "errors", "warnings", "ou_lines"):
        items = getattr(report, attr)
        setattr(report, attr, items[:500])
    return report