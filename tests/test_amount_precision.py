"""Amounts keep their cents from clean through to DuckDB"""

import re
import shutil
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest
import yaml
from utils.clean.columns import clean_table_columns
from utils.constants import DEFAULT_SCHEMA_PATH
from utils.database import _duckdb_type, connect_duckdb, create_or_append_parquet_to_db
from utils.schema import DataSchema, parse_decimal_type
from utils.standardize.config import resolve_inheritance
from utils.standardize.source import DataSourceStandardizationPipeline

# Float32 stored this as 12345679.0
LARGE_AMOUNT = 12345678.91
# Float32 stored this as 150000.0625, which rounds to the wrong cent
MID_AMOUNT = 150000.07

CONFIG_DIRECTORY = (
    Path(__file__).parents[1] / "src" / "utils" / "standardize" / "finance" / "config"
)
RAW_FIXTURE_DIRECTORY = Path(__file__).parent / "data" / "standardize" / "raw"
AMOUNT_COLUMN = re.compile(r"^amount(-\d+)?$")


@pytest.fixture
def transaction_schema():
    """Load the Transaction table schema from the repo's table.yaml

    Returns:
        The Transaction TableSchema.
    """
    return DataSchema(DEFAULT_SCHEMA_PATH).schema["Transaction"]


def cleaned_transactions(transaction_schema, amounts):
    """Build a Transaction table with the given amounts and run clean's type step

    Args:
        transaction_schema: The repo's Transaction table schema (fixture).
        amounts: Amount for each row. Each row gets its own donor and
            recipient id.

    Returns:
        The table after clean_table_columns.
    """
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
    """Decimal types parse to (precision, scale) and other types to None

    Args:
        schema_type: Type string as written in the schema YAML.
        expected: (precision, scale) the type should parse to, or None.
    """
    assert parse_decimal_type(schema_type) == expected


def test_decimal_schema_type_becomes_a_duckdb_decimal():
    """A decimal schema type maps to DECIMAL in DuckDB, others are unchanged"""
    assert _duckdb_type("decimal(18,2)") == "DECIMAL(18,2)"
    assert _duckdb_type("Float64") == "DOUBLE"


def test_clean_keeps_cents_on_large_amounts(transaction_schema):
    """Clean keeps large and negative amounts exact and rounds to cents

    Args:
        transaction_schema: The repo's Transaction table schema (fixture).
    """
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
    """A table with no amount column gets a Float64 column of nulls

    Args:
        transaction_schema: The repo's Transaction table schema (fixture).
    """
    table = clean_table_columns(
        pd.DataFrame({"donor_id": ["d"], "recipient_id": ["r"]}), transaction_schema
    )
    assert str(table["amount"].dtype) == "Float64"
    assert table["amount"].isna().all()


def test_clean_raises_on_amounts_too_large_for_the_type(transaction_schema):
    """Clean raises if an amount has too many digits for decimal(18,2)

    Args:
        transaction_schema: The repo's Transaction table schema (fixture).
    """
    # DuckDB would otherwise refuse to load the whole table
    with pytest.raises(ValueError, match="1 values in 'amount' are too large"):
        cleaned_transactions(transaction_schema, [1.0, 1e16])


def test_duckdb_stores_amounts_as_exact_decimals(tmp_path, transaction_schema):
    """Cleaned amounts load into DuckDB as exact DECIMAL(18,2) values and sums

    Args:
        tmp_path: Temporary directory for this test (pytest fixture).
        transaction_schema: The repo's Transaction table schema (fixture).
    """
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


def amount_column_types():
    """List every state config column that is standardized to an amount

    Reads each state config in CONFIG_DIRECTORY, resolves each form's
    inheritance, and keeps columns whose standard name is `amount` or
    `amount-N`.

    Yields:
        (state code, form name, raw column name, type) for each amount column.
    """
    for config_path in sorted(CONFIG_DIRECTORY.glob("*.yaml")):
        with config_path.open() as f:
            config = yaml.safe_load(f)
        for form in config:
            for column in resolve_inheritance(config, form).get("column_details", []):
                if AMOUNT_COLUMN.match(column.get("standard_name", "")):
                    yield config_path.stem, form, column["raw_name"], column["type"]


def test_state_configs_do_not_read_amounts_as_float32():
    """No state config reads an amount column as Float32"""
    # read_csv uses this type, so Float32 loses cents before clean can round
    float32_amounts = [
        column
        for column in amount_column_types()
        if str(column[3]).lower() == "float32"
    ]
    assert float32_amounts == []


def test_large_amounts_keep_their_cents_through_standardize_and_clean(
    tmp_path, transaction_schema
):
    """Large amounts in a raw Texas file come out of standardize and clean exact

    Args:
        tmp_path: Temporary directory for this test (pytest fixture).
        transaction_schema: The repo's Transaction table schema (fixture).
    """
    state_directory = tmp_path / "tx"
    shutil.copytree(RAW_FIXTURE_DIRECTORY / "tx", state_directory)
    contributions = state_directory / "contribs_01.csv"
    raw = pd.read_csv(contributions, dtype=str, keep_default_na=False)
    raw.loc[:1, "contributionAmount"] = [str(LARGE_AMOUNT), str(MID_AMOUNT)]
    raw.to_csv(contributions, index=False)

    standardized = DataSourceStandardizationPipeline(
        state_code="tx",
        form_code="contributions",
        config_file=CONFIG_DIRECTORY / "tx.yaml",
    ).load_and_standardize_data_source(state_data_directory=state_directory)
    cleaned = clean_table_columns(standardized, transaction_schema)

    assert {LARGE_AMOUNT, MID_AMOUNT} <= set(cleaned["amount"].dropna())
