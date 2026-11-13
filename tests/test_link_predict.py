"""Linkage records what it merged and never changes the source tables"""

import random
from unittest import mock

import duckdb
import pandas as pd
import pytest
from utils.database import connect_duckdb
from utils.link import predict, train
from utils.provenance import sha256_file

FIRST_NAMES = ["ann", "bob", "cat", "dan", "eve", "fay", "gus", "hal", "ivy", "jon"]
LAST_NAMES = ["smith", "jones", "lee", "khan", "garcia", "nguyen", "brown"]
CITIES = ["chicago", "austin", "reno", "denver"]
STREETS = ["main st", "oak ave", "elm rd", "pine ct", "lake dr"]
# people reported twice under different ids, so linkage should merge them
DUPLICATED_PEOPLE = 40
THRESHOLD = 0.9
# organizations added to the linkage database: id, name, state
ORGANIZATIONS = [
    ("o1", "Acme Inc.", "tx"),
    ("o2", "ACME, Inc", "tx"),
    ("o3", "The Acme Corporation", "tx"),
    ("o4", "Acme Inc", "pa"),  # another state
    ("o5", "Inc.", "tx"),  # nothing left to compare once "Inc" is dropped
    ("o6", "Acme Widgets LLC", "tx"),  # a different name
]
TRANSACTOR_COUNT = 300 + DUPLICATED_PEOPLE + len(ORGANIZATIONS)


def fake_transactors() -> pd.DataFrame:
    """Build fake transactors, some reported twice under different ids

    Returns:
        A Transactor table with the columns the linkage model compares.
    """
    rng = random.Random(0)  # noqa: S311 (fake test data, not security)
    people = [
        {
            "transactor_type": "Individual",
            "first_name": rng.choice(FIRST_NAMES),
            # a distinct surname per person, so only the copies are true matches
            "last_name": f"{rng.choice(LAST_NAMES)}{index}",
            "address_city": rng.choice(CITIES),
            "address_street_name": rng.choice(STREETS),
            "name_suffix": None,
            "name_prefix": None,
            "employer_full_name": rng.choice(["acme", "globex", "initech", None]),
            "employer_role": rng.choice(["ceo", "clerk", None]),
            "reported_state": "tx",
        }
        for index in range(300)
    ]
    rows = people + [dict(person) for person in people[:DUPLICATED_PEOPLE]]
    table = pd.DataFrame(rows)
    table.insert(0, "id", [f"t{i:03d}" for i in range(len(table))])
    table["full_name"] = table["first_name"] + " " + table["last_name"]
    # text type even for all-empty columns, which DuckDB would read as numbers
    return table.astype("string")


@pytest.fixture(scope="module")
def model_path(tmp_path_factory):
    """Train a linkage model on the fake transactors

    Args:
        tmp_path_factory: Makes temporary directories (pytest fixture).

    Returns:
        Path to the trained model's JSON settings.
    """
    directory = tmp_path_factory.mktemp("model")
    con = duckdb.connect()
    con.register("df", fake_transactors())
    con.execute("CREATE TABLE Transactor AS SELECT * FROM df")
    path = directory / "model.json"
    train.train_splink(con, "Transactor", path)
    con.close()
    return path


@pytest.fixture
def database_path(tmp_path):
    """Build a DuckDB file with Transactor and Transaction tables

    Args:
        tmp_path: Temporary directory for this test (pytest fixture).

    Returns:
        Path to the database.
    """
    path = tmp_path / "linked.duckdb"
    organizations = pd.DataFrame(
        ORGANIZATIONS, columns=["id", "full_name", "reported_state"]
    ).assign(transactor_type="Organization")
    transactors = pd.concat([fake_transactors(), organizations], ignore_index=True)
    transactors = transactors.astype("string")
    transactions = pd.DataFrame(
        {
            "donor_id": transactors["id"],
            "recipient_id": "committee",
            "amount": 10.0,
        }
    )
    con = connect_duckdb(path)
    con.register("transactors", transactors)
    con.register("transactions", transactions)
    con.execute("CREATE TABLE Transactor AS SELECT * FROM transactors")
    con.execute('CREATE TABLE "Transaction" AS SELECT * FROM transactions')
    con.close()
    return path


def read(path, sql: str) -> pd.DataFrame:
    """Run a query on a database file

    Args:
        path: The database file.
        sql: Query to run.

    Returns:
        The result.
    """
    con = connect_duckdb(path)
    try:
        return con.execute(sql).fetchdf()
    finally:
        con.close()


def run_linkage(database_path, model_path) -> str:
    """Run the linkage pipeline on the Transactor table

    Args:
        database_path: The database file.
        model_path: The trained model.

    Returns:
        The run's id.
    """
    return predict.run_linkage_pipeline(
        duckdb_path=database_path,
        model_path=model_path,
        threshold=THRESHOLD,
        table_name="Transactor",
    )


def test_source_tables_are_unchanged(database_path, model_path):
    """Linkage leaves Transactor and Transaction exactly as they were

    Args:
        database_path: Database with Transactor and Transaction tables.
        model_path: The trained model.
    """
    before = {
        name: read(database_path, f'SELECT * FROM "{name}" ORDER BY ALL')
        for name in ("Transactor", "Transaction")
    }
    run_linkage(database_path, model_path)
    for name, table in before.items():
        after = read(database_path, f'SELECT * FROM "{name}" ORDER BY ALL')
        pd.testing.assert_frame_equal(after, table)


