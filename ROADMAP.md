# odoo_migrator Strategic Roadmap 🗺️

This document outlines the strategic vision, completed milestones, and future development tracks for **`odoo_migrator`**.

---

## 🎯 Strategic Vision

`odoo_migrator` is engineered as a **local-first, deterministic migration orchestrator** for the PerfectWork / ANSIS Odoo ecosystem. Its primary objective is to replace legacy ad-hoc scripts with an atomic, auditable, and reproducible N → N+1 pipeline that guarantees zero data loss across major Odoo version upgrades.

```
       Phase 1-2              Phase 3-4               Phase 5-6             Phase 7-9
 ┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐
 │ Architecture &   │──▶│ 14-Step Hop &    │──▶│ Dual-Gate Verify │──▶│ Automated Tests, │
 │ Isolated Shell   │   │ Addon Gap Engine │   │ & Resumable Chain│   │ Multi-DB & UI    │
 └──────────────────┘   └──────────────────┘   └──────────────────┘   └──────────────────┘
```

---

## 🚦 Phase Overview & Status

| Phase | Milestone | Focus Area | Status | Completed / Target |
|---|---|---|---|---|
| **Phase 1** | **V2 Foundation & Strict Typing** | Typer CLI, strict YAML validation, Odoo major version invariants | ✅ Complete | 2026-Q1 |
| **Phase 2** | **Subprocess & Shell Isolation** | `--no-http` execution, piped stdin shell scripts (`uninstall.py`, `install.py`) | ✅ Complete | 2026-Q2 |
| **Phase 3** | **Addons Gap Analysis Engine** | Accurate `get_modules()` parity, manifest scanning, draft YAML export | ✅ Complete | 2026-Q2 |
| **Phase 4** | **14-Step Hop Pipeline** | Atomic template cloning, backup integration, in-flight OpenUpgrade patching | ✅ Complete | 2026-Q3 |
| **Phase 5** | **Dual-Gate Verification** | Stream log parser (CRITICAL/ERROR), per-table count diff, record loss gate | ✅ Complete | 2026-Q3 |
| **Phase 6** | **Resumable Hop Chains** | State persistence (`.state.yaml`), stop-and-verify gates, `--auto` progression | ✅ Complete | 2026-Q3 |
| **Phase 7** | **Documentation Suite** | CStation parity: README, Architecture, Roadmap, Contributing, CLI manual | ✅ Complete | 2026-Q3 |
| **Phase 8** | **Automated Test Suite** | Pytest unit tests, CLI mock runners, synthetic migration fixtures | 📅 In Progress | 2026-Q4 |
| **Phase 9** | **Fleet & Multi-DB Batching** | Concurrent multi-database migration runner with resource throttling | 📅 Planned | 2027-Q1 |
| **Phase 10** | **Observability Web UI** | Rich web dashboard for table diff inspection and migration progress visualization | 📅 Planned | 2027-Q2 |

---

## 🔍 Detailed Track Roadmaps

### Track A: Core Pipeline & Orchestration Hardening

- [x] **Strict 14-Step Hop Orchestration**:
  - Deterministic sequence: pre-flight → snapshot → backup → uninstall → auto_install → pre-SQL → source update → DB copy → filestore copy → post-copy SQL → patch apply → OpenUpgrade run → log gate → reinstall → verify → state save.
- [x] **Atomic Template Database Copy**:
  - PostgreSQL `createdb -T` template copying for instant, transactionally safe branch creation.
- [x] **Guaranteed In-Flight Patch Cleanup**:
  - Temporary replacement of OpenUpgrade scripts with automated `.pw2.bak` restoration in `finally` blocks.
- [ ] **Automated Rollback Engine**:
  - Single-command rollback (`odoo-migrator rollback <DB>`) that drops failed target databases and restores original state cleanly.
- [ ] **Transaction Checkpointing**:
  - Resume interrupted hops from specific checkpoints rather than restarting from step 1.

---

### Track B: OpenUpgrade Engine & Addons Ecosystem

- [x] **Zero-HTTP Subprocess Runner**:
  - Execution via `odoo-bin` with `--no-http` and `--stop-after-init` to eliminate port 8069 contention.
- [x] **Addons Manifest Scanner**:
  - Accurate filesystem discovery following symlinks (`PW_ADDONS.18.0/ENTERPRISE -> 18.E`).
- [x] **Enterprise Modules Lifecycle**:
  - Systematic uninstall prior to hop 1 per ANSIS precedent, avoiding corrupted EE data structures.
- [ ] **Automated OpenUpgrade Patch Registry**:
  - Central repository of verified monkey-patches for upstream OpenUpgrade edge-cases.
- [ ] **Custom Module Migration Scaffolding**:
  - Helper to generate boilerplate `pre-migration.py` and `post-migration.py` scripts for proprietary modules.

---

### Track C: Data Verification & Telemetry

- [x] **Stream Log Classification**:
  - Real-time detection of `CRITICAL`, `ERROR`, `WARNING`, and `openupgrade:` markers.
- [x] **Exact Per-Table Count Diffing**:
  - Table-by-table record counting comparing post-hop state against baseline pre-hop snapshot.
- [x] **Model Owner Loss Detection**:
  - Flagging record shrinkage in tables owned by retained modules as hard migration failures.
- [ ] **Column-Level Schema Diffs**:
  - Deep inspection of added, altered, or dropped PostgreSQL column types across hops.
- [ ] **Data Integrity Assertion Hooks**:
  - Custom SQL assertions defined in YAML (e.g. `assert: "SELECT count(*) FROM account_move WHERE state='posted' > 0"`).

---

### Track D: Developer Tooling & Ergonomics

- [x] **Typer CLI Suite (14 Commands)**:
  - Rich CLI dashboards (`status`, `doctor`, `logs`, `verify`).
- [x] **Draft Config Generator (`analyze --save-draft`)**:
  - Automated drafting of `modules_to_remove` from target addon comparisons.
- [ ] **Interactive Migration Wizard**:
  - Interactive TUI prompt guiding operators through gap analysis and config generation.
- [ ] **CI/CD Integration**:
  - Headless test execution pipeline for testing customer database migrations against staging snapshots.

