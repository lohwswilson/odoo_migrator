from odoo_migrator.verify import verify_hop


def test_verify_hop_clean(monkeypatch):
    snapshot = {
        "modules": ["base", "web", "crm"],
        "tables": {"res_partner": 100, "res_users": 5, "crm_lead": 50},
        "model_modules": {
            "res_partner": "base",
            "res_users": "base",
            "crm_lead": "crm",
        },
    }

    # Simulation: crm removed cleanly, base tables preserved
    monkeypatch.setattr(
        "odoo_migrator.pg.installed_modules",
        lambda db: [{"name": "base"}, {"name": "web"}],
    )
    monkeypatch.setattr(
        "odoo_migrator.pg.table_counts",
        lambda db: {"res_partner": 100, "res_users": 6},
    )

    result = verify_hop("test_db", snapshot, expected_removed=["crm"])
    assert result.ok is True
    assert len(result.errors) == 0
    assert any("module removed as planned: crm" in info for info in result.info)
    assert any("table dropped with removed module ['crm']: crm_lead" in info for info in result.info)


def test_verify_hop_unintended_module_lost(monkeypatch):
    snapshot = {
        "modules": ["base", "web", "sale"],
        "tables": {"res_partner": 10, "sale_order": 5},
        "model_modules": {"res_partner": "base", "sale_order": "sale"},
    }

    # Simulation: sale was lost unintentionally
    monkeypatch.setattr(
        "odoo_migrator.pg.installed_modules",
        lambda db: [{"name": "base"}, {"name": "web"}],
    )
    monkeypatch.setattr(
        "odoo_migrator.pg.table_counts",
        lambda db: {"res_partner": 10, "sale_order": 5},
    )

    result = verify_hop("test_db", snapshot, expected_removed=[])
    assert result.ok is True  # Lost module is a warning (cascade warning), not an error
    assert any("module lost during hop (dependency cascade?): sale" in w for w in result.warnings)


def test_verify_hop_record_loss_in_retained_module_fails(monkeypatch):
    snapshot = {
        "modules": ["base", "web"],
        "tables": {"res_partner": 100},
        "model_modules": {"res_partner": "base"},
    }

    # Simulation: res_partner lost records (100 -> 80)
    monkeypatch.setattr(
        "odoo_migrator.pg.installed_modules",
        lambda db: [{"name": "base"}, {"name": "web"}],
    )
    monkeypatch.setattr(
        "odoo_migrator.pg.table_counts",
        lambda db: {"res_partner": 80},
    )

    result = verify_hop("test_db", snapshot, expected_removed=[])
    assert result.ok is False
    assert len(result.errors) == 1
    assert "RECORD LOSS in retained module ['base']: table shrank 100 -> 80: res_partner" in result.errors[0]


def test_verify_hop_shrinkage_in_removed_module_is_warning(monkeypatch):
    snapshot = {
        "modules": ["base", "crm"],
        "tables": {"crm_lead": 50},
        "model_modules": {"crm_lead": "crm"},
    }

    # Simulation: crm_lead shrank because crm was removed
    monkeypatch.setattr(
        "odoo_migrator.pg.installed_modules",
        lambda db: [{"name": "base"}],
    )
    monkeypatch.setattr(
        "odoo_migrator.pg.table_counts",
        lambda db: {"crm_lead": 20},
    )

    result = verify_hop("test_db", snapshot, expected_removed=["crm"])
    assert result.ok is True
    assert len(result.errors) == 0
    assert any("table shrank 50 -> 20: crm_lead (module ['crm'] was removed)" in w for w in result.warnings)

