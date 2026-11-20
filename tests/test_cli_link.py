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
    counts_sql = (
        'SELECT (SELECT count(*) FROM "Transactor"),'
        " (SELECT count(*) FROM transactor_detailed_view)"
    )
    [(transactors, view_rows)] = query(database_path, counts_sql)
    assert transactors == view_rows > 0

    # clean writes one more transactor
    transactor_file = data_directory / "cleaned" / "tx" / "Transactor.parquet"
    new_file = transactor_file.with_suffix(".new.parquet")
    con = connect_duckdb(":memory:")
    con.execute(
        f"""
        COPY (
            SELECT * FROM read_parquet('{transactor_file}')
            UNION ALL
            (
                SELECT * REPLACE ('new transactor' AS id)
                FROM read_parquet('{transactor_file}') LIMIT 1
            )
        ) TO '{new_file}' (FORMAT PARQUET)
        """
    )
    con.close()
    new_file.replace(transactor_file)
    link(data_directory, "--states", "tx")

    assert query(database_path, counts_sql) == [(transactors + 1, view_rows + 1)]


def test_a_state_with_no_cleaned_data_stops_the_run_before_dropping_anything(
    data_directory,
):
    """A --states value with no cleaned folder fails and keeps the database as it was

    Args:
        data_directory: Data folder with cleaned TX and MN data.
    """
    link(data_directory)
    database_path = data_directory / "linked" / DEFAULT_DATABASE_NAME
    before = query(database_path, 'SELECT count(*) FROM "Transactor"')[0][0]

    with pytest.raises(ValueError, match="zz"):
        link(data_directory, "--states", "tx", "zz")

    assert before > 0
    assert query(database_path, 'SELECT count(*) FROM "Transactor"') == [(before,)]


def test_reported_by_reaches_the_database(data_directory):
    """The column standardize sets survives normalize, clean and the load

    Args:
        data_directory: Data folder with cleaned TX and MN data.
    """
    link(data_directory)
    database_path = data_directory / "linked" / DEFAULT_DATABASE_NAME
    rows = query(
        database_path,
        'SELECT reported_state, reported_by, count(*) FROM "Transaction"'
        " GROUP BY 1, 2 ORDER BY 1, 2",
    )
    by_state = {}
    for state, reported_by, count in rows:
        by_state.setdefault(state, {})[reported_by] = count
    assert set(by_state) == {"mn", "tx"}
    for counts in by_state.values():
        assert set(counts) == {"donor", "recipient"}
        assert all(count > 0 for count in counts.values())


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
