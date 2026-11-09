"""Amounts keep their cents from clean through to DuckDB"""

from decimal import Decimal

import pandas as pd
import pytest
from utils.clean.columns import clean_table_columns
from utils.constants import DEFAULT_SCHEMA_PATH
from utils.database import _duckdb_type, connect_duckdb, create_or_append_parquet_to_db
from utils.schema import DataSchema, parse_decimal_type

# Float32 stored this as 12345679.0
LARGE_AMOUNT = 12345678.91


@pytest.fixture
def transaction_schema():
    """The repo's Transaction schema"""
    return DataSchema(DEFAULT_SCHEMA_PATH).schema["Transaction"]


def cleaned_transactions(transaction_schema, amounts):
    """A Transaction table with the given amounts, after clean's type step"""
    table = pd.DataFrame(
        {
            "amount": amounts,
            "donor_id": [f"d{i}" for i in range(len(amounts))],
            "recipient_id": [f"r{i}" for i in range(len(amounts))],
        }
    )
    return clean_table_columns(table, transaction_schema)


@pytest.mark.parametrize(
    "schema_type, expected",
    [
        ("decimal(18,2)", (18, 2)),
        ("DECIMAL( 14 , 2 )", (14, 2)),
        ("Float64", None),
        ("decimal", None),
    ],
)
def test_parse_decimal_type(schema_type, expected):
    assert parse_decimal_type(schema_type) == expected


def test_decimal_schema_type_becomes_a_duckdb_decimal():
    assert _duckdb_type("decimal(18,2)") == "DECIMAL(18,2)"
    assert _duckdb_type("Float64") == "DOUBLE"


def test_clean_keeps_cents_on_large_amounts(transaction_schema):
    table = cleaned_transactions(
        transaction_schema, [LARGE_AMOUNT, -17.85, None, 1.234]
    )
    assert str(table["amount"].dtype) == "Float64"
    assert table["amount"].iloc[0] == LARGE_AMOUNT
    assert table["amount"].iloc[1] == -17.85
    assert pd.isna(table["amount"].iloc[2])
    # rounded to cents, as DuckDB will store it
    assert table["amount"].iloc[3] == 1.23


def test_clean_fills_a_missing_amount_column_with_nulls(transaction_schema):
    table = clean_table_columns(
        pd.DataFrame({"donor_id": ["d"], "recipient_id": ["r"]}), transaction_schema
    )
    assert str(table["amount"].dtype) == "Float64"
    assert table["amount"].isna().all()


def test_clean_raises_on_amounts_too_large_for_the_type(transaction_schema):
    # DuckDB would otherwise refuse to load the whole table
    with pytest.raises(ValueError, match="1 values in 'amount' are too large"):
        cleaned_transactions(transaction_schema, [1.0, 1e16])


def test_duckdb_stores_amounts_as_exact_decimals(tmp_path, transaction_schema):
    amounts = [LARGE_AMOUNT, 0.1, 0.2, -17.85, None]
    parquet_path = tmp_path / "Transaction.parquet"
    cleaned_transactions(transaction_schema, amounts).to_parquet(parquet_path)

    con = connect_duckdb(tmp_path / "test.duckdb")
    try:
        create_or_append_parquet_to_db(con, parquet_path)
        (column_type,) = con.execute(
            "SELECT data_type FROM information_schema.columns"
            " WHERE table_name = 'Transaction' AND column_name = 'amount'"
        ).fetchone()
        stored = [
            row[0] for row in con.execute("SELECT amount FROM Transaction").fetchall()
        ]
        (total,) = con.execute("SELECT SUM(amount) FROM Transaction").fetchone()
    finally:
        con.close()

    assert column_type == "DECIMAL(18,2)"
    assert stored == [
        Decimal("12345678.91"),
        Decimal("0.10"),
        Decimal("0.20"),
        Decimal("-17.85"),
        None,
    ]
    # DECIMAL sums are exact (as floats, 0.1 + 0.2 is 0.30000000000000004)
    assert total == Decimal("12345661.36")
