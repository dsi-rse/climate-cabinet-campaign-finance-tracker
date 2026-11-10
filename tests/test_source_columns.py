"""Each transaction keeps the file, row and state id it came from

Standardize fills source_file and source_row for every Transaction form and
source_identifier where the state's config maps one. Normalize and clean keep them,
and DuckDB stores them, so any row in the final data can be traced back to the
line in the raw file it was read from.
"""

import shutil
from pathlib import Path

import pandas as pd
import pytest
from utils.clean import clean_data
from utils.constants import DEFAULT_SCHEMA_PATH
from utils.database import connect_duckdb, create_or_append_parquet_to_db
from utils.normalize import Normalizer
from utils.standardize.source import DataSourceStandardizationPipeline

CONFIG_DIRECTORY = (
    Path(__file__).parents[1] / "src" / "utils" / "standardize" / "finance" / "config"
)
RAW_FIXTURE_DIRECTORY = Path(__file__).parent / "data" / "standardize" / "raw"
SOURCE_COLUMNS = ["source_identifier", "source_file", "source_row"]


def standardize(state: str, form: str, state_data_directory: Path) -> pd.DataFrame:
    """Standardize one form of a state's raw files with the state's config

    Args:
        state: Two letter state code, such as "tx".
        form: Form name in the state's config.
        state_data_directory: Directory holding the state's raw files.

    Returns:
        The standardized table for every file the form matches.
    """
    return DataSourceStandardizationPipeline(
        state_code=state,
        form_code=form,
        config_file=CONFIG_DIRECTORY / f"{state}.yaml",
    ).load_and_standardize_data_source(state_data_directory=state_data_directory)


def source_columns(table: pd.DataFrame) -> list[tuple]:
    """List (source_identifier, source_file, source_row) for each row, sorted

    Args:
        table: Table with the three source columns.

    Returns:
        One tuple per row, with nulls as None, sorted by file and row.
    """
    values = table[SOURCE_COLUMNS].astype(object)
    rows = values.where(values.notna(), None).itertuples(index=False, name=None)
    return sorted(rows, key=lambda row: (row[1], row[2]))


@pytest.fixture
def tx_directory(tmp_path):
    """Copy the Texas fixture files to a directory a test can edit

    Args:
        tmp_path: Temporary directory for this test (pytest fixture).

    Returns:
        Path of the copied Texas directory.
    """
    state_directory = tmp_path / "tx"
    shutil.copytree(RAW_FIXTURE_DIRECTORY / "tx", state_directory)
    return state_directory


def test_texas_rows_keep_their_id_file_and_row():
    """TX contributions carry contributionInfoId, the file name and row number"""
    table = standardize("tx", "contributions", RAW_FIXTURE_DIRECTORY / "tx")
    assert source_columns(table) == [
        ("700", "contribs_01.csv", 1),
        ("701", "contribs_01.csv", 2),
        ("702", "contribs_01.csv", 3),
        ("1100", "contribs_100.csv", 1),
        ("1101", "contribs_100.csv", 2),
        ("1102", "contribs_100.csv", 3),
    ]


def test_arizona_access_export_keeps_transaction_id():
    """AZ Access transactions carry TransactionID as their source id"""
    table = standardize("az", "mdb_transactions", RAW_FIXTURE_DIRECTORY / "az")
    assert table["source_identifier"].notna().all()
    assert set(table["source_file"]) == {"AccessDatabase/Transactions.csv"}


@pytest.mark.parametrize(
    ("state", "form"),
    [
        ("mn", "contributions"),
        ("mi", "legacy_download_contributions_or_receipts"),
        ("pa", "contributions"),
        ("pa", "contributions_2002"),
    ],
)
def test_states_without_a_row_id_still_get_file_and_row(state, form):
    """Forms with no unique row id still record the file and row of each row

    Args:
        state: Two letter state code.
        form: Form name in the state's config.
    """
    table = standardize(state, form, RAW_FIXTURE_DIRECTORY / state)
    assert table["source_file"].notna().all()
    assert sorted(table["source_row"].unique()) == [1, 2, 3]
    assert ("source_identifier" not in table) or table["source_identifier"].isna().all()


def test_source_row_counts_dropped_rows(tx_directory):
    """Blank rows are dropped but still counted, so row numbers match the file

    Args:
        tx_directory: Editable copy of the Texas fixtures.
    """
    path = tx_directory / "contribs_01.csv"
    lines = path.read_text().splitlines()
    blank_row = "," * lines[0].count(",")
    path.write_text("\n".join([lines[0], lines[1], blank_row, *lines[2:]]) + "\n")

    table = standardize("tx", "contributions", tx_directory)
    rows = table.loc[table["source_file"] == "contribs_01.csv"]
    assert sorted(zip(rows["source_identifier"], rows["source_row"])) == [
        ("700", 1),
        ("701", 3),
        ("702", 4),
    ]


def test_transactor_tables_get_no_source_columns():
    """Only tables whose schema lists the source columns get them"""
    table = standardize("tx", "filers", RAW_FIXTURE_DIRECTORY / "tx")
    assert not set(SOURCE_COLUMNS) & set(table.columns)


def test_source_columns_reach_duckdb(tmp_path):
    """Source columns survive normalize and clean and load into DuckDB

    Args:
        tmp_path: Temporary directory for this test (pytest fixture).
    """
    transactions = standardize("tx", "contributions", RAW_FIXTURE_DIRECTORY / "tx")
    expected = source_columns(transactions)
    # normalize changes the tables it is given, so give it a copy
    normalized = Normalizer(
        {"Transaction": transactions.copy()}, DEFAULT_SCHEMA_PATH
    ).normalize_database()
    cleaned = clean_data(normalized, DEFAULT_SCHEMA_PATH)
    parquet_path = tmp_path / "Transaction.parquet"
    cleaned["Transaction"].to_parquet(parquet_path)

    con = connect_duckdb(tmp_path / "test.duckdb")
    try:
        create_or_append_parquet_to_db(con, parquet_path)
        stored = con.execute(
            "SELECT source_identifier, source_file, source_row FROM Transaction"
            " ORDER BY source_file, source_row"
        ).fetchall()
        column_types = dict(
            con.execute(
                "SELECT column_name, data_type FROM information_schema.columns"
                " WHERE table_name = 'Transaction' AND column_name LIKE 'source_%'"
            ).fetchall()
        )
    finally:
        con.close()

    # the state's ids come through unchanged, not replaced by uuids
    assert stored == expected
    assert [row[0] for row in stored] == ["700", "701", "702", "1100", "1101", "1102"]
    assert column_types == {
        "source_identifier": "VARCHAR",
        "source_file": "VARCHAR",
        "source_row": "BIGINT",
    }
