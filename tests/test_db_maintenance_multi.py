"""Tests for 462.8 multi-DB maintenance integration."""


def test_repair_database_all_reports_per_db():
    from src.db_maintenance_multi import repair_database_all

    result = repair_database_all()
    assert "results_per_db" in result
    assert "cross_db_consistent" in result
    assert "any_quarantined" in result


def test_get_db_info_all_reports_isolated():
    from src.db_maintenance_multi import get_db_info_all

    result = get_db_info_all()
    assert result.get("isolated") is True
    assert "per_db" in result
    assert "cross_db_table_counts" in result
