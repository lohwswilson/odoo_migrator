# Install / replace modules.
#
# Runs INSIDE `odoo-bin shell` (piped via stdin by odoo_migrator.odoo.shell()).
# Env vars:
#   PW_MIGRATOR_INSTALL      comma-separated modules to install
#   PW_MIGRATOR_REPLACE_OLD  comma-separated old modules (uninstalled after new are in)
#   PW_MIGRATOR_REPLACE_NEW   comma-separated replacement modules (installed first)
# `env` is provided by the odoo shell.

import os

_install = [m.strip() for m in os.environ.get("PW_MIGRATOR_INSTALL", "").split(",") if m.strip()]
_replace_old = [m.strip() for m in os.environ.get("PW_MIGRATOR_REPLACE_OLD", "").split(",") if m.strip()]
_replace_new = [m.strip() for m in os.environ.get("PW_MIGRATOR_REPLACE_NEW", "").split(",") if m.strip()]


def _install_one(name):
    rec = env['ir.module.module'].search([('name', '=', name)], limit=1)
    if not rec:
        print("om: FAILED not found: %s" % name)
        raise RuntimeError("module not found: %s" % name)
    if rec.state == 'installed':
        print("om: SKIP %s (already installed)" % name)
        return
    try:
        rec.button_immediate_install()
        print("om: INSTALLED %s" % name)
    except Exception as e:
        print("om: FAILED %s: %s" % (name, e))
        raise


def _uninstall_one(name):
    rec = env['ir.module.module'].search([('name', '=', name)], limit=1)
    if not rec or rec.state != 'installed':
        print("om: SKIP uninstall %s (absent or not installed)" % name)
        return
    try:
        rec.button_immediate_uninstall()
        print("om: UNINSTALLED %s" % name)
    except Exception as e:
        print("om: FAILED uninstall %s: %s" % (name, e))
        raise


for _name in _install + _replace_new:
    _install_one(_name)
for _name in _replace_old:
    _uninstall_one(_name)

env.cr.commit()
print("om: install/replace done")