from pathlib import Path

from odoo_migrator.analyze import _classify_root, index_modules, is_manifest_installable


def test_classify_root():
    assert _classify_root(Path("/opt/PW/addons")) == "core"
    assert _classify_root(Path("/opt/PW/OCA")) == "oca"
    assert _classify_root(Path("/opt/PW/ansis")) == "custom"
    assert _classify_root(Path("/opt/PW/ENTERPRISE")) == "enterprise"
    assert _classify_root(Path("/opt/PW/OpenUpgrade_17.0")) == "openupgrade"
    assert _classify_root(Path("/opt/PW/something_else")) == "other:something_else"


def test_is_manifest_installable(tmp_path):
    # Case 1: Explicit installable: False
    f1 = tmp_path / "m1.py"
    f1.write_text("{'name': 'm1', 'installable': False}")
    assert is_manifest_installable(f1) is False

    # Case 2: Explicit installable: True
    f2 = tmp_path / "m2.py"
    f2.write_text("{'name': 'm2', 'installable': True}")
    assert is_manifest_installable(f2) is True

    # Case 3: Default (no installable specified)
    f3 = tmp_path / "m3.py"
    f3.write_text("{'name': 'm3', 'version': '1.0'}")
    assert is_manifest_installable(f3) is True

    # Case 4: Non-literal / dynamic syntax with regex fallback
    f4 = tmp_path / "m4.py"
    f4.write_text(
        "# Some comment\n"
        "VERSION = '1.0'\n"
        "{\n"
        "    'name': 'm4',\n"
        "    'installable': False,\n"
        "    'version': VERSION,\n"
        "}\n"
    )
    assert is_manifest_installable(f4) is False


def test_index_modules(tmp_path):
    addons_dir = tmp_path / "custom_addons"
    addons_dir.mkdir()

    # Module A: valid & installable
    mod_a = addons_dir / "mod_a"
    mod_a.mkdir()
    (mod_a / "__manifest__.py").write_text("{'name': 'mod_a', 'installable': True}")

    # Module B: un-installable
    mod_b = addons_dir / "mod_b"
    mod_b.mkdir()
    (mod_b / "__manifest__.py").write_text("{'name': 'mod_b', 'installable': False}")

    # Non-module dir
    non_mod = addons_dir / "docs"
    non_mod.mkdir()

    # Symlinked module C: valid & installable
    outside_dir = tmp_path / "external_repo" / "mod_c"
    outside_dir.mkdir(parents=True)
    (outside_dir / "__manifest__.py").write_text("{'name': 'mod_c'}")
    mod_c_symlink = addons_dir / "mod_c"
    mod_c_symlink.symlink_to(outside_dir)

    indexed = index_modules([addons_dir], origin="custom")

    assert "mod_a" in indexed
    assert indexed["mod_a"] == "custom"
    assert "mod_b" not in indexed  # installable: False skipped
    assert "docs" not in indexed   # no manifest skipped
    assert "mod_c" in indexed     # symlinks followed
    assert indexed["mod_c"] == "custom"

