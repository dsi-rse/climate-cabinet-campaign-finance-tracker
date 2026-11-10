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


def test_source_row_counts_malformed_and_empty_lines(tx_directory):
    """Lines read_csv skips still count, so later rows keep their line numbers

    TX reads with on_bad_lines: skip. A malformed line (one field too many)
    and an empty line come before the last two rows; without counting them,
    every later row would point at an earlier line of the file.

    Args:
        tx_directory: Editable copy of the Texas fixtures.
    """
    path = tx_directory / "contribs_01.csv"
    lines = path.read_text().splitlines()
    malformed = lines[1] + ",EXTRA"
    raw_lines = [lines[0], lines[1], malformed, "", *lines[2:]]
    path.write_text("\n".join(raw_lines) + "\n")

    table = standardize("tx", "contributions", tx_directory)
    rows = table.loc[table["source_file"] == "contribs_01.csv"]
    assert sorted(zip(rows["source_identifier"], rows["source_row"])) == [
        ("700", 1),
        ("701", 4),
        ("702", 5),
    ]
    # with a header, the raw line is line source_row + 1
    id_column = lines[0].split(",").index("contributionInfoId")
    for identifier, row in zip(rows["source_identifier"], rows["source_row"]):
        assert raw_lines[row].split(",")[id_column] == identifier


def test_source_row_counts_skipped_lines_in_a_file_with_no_header(tmp_path):
    """With header: null, skipped lines are counted from the file's first line

    PA's 2002 files have no header row and are read with on_bad_lines: warn.
    The record number of a skipped line is one less than in a file with a
    header, so it must not be corrected by a header row that isn't there.

    Args:
        tmp_path: Temporary directory for this test (pytest fixture).
    """
    pa_directory = tmp_path / "pa"
    shutil.copytree(RAW_FIXTURE_DIRECTORY / "pa", pa_directory)
    path = pa_directory / "2002" / "contrib_2002.txt"
    lines = path.read_text(encoding="latin-1").splitlines()
    raw_lines = [lines[0], lines[0] + ",EXTRA", *lines[1:]]
    path.write_text("\n".join(raw_lines) + "\n", encoding="latin-1")

    table = standardize("pa", "contributions_2002", pa_directory)
    assert set(table["source_file"]) == {"2002/contrib_2002.txt"}
    assert sorted(table["source_row"]) == [1, 3, 4]
    # with no header, the raw line is line source_row: each row points at a
    # different real record (the first field is the filer id), never at the
    # malformed line
    raw_records = [raw_lines[row - 1] for row in table["source_row"]]
    assert not any(record.endswith(",EXTRA") for record in raw_records)
    assert sorted(record.split(",")[0] for record in raw_records) == [
        "9900000",
        "9900001",
        "9900002",
    ]


def test_source_row_skipped_lines_in_warn_mode(tx_directory, monkeypatch, caplog):
    """on_bad_lines: warn counts skipped lines the same way and logs them

    Args:
        tx_directory: Editable copy of the Texas fixtures.
        monkeypatch: pytest fixture to switch the form to warn mode.
        caplog: pytest fixture holding log records.
    """
    path = tx_directory / "contribs_01.csv"
    lines = path.read_text().splitlines()
    raw_lines = [lines[0], lines[1], lines[2] + ",EXTRA", *lines[2:]]
    path.write_text("\n".join(raw_lines) + "\n")
    pipeline = DataSourceStandardizationPipeline(
        state_code="tx",
        form_code="contributions",
        config_file=CONFIG_DIRECTORY / "tx.yaml",
    )
    monkeypatch.setitem(pipeline.data_reader.read_csv_params, "on_bad_lines", "warn")

    table = pipeline.load_and_standardize_data_source(state_data_directory=tx_directory)
    rows = table.loc[table["source_file"] == "contribs_01.csv"]
    assert sorted(zip(rows["source_identifier"], rows["source_row"])) == [
        ("700", 1),
        ("701", 3),
        ("702", 4),
    ]
    assert "skipped 1 malformed lines (first: line 3)" in caplog.text


def test_renumbered_rows_raise():
    """A step that drops, adds or renumbers rows stops standardize"""
    pipeline = DataSourceStandardizationPipeline(
        state_code="tx",
        form_code="contributions",
        config_file=CONFIG_DIRECTORY / "tx.yaml",
    )
    raw_positions = pd.RangeIndex(3)
    renumbered = pd.DataFrame({"amount": [1.0, 2.0]})
    with pytest.raises(ValueError, match="renumbered"):
        pipeline._add_source_columns(renumbered, "f.csv", raw_positions)

    reordered = pd.DataFrame({"amount": [1.0, 2.0, 3.0]}, index=[2, 0, 1])
    result = pipeline._add_source_columns(reordered, "f.csv", raw_positions)
    assert result["source_row"].tolist() == [3, 1, 2]


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


@pytest.mark.parametrize("old_file_first", [True, False])
def test_older_files_without_source_columns_still_load(tmp_path, old_file_first):
    """A cleaned file written before the source columns loads with them empty

    Before, the missing column failed the load, and link printed the error
    and carried on without that state's transactions.

    Args:
        tmp_path: Temporary directory for this test (pytest fixture).
        old_file_first: Whether the older file creates the table.
    """
    transactions = standardize("tx", "contributions", RAW_FIXTURE_DIRECTORY / "tx")
    normalized = Normalizer(
        {"Transaction": transactions.copy()}, DEFAULT_SCHEMA_PATH
    ).normalize_database()
    cleaned = clean_data(normalized, DEFAULT_SCHEMA_PATH)["Transaction"]
    (tmp_path / "new").mkdir()
    (tmp_path / "old").mkdir()
    new_path = tmp_path / "new" / "Transaction.parquet"
    old_path = tmp_path / "old" / "Transaction.parquet"
    cleaned.to_parquet(new_path)
    cleaned.drop(columns=SOURCE_COLUMNS).to_parquet(old_path)

    con = connect_duckdb(tmp_path / "test.duckdb")
    try:
        for path in [old_path, new_path] if old_file_first else [new_path, old_path]:
            create_or_append_parquet_to_db(con, path)
        counts = con.execute(
            "SELECT count(*), count(source_row) FROM Transaction"
        ).fetchone()
    finally:
        con.close()

    assert counts == (2 * len(cleaned), len(cleaned))