def test_run_is_recorded_with_its_model_and_threshold(database_path, model_path):
    """linkage_run says when, with what model and threshold, and by what code

    Args:
        database_path: Database with Transactor and Transaction tables.
        model_path: The trained model.
    """
    run_id = run_linkage(database_path, model_path)
    runs = read(database_path, "SELECT * FROM linkage_run")
    assert runs["run_id"].tolist() == [run_id]
    run = runs.iloc[0]
    assert run["threshold"] == THRESHOLD
    assert run["model_sha256"] == sha256_file(model_path)
    assert run["splink_version"]
    assert run["table_name"] == "Transactor"
    assert run["finished_at"] >= run["started_at"]


def test_duplicates_are_linked_and_each_merge_has_its_evidence(
    database_path, model_path
):
    """Every merged pair has a stored prediction above the threshold

    Args:
        database_path: Database with Transactor and Transaction tables.
        model_path: The trained model.
    """
    run_linkage(database_path, model_path)
    mapping = read(database_path, "SELECT * FROM linkage_mapping")
    canonical = dict(zip(mapping["old_id"], mapping["canonical_id"]))
    # every transactor is mapped, and the copies of a person share a cluster
    assert len(canonical) == TRANSACTOR_COUNT
    linked_copies = sum(
        canonical[f"t{i:03d}"] == canonical[f"t{300 + i:03d}"]
        for i in range(DUPLICATED_PEOPLE)
    )
    assert linked_copies == DUPLICATED_PEOPLE

    predictions = read(database_path, "SELECT * FROM linkage_prediction")
    assert (predictions["match_probability"] >= THRESHOLD).all()
    scored_pairs = {
        frozenset(pair) for pair in zip(predictions["id_l"], predictions["id_r"])
    }
    for i in range(DUPLICATED_PEOPLE):
        assert frozenset({f"t{i:03d}", f"t{300 + i:03d}"}) in scored_pairs


def test_canonical_views_replace_ids_without_changing_tables(database_path, model_path):
    """Transaction_canonical has each donor's canonical id; Transaction keeps its own

    Args:
        database_path: Database with Transactor and Transaction tables.
        model_path: The trained model.
    """
    run_linkage(database_path, model_path)
    mapping = read(database_path, "SELECT * FROM linkage_mapping")
    canonical = dict(zip(mapping["old_id"], mapping["canonical_id"]))
    view = read(
        database_path,
        "SELECT t.donor_id AS source_id, c.donor_id AS canonical_id"
        ' FROM "Transaction" t POSITIONAL JOIN "Transaction_canonical" c',
    )
    assert view["canonical_id"].tolist() == [
        canonical[donor] for donor in view["source_id"]
    ]
    assert (view["source_id"] != view["canonical_id"]).sum() > 0


def test_each_run_is_kept_and_the_mapping_view_shows_the_latest(
    database_path, model_path
):
    """A second run adds rows instead of replacing or silently dropping them

    Args:
        database_path: Database with Transactor and Transaction tables.
        model_path: The trained model.
    """
    first = run_linkage(database_path, model_path)
    second = run_linkage(database_path, model_path)
    history = read(
        database_path,
        "SELECT run_id, count(*) AS n FROM linkage_mapping_history GROUP BY run_id",
    )
    assert dict(zip(history["run_id"], history["n"])) == {
        first: TRANSACTOR_COUNT,
        second: TRANSACTOR_COUNT,
    }
    latest = read(database_path, "SELECT count(*) AS n FROM linkage_mapping")
    assert latest["n"].iloc[0] == TRANSACTOR_COUNT
    assert read(database_path, "SELECT count(*) AS n FROM linkage_run")["n"][0] == 2


def test_failed_run_records_nothing(database_path, model_path):
    """A failure while recording leaves no partial run behind

    Args:
        database_path: Database with Transactor and Transaction tables.
        model_path: The trained model.
    """
    with (
        mock.patch.object(
            predict, "create_canonical_views", side_effect=RuntimeError("boom")
        ),
        pytest.raises(RuntimeError, match="boom"),
    ):
        run_linkage(database_path, model_path)
    for table in ("linkage_run", "linkage_prediction", "linkage_mapping_history"):
        count = read(database_path, f"SELECT count(*) AS n FROM {table}")["n"][0]
        assert count == 0, table


def test_splink_working_tables_are_dropped(database_path, model_path):
    """No __splink__ tables are left after a run

    Args:
        database_path: Database with Transactor and Transaction tables.
        model_path: The trained model.
    """
    run_linkage(database_path, model_path)
    tables = read(database_path, "SELECT table_name FROM information_schema.tables")
    assert not [name for name in tables["table_name"] if name.startswith("__splink__")]


def test_organizations_with_matching_names_in_one_state_are_linked(
    database_path, model_path
):
    """Organizations match on name, ignoring case, punctuation and "Inc"

    Args:
        database_path: Database with Transactor and Transaction tables.
        model_path: The trained model.
    """
    run_linkage(database_path, model_path)
    rows = read(
        database_path,
        "SELECT old_id, canonical_id, method FROM linkage_mapping_history"
        " WHERE starts_with(old_id, 'o') ORDER BY old_id",
    )
    linked = {
        old_id: (canonical_id, method if pd.notna(method) else None)
        for old_id, canonical_id, method in rows.itertuples(index=False)
    }
    assert linked == {
        "o1": ("o1", None),
        "o2": ("o1", "organization_name"),
        "o3": ("o1", "organization_name"),
        "o4": ("o4", None),
        "o5": ("o5", None),
        "o6": ("o6", None),
    }
