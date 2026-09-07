import pytest


@pytest.fixture
def sample_config_dict():
    return {
        "name": "MYDB",
        "full_name": "My Sample Database",
        "databases": {
            16: "mydb_16",
            17: "mydb_17",
            18: "mydb_18",
        },
        "current_version": 16,
        "target_version": 18,
        "hops": {
            "16_to_17": {
                "modules_to_remove": ["crm"],
                "auto_install_false": ["mail"],
                "pre_sql": ["SELECT 1;"],
                "post_copy_sql": ["SELECT 2;"],
                "modules_to_reinstall": [],
                "modules_to_replace": {"sale_old": "sale_new"},
                "notes": "Hop 16 to 17",
            },
            "17_to_18": {
                "modules_to_remove": [],
                "auto_install_false": [],
                "notes": "Hop 17 to 18",
            },
        },
        "notes": "Sample test config",
    }

