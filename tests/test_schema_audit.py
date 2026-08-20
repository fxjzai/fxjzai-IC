from sqlalchemy import UniqueConstraint

from ic.db import Base


def test_normative_entities_carry_schema_audit_fields() -> None:
    normative_tables = {
        "sources",
        "source_endpoints",
        "source_policies",
        "raw_documents",
        "items",
        "candidate_snapshots",
        "scores",
        "digests",
        "feedback",
    }

    for table_name in normative_tables:
        columns = Base.metadata.tables[table_name].columns
        assert "schema_version" in columns, table_name
        assert "created_at" in columns, table_name


def test_derived_products_carry_rebuild_audit_fields() -> None:
    derived_tables = {
        "candidate_snapshots",
        "scores",
        "digests",
        "feedback",
        "policy_proposals",
    }

    for table_name in derived_tables:
        columns = Base.metadata.tables[table_name].columns
        assert "producer" in columns, table_name
        assert "producer_version" in columns, table_name
        assert "input_ids" in columns, table_name
        assert "policy_version" in columns, table_name


def test_source_policy_version_is_unique_within_a_source() -> None:
    table = Base.metadata.tables["source_policies"]
    unique_column_sets = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("source_id", "version") in unique_column_sets


def test_source_endpoint_identity_and_operational_fields_match_the_contract() -> None:
    table = Base.metadata.tables["source_endpoints"]
    unique_column_sets = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("kind", "locator") in unique_column_sets
    assert {"credentials_ref", "priority", "last_observed_at"}.issubset(table.columns.keys())
