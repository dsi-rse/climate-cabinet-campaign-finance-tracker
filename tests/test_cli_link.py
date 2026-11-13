"""cft link loads the current cleaned data, for the requested states only

Linkage itself is covered in test_link_predict.py; these tests replace it
with a stub and check how the command builds the database.
"""

import shutil
from pathlib import Path
from unittest import mock

import pytest
from utils.cli.core import DEFAULT_DATABASE_NAME, main
from utils.database import connect_duckdb

RAW_FIXTURE_DIRECTORY = Path(__file__).parent / "data" / "standardize" / "raw"
STATES = ("tx", "mn")


@pytest.fixture(scope="module")
def cleaned_directory(tmp_path_factory):
    """Run standardize, normalize and clean on the TX and MN fixtures

    Args:
        tmp_path_factory: Makes temporary directories (pytest fixture).

    Returns:
        The data folder, with cleaned/tx and cleaned/mn.
    """
    data_directory = tmp_path_factory.mktemp("data")
    for state in STATES:
        shutil.copytree(RAW_FIXTURE_DIRECTORY / state, data_directory / "raw" / state)
    common = ["--states", *STATES, "--data-directory", str(data_directory)]
    for step in ("standardize", "normalize", "clean"):
        assert main([step, *common]) == 0
    return data_directory


@pytest.fixture
def data_directory(cleaned_directory, tmp_path):
    """Copy the cleaned data so a test can change it

    Args:
        cleaned_directory: Data folder with cleaned TX and MN data.
        tmp_path: Temporary directory for this test (pytest fixture).

    Returns:
        A data folder with its own copy of cleaned/.
    """
    shutil.copytree(cleaned_directory / "cleaned", tmp_path / "cleaned")
    return tmp_path


def link(data_directory: Path, *options: str) -> mock.MagicMock:
    """Run cft link with linkage replaced by a stub

    Args:
        data_directory: The data folder.
        *options: More command line options.

    Returns:
        The stub, to check how linkage was called.
    """
    with mock.patch("utils.cli.core.run_linkage_pipeline") as pipeline:
        assert (
            main(
                [
                    "link",
                    "--data-directory",
                    str(data_directory),
                    "--model-path",
                    str(data_directory / "model.json"),
                    *options,
                ]
            )
            == 0
        )
    return pipeline


def query(database_path: Path, sql: str) -> list[tuple]:
    """Run a query on a database file

    Args:
        database_path: The database file.
        sql: Query to run.

    Returns:
        The rows.
    """
    con = connect_duckdb(database_path)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_database_defaults_to_the_linked_folder(data_directory):
    """With no --database-path, link builds linked/campaign_finance.duckdb

    Args:
        data_directory: Data folder with cleaned TX and MN data.
    """
    pipeline = link(data_directory)
    database_path = data_directory / "linked" / DEFAULT_DATABASE_NAME
    assert pipeline.call_args.kwargs["duckdb_path"] == database_path
    tables = {row[0] for row in query(database_path, "SHOW TABLES")}
    assert {"Transaction", "Transactor", "transactor_detailed_view"} <= tables
    assert (data_directory / "linked" / "_run_manifest.json").exists()


def test_new_cleaned_data_is_loaded_on_the_next_run(data_directory):
    """Re-running link after clean uses the new rows, not the old database

    Args:
        data_directory: Data folder with cleaned TX and MN data.
    """
    link(data_directory, "--states", "tx")
    database_path = data_directory / "linked" / DEFAULT_DATABASE_NAME
    before = query(database_path, 'SELECT count(*) FROM "Transaction"')[0][0]

    (data_directory / "cleaned" / "tx" / "Transaction.parquet").unlink()
    link(data_directory, "--states", "tx")

    assert before > 0
    assert query(database_path, "SHOW TABLES").count(("Transaction",)) == 0


def test_only_the_requested_states_are_loaded(data_directory):
    """--states limits which cleaned folders go into the database

    Args:
        data_directory: Data folder with cleaned TX and MN data.
    """
    link(data_directory, "--states", "mn")
    database_path = data_directory / "linked" / DEFAULT_DATABASE_NAME
    states = query(
        database_path, 'SELECT DISTINCT lower(reported_state) FROM "Transaction"'
    )
    assert states == [("mn",)]


def test_a_table_that_fails_to_load_stops_the_run(data_directory):
    """A broken parquet file fails the step and names the file

    Args:
        data_directory: Data folder with cleaned TX and MN data.
    """
    (data_directory / "cleaned" / "tx" / "Address.parquet").write_bytes(b"not parquet")
    with pytest.raises(RuntimeError, match=r"tx/Address\.parquet"):
        link(data_directory)


def test_linkage_history_is_kept_unless_overwrite(data_directory):
    """Reloading keeps earlier linkage runs; --overwrite drops them

    Args:
        data_directory: Data folder with cleaned TX and MN data.
    """
    link(data_directory)
    database_path = data_directory / "linked" / DEFAULT_DATABASE_NAME
    con = connect_duckdb(database_path)
    con.execute("CREATE TABLE linkage_run AS SELECT 'earlier run' AS run_id")
    con.close()

    link(data_directory)
    assert query(database_path, "SELECT run_id FROM linkage_run") == [("earlier run",)]

    link(data_directory, "--overwrite")
    assert ("linkage_run",) not in query(database_path, "SHOW TABLES")
