"""Ids are uuid5s, so the same data gets the same ids in every run and chunk"""

import shutil
import uuid
from pathlib import Path

import pandas as pd
import pytest
from utils.constants import DEFAULT_SCHEMA_PATH
from utils.ids import (
    UUID_REGEX,
    create_new_uuid_mapping,
    load_id_mapping,
    replace_null_ids_with_uuids,
    save_id_mapping,
    source_id_uuid,
)
from utils.normalize import Normalizer
from utils.standardize.source import DataSourceStandardizationPipeline

CONFIG_DIRECTORY = (
    Path(__file__).parents[1] / "src" / "utils" / "standardize" / "finance" / "config"
)
RAW_FIXTURE_DIRECTORY = Path(__file__).parent / "data" / "standardize" / "raw"


@pytest.fixture
def tx_transactions(tmp_path):
    """Standardize the Texas contribution fixtures, with one donor in both files

    The first contributor in each file has the same name and city, so
    normalize has to combine them into one Transactor by their attributes,
    since individual contributors have no source id.

    Args:
        tmp_path: Temporary directory for this test (pytest fixture).

    Returns:
        The standardized Transaction table.
    """
    state_directory = tmp_path / "tx"
    shutil.copytree(RAW_FIXTURE_DIRECTORY / "tx", state_directory)
    path = state_directory / "contribs_100.csv"
    path.write_text(path.read_text().replace("Laterperson0", "Testperson0"))
    return DataSourceStandardizationPipeline(
        state_code="tx",
        form_code="contributions",
        config_file=CONFIG_DIRECTORY / "tx.yaml",
    ).load_and_standardize_data_source(state_data_directory=state_directory)


def normalize(table: pd.DataFrame, id_mapping: dict | None = None) -> dict:
    """Normalize a copy of a Transaction table with a new Normalizer

    Args:
        table: Standardized Transaction table.
        id_mapping: Id mapping from an earlier run or chunk, if any.

    Returns:
        The normalized database, mapping table names to tables.
    """
    return Normalizer(
        {"Transaction": table.copy()}, DEFAULT_SCHEMA_PATH, id_mapping
    ).normalize_database()


def ids_by_source_row(database: dict) -> dict[tuple, tuple]:
    """Map each transaction's source file and row to its donor and recipient ids

    Args:
        database: Normalized database.

    Returns:
        {(source_file, source_row): (donor_id, recipient_id)}
    """
    transactions = database["Transaction"]
    return {
        (file, row): (donor, recipient)
        for file, row, donor, recipient in zip(
            transactions["source_file"],
            transactions["source_row"],
            transactions["donor_id"],
            transactions["recipient_id"],
        )
    }


def test_uuid_regex_accepts_uuid4_and_uuid5_but_not_raw_ids():
    """Both id versions count as uuids; raw ids do not"""
    assert UUID_REGEX.match(str(uuid.uuid4()))
    assert UUID_REGEX.match(source_id_uuid(("222", None, "TX", "Transactor")))
    assert not UUID_REGEX.match("222")
    assert not UUID_REGEX.match("00090001")


def test_source_id_uuid_ignores_how_the_raw_id_was_read():
    """222, 222.0 and "222" are the same raw id"""
    ids = {
        source_id_uuid((raw_id, 2024, "TX", "Transactor"))
        for raw_id in (222, 222.0, "222")
    }
    assert len(ids) == 1


@pytest.mark.parametrize(
    "other_key",
    [
        ("223", 2024, "TX", "Transactor"),
        ("222", 2023, "TX", "Transactor"),
        ("222", 2024, "PA", "Transactor"),
        ("222", 2024, "TX", "Election"),
    ],
)
def test_source_id_uuid_differs_by_id_year_state_and_table(other_key):
    """Changing any part of the key gives a different id

    Args:
        other_key: A key that differs from ("222", 2024, "TX", "Transactor")
            in one part.
    """
    assert source_id_uuid(("222", 2024, "TX", "Transactor")) != source_id_uuid(
        other_key
    )


def test_new_uuid_mapping_is_the_same_each_run():
    """Mapping the same raw ids twice gives the same uuids"""
    table = pd.DataFrame({"id": ["1", "2"], "reported_state": ["TX", "TX"]})
    first = create_new_uuid_mapping(table, "Transactor")
    second = create_new_uuid_mapping(table, "Transactor")
    assert first == second
    assert len(set(first.values())) == 2


