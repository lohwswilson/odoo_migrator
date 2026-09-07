import pytest
import yaml

from odoo_migrator.dbcfg import (
    ConfigError,
    DatabaseConfig,
    list_database_configs,
    load_database_config,
)


def test_valid_config(sample_config_dict, tmp_path):
    cfg_path = tmp_path / "MYDB.yaml"
    cfg = DatabaseConfig(sample_config_dict, cfg_path)
    cfg.validate()

    assert cfg.name == "MYDB"
    assert cfg.full_name == "My Sample Database"
    assert cfg.db_name(16) == "mydb_16"
    assert cfg.db_name(17) == "mydb_17"
    assert cfg.db_name(18) == "mydb_18"
    assert cfg.versions_between(16, 18) == [16, 17]
    assert cfg.get_hop(16, 17)["modules_to_remove"] == ["crm"]


def test_unmapped_db_name_raises(sample_config_dict, tmp_path):
    cfg_path = tmp_path / "MYDB.yaml"
    cfg = DatabaseConfig(sample_config_dict, cfg_path)
    with pytest.raises(ConfigError, match="no database name mapped for Odoo 19"):
        cfg.db_name(19)


def test_unknown_top_level_key(sample_config_dict, tmp_path):
    data = dict(sample_config_dict)
    data["unexpected_key"] = 123
    cfg = DatabaseConfig(data, tmp_path / "MYDB.yaml")
    with pytest.raises(ConfigError, match="unknown top-level keys"):
        cfg.validate()


def test_invalid_target_version(sample_config_dict, tmp_path):
    data = dict(sample_config_dict)
    data["target_version"] = 16
    cfg = DatabaseConfig(data, tmp_path / "MYDB.yaml")
    with pytest.raises(ConfigError, match="target_version .* must be greater than current_version"):
        cfg.validate()


def test_hop_target_not_plus_one(sample_config_dict, tmp_path):
    data = dict(sample_config_dict)
    data["hops"] = {"16_to_18": {}}
    cfg = DatabaseConfig(data, tmp_path / "MYDB.yaml")
    with pytest.raises(ConfigError, match="target must be source \\+ 1"):
        cfg.validate()


def test_hop_version_not_in_databases(sample_config_dict, tmp_path):
    data = dict(sample_config_dict)
    data["databases"] = {16: "db16", 18: "db18"}
    data["hops"] = {"16_to_17": {}}
    cfg = DatabaseConfig(data, tmp_path / "MYDB.yaml")
    with pytest.raises(ConfigError, match="version 17 is not in `databases`"):
        cfg.validate()


def test_hop_unknown_key(sample_config_dict, tmp_path):
    data = dict(sample_config_dict)
    data["hops"] = {"16_to_17": {"invalid_hop_key": True}}
    cfg = DatabaseConfig(data, tmp_path / "MYDB.yaml")
    with pytest.raises(ConfigError, match="unknown keys \\['invalid_hop_key'\\]"):
        cfg.validate()


def test_hop_str_list_validation(sample_config_dict, tmp_path):
    data = dict(sample_config_dict)
    data["hops"] = {"16_to_17": {"modules_to_remove": [123]}}
    cfg = DatabaseConfig(data, tmp_path / "MYDB.yaml")
    with pytest.raises(ConfigError, match="'modules_to_remove' must be a list of strings"):
        cfg.validate()


def test_load_config_and_list(tmp_path, monkeypatch, sample_config_dict):
    monkeypatch.setattr("odoo_migrator.dbcfg.databases_dir", lambda: tmp_path)

    # Missing config
    with pytest.raises(ConfigError, match="No config file"):
        load_database_config("NONEXISTENT")

    # Valid config file
    cfg_file = tmp_path / "MYDB.yaml"
    cfg_file.write_text(yaml.safe_dump(sample_config_dict))

    # Draft config file
    draft_file = tmp_path / "OTHER.draft.yaml"
    draft_file.write_text(yaml.safe_dump(sample_config_dict))

    configs = list_database_configs()
    assert "MYDB" in configs
    assert "OTHER.draft" not in configs

    loaded = load_database_config("MYDB")
    assert loaded.name == "MYDB"


def test_load_config_name_mismatch(tmp_path, monkeypatch, sample_config_dict):
    monkeypatch.setattr("odoo_migrator.dbcfg.databases_dir", lambda: tmp_path)
    cfg_file = tmp_path / "DIFFERENT_NAME.yaml"
    cfg_file.write_text(yaml.safe_dump(sample_config_dict))

    with pytest.raises(ConfigError, match="config 'name' is 'MYDB' but file is DIFFERENT_NAME.yaml"):
        load_database_config("DIFFERENT_NAME")

