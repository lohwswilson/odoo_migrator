# Uninstall modules with reverse dependency resolution.
#
# Runs INSIDE `odoo-bin shell` (piped via stdin by odoo_migrator.odoo.shell()).
# Reads the comma-separated module list from PW_MIGRATOR_MODULES.
# `env` is provided by the odoo shell. Prints om: markers for log parsing.

import os

_mods = [m.strip() for m in os.environ.get("PW_MIGRATOR_MODULES", "").split(",") if m.strip()]
if not _mods:
    print("om: no modules requested")
else:
    def _dependents(name):
        deps = env['ir.module.module.dependency'].search([
            ('name', '=', name),
            ('module_id.state', '=', 'installed'),
        ])
        return sorted({d.module_id.name for d in deps if d.module_id})

    _order, _seen = [], set()

    def _visit(name):
        if name in _seen:
            return
        _seen.add(name)
        for dep in _dependents(name):
            _visit(dep)
        _order.append(name)

    for _m in _mods:
        _visit(_m)

    print("om: uninstall order: " + ", ".join(_order))

    for _name in _order:
        _rec = env['ir.module.module'].search([('name', '=', _name)], limit=1)
        if not _rec:
            print("om: SKIP %s (absent)" % _name)
            continue
        if _rec.state in ('to upgrade', 'to install'):
            print("om: NORMALIZING %s state %s -> installed" % (_name, _rec.state))
            _rec.write({'state': 'installed'})
        elif _rec.state != 'installed':
            print("om: SKIP %s (state=%s)" % (_name, _rec.state))
            continue
        try:
            _rec.button_immediate_uninstall()
            print("om: UNINSTALLED %s" % _name)
        except Exception as _e:
            print("om: FAILED %s: %s" % (_name, _e))
            raise

    env.cr.commit()
    print("om: uninstall done")