from odoo_migrator.logparse import parse_log


def test_parse_log_clean(tmp_path):
    log_file = tmp_path / "migration.log"
    log_file.write_text(
        "2026-09-07 14:20:00,100 1234 INFO mydb odoo.modules.loading: loading 50 modules...\n"
        "2026-09-07 14:20:01,200 1234 INFO mydb openupgrade: running migration for account\n"
        "2026-09-07 14:20:02,300 1234 WARNING mydb odoo.models: field foo is deprecated\n"
    )

    report = parse_log(log_file, exit_code=0)
    assert report.ok is True
    assert len(report.criticals) == 0
    assert len(report.errors) == 0
    assert len(report.warnings) == 1
    assert len(report.ou_lines) == 1
    assert "field foo is deprecated" in report.warnings[0]
    assert "openupgrade: running migration" in report.ou_lines[0]


def test_parse_log_with_errors_and_criticals(tmp_path):
    log_file = tmp_path / "migration.log"
    log_file.write_text(
        "2026-09-07 14:20:00,100 1234 CRITICAL mydb odoo.service.server: Failed to initialize\n"
        "2026-09-07 14:20:01,200 1234 ERROR mydb odoo.sql_db: bad query\n"
        "Traceback (most recent call last):\n"
        "  File 'odoo/cli.py', line 10, in <module>\n"
    )

    report = parse_log(log_file, exit_code=1)
    assert report.ok is False
    assert len(report.criticals) == 1
    assert len(report.errors) == 2  # 1 standard ERROR + 1 Traceback line
    assert "Failed to initialize" in report.criticals[0]
    assert "bad query" in report.errors[0]
    assert "Traceback (most recent call last):" in report.errors[1]


def test_parse_log_sql_query_with_error_literal_ignored(tmp_path):
    """Ensure SQL queries with string literal 'ERROR' or column names don't fail log gate."""
    log_file = tmp_path / "migration.log"
    log_file.write_text(
        "2026-09-07 14:20:00,100 1234 INFO mydb odoo.sql_db: SELECT * FROM audit_log WHERE level = 'ERROR';\n"
        "2026-09-07 14:20:01,200 1234 INFO mydb odoo.sql_db: UPDATE queue SET error_count = error_count + 1;\n"
    )

    report = parse_log(log_file, exit_code=0)
    assert report.ok is True
    assert len(report.errors) == 0
    assert len(report.criticals) == 0


def test_parse_log_missing_file(tmp_path):
    missing_file = tmp_path / "does_not_exist.log"
    report = parse_log(missing_file)
    assert report.ok is False
    assert len(report.criticals) == 1
    assert "Log file missing" in report.criticals[0]