def test_rows_without_ids_get_ids_from_their_values():
    """Equal rows share an id; other tables, values or columns change it"""
    table = pd.DataFrame(
        {
            "id": [None, None, None],
            "full_name": ["Jane", "Jane", "Alex"],
            "reported_state": ["TX", "TX", "TX"],
        }
    )
    replace_null_ids_with_uuids(table, "id", table_name="Transactor")
    assert table["id"][0] == table["id"][1] != table["id"][2]

    other_table = table.assign(id=None)
    replace_null_ids_with_uuids(other_table, "id", table_name="Election")
    assert other_table["id"][0] != table["id"][0]

    with_empty_column = table[["full_name", "reported_state"]].assign(
        id=None, email=None
    )
    replace_null_ids_with_uuids(with_empty_column, "id", table_name="Transactor")
    assert with_empty_column["id"].tolist() == table["id"].tolist()


def test_normalize_gives_the_same_ids_each_run(tx_transactions):
    """Normalizing the same data twice gives identical tables

    Args:
        tx_transactions: Standardized Texas contributions.
    """
    first = normalize(tx_transactions)
    second = normalize(tx_transactions)
    for table_name in ("Transaction", "Transactor"):
        pd.testing.assert_frame_equal(first[table_name], second[table_name])


def test_normalize_gives_the_same_ids_in_any_chunk(tx_transactions):
    """Each row gets the same ids whether its file is normalized alone or not

    Each file is normalized as its own chunk with no id mapping, as if run
    separately, so the donor in both files must get the same id from its
    attributes alone.

    Args:
        tx_transactions: Standardized Texas contributions.
    """
    whole = ids_by_source_row(normalize(tx_transactions))
    chunks = {}
    for _, chunk in tx_transactions.groupby("source_file"):
        chunks.update(ids_by_source_row(normalize(chunk)))
    assert chunks == whole
    # the contributor in both files is one donor
    assert whole[("contribs_01.csv", 1)][0] == whole[("contribs_100.csv", 1)][0]
    assert len({donor for donor, _ in whole.values()}) == len(whole) - 1


def test_saved_id_mapping_gives_the_same_ids(tmp_path, tx_transactions):
    """Re-running with the saved id mapping gives the ids of a fresh run

    The saved mapping used to read raw ids back as numbers, which missed the
    lookup and created new ids.

    Args:
        tmp_path: Temporary directory for this test (pytest fixture).
        tx_transactions: Standardized Texas contributions.
    """
    normalizer = Normalizer(
        {"Transaction": tx_transactions.copy()}, DEFAULT_SCHEMA_PATH
    )
    fresh = ids_by_source_row(normalizer.normalize_database())
    mapping_path = tmp_path / "id_mapping.tsv"
    save_id_mapping(normalizer.id_mapping, mapping_path)

    rerun_normalizer = Normalizer(
        {"Transaction": tx_transactions.copy()},
        DEFAULT_SCHEMA_PATH,
        load_id_mapping(mapping_path),
    )
    rerun = ids_by_source_row(rerun_normalizer.normalize_database())
    assert rerun == fresh

    # raw ids are read back as text, so every saved id is found and saving
    # again writes the same file instead of adding rows for the same ids
    first_save = mapping_path.read_bytes()
    save_id_mapping(rerun_normalizer.id_mapping, mapping_path)
    assert mapping_path.read_bytes() == first_save


def test_id_mapping_with_uuid4_ids_is_refused(tmp_path):
    """A mapping saved before ids were uuid5s would mix old and new ids

    Args:
        tmp_path: Temporary directory for this test (pytest fixture).
    """
    mapping_path = tmp_path / "id_mapping.tsv"
    save_id_mapping(
        {("222", None, "tx", "Transactor"): str(uuid.uuid4())}, mapping_path
    )
    with pytest.raises(ValueError, match="not uuid5s"):
        load_id_mapping(mapping_path)


def test_raw_ids_keep_leading_zeros_when_loaded(tmp_path):
    """Raw ids such as 00090001 are read back as written, not as numbers

    Args:
        tmp_path: Temporary directory for this test (pytest fixture).
    """
    key = ("00090001", None, "tx", "Transactor")
    mapping_path = tmp_path / "id_mapping.tsv"
    save_id_mapping({key: source_id_uuid(key)}, mapping_path)
    assert load_id_mapping(mapping_path) == {key: source_id_uuid(key)}


def test_same_entity_in_another_state_gets_another_id(tx_transactions):
    """The same donor attributes reported by two states are two donors

    Normalize runs once per state, so the state must be part of a donor's id
    for the two states' donors to stay apart when their tables are combined.

    Args:
        tx_transactions: Standardized Texas contributions.
    """
    other_state = tx_transactions.copy()
    other_state["reported_state"] = "pa"
    texas_ids = ids_by_source_row(normalize(tx_transactions))
    other_ids = ids_by_source_row(normalize(other_state))
    assert texas_ids.keys() == other_ids.keys()
    for key, (donor, _) in texas_ids.items():
        assert other_ids[key][0] != donor
