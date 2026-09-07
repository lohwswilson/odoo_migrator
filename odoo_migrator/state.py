"""Run-state persistence for odoo_migrator.

v1 updated `current_version` on an in-memory object and lost it at process
exit. v2 writes a per-database state file after every hop, so chains are
resumable and `status` reflects reality, not YAML claims.

Layout (under databases/states/):
  <DB>.state.yaml        — current version + hop history
  <DB>.snapshot.<v>.json — pre-hop snapshot (installed modules, table counts)
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .config import databases_dir


def states_dir() -> Path:
    d = databases_dir() / "states"
    d.mkdir(parents=True, exist_ok=True)
    return d


def state_path(name: str) -> Path:
    return states_dir() / f"{name}.state.yaml"


def snapshot_path(name: str, version: int) -> Path:
    return states_dir() / f"{name}.snapshot.{version}.json"


def load_state(name: str) -> dict[str, Any]:
    p = state_path(name)
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text()) or {}


def save_state(name: str, state: dict[str, Any]) -> None:
    p = state_path(name)
    p.write_text(yaml.safe_dump(state, sort_keys=False, allow_unicode=True))


def record_hop(
    name: str,
    from_ver: int,
    to_ver: int,
    *,
    ok: bool,
    log: str | None,
    verify_summary: dict[str, Any] | None,
) -> None:
    state = load_state(name)
    state.setdefault("db", name)
    state["history"] = state.get("history") or []
    state["history"].append(
        {
            "hop": f"{from_ver}_to_{to_ver}",
            "finished": datetime.now().isoformat(timespec="seconds"),
            "ok": ok,
            "log": log,
            "verify": verify_summary or {},
        }
    )
    if ok:
        state["current_version"] = to_ver
    save_state(name, state)


def current_version(name: str) -> int | None:
    """Version according to persisted run state (the truth), or None."""
    return load_state(name).get("current_version")


def save_snapshot(name: str, version: int, data: dict[str, Any]) -> Path:
    p = snapshot_path(name, version)
    p.write_text(json.dumps(data, indent=1, sort_keys=True))
    return p


def load_snapshot(name: str, version: int) -> dict[str, Any] | None:
    p = snapshot_path(name, version)
    if not p.exists():
        return None
    return json.loads(p.read_text())