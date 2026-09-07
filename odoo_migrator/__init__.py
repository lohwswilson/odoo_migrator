"""odoo_migrator — OpenUpgrade migration orchestrator v2 for PerfectWork/ANSIS.

Design principles (v2):
- No XML-RPC. Every Odoo interaction is a short-lived `odoo-bin shell` subprocess
  with a piped script and `--no-http` (verified: odoo/cli/shell.py execs stdin in
  Odoo 16/17/18; --no-http exists in all three).
- Read-only state comes from plain SQL via psql — no Odoo needed.
- Configs are strictly validated (Odoo version numbers only, no silent fallbacks).
- Run state is persisted after every hop — chains are resumable.
- Every odoo-bin run is captured to a per-hop log file; OpenUpgrade output is
  parsed and used as a gate (openupgrade_records analysis is retired upstream,
  so log harvesting + module/record-count diffs are the verification stack).
"""

__version__ = "2.0.0"