"""Tests for the clean module"""

import pandas as pd
import pytest
from utils.clean.address import clean_address, clean_zipcode
from utils.clean.columns import clean_table_columns
from utils.clean.transactor import (
    _clean_name_components,
    clean_individuals_names,
    clean_phone_number,
    clean_transactors,
    divide_full_name_nameparser,
    fill_in_transactor_types,
)
from utils.constants import DEFAULT_SCHEMA_PATH
from utils.database import connect_duckdb, create_or_append_parquet_to_db
from utils.schema import DataSchema


@pytest.mark.parametrize(
    "full_name, expected",
    [
        (
            "John Doe",
            {
                "first_name": "John",
                "middle_name": None,
                "last_name": "Doe",
                "name_suffix": None,
                "name_preferred": None,
                "name_prefix": None,
            },
        ),
        (
            "John Doe Jr.",
            {
                "first_name": "John",
                "middle_name": None,
                "last_name": "Doe",
                "name_suffix": "Jr",
                "name_preferred": None,
                "name_prefix": None,
            },
        ),
        (
            "John Doe, Jr.",
            {
                "first_name": "John",
                "middle_name": None,
                "last_name": "Doe",
                "name_suffix": "Jr",
                "name_preferred": None,
                "name_prefix": None,
            },
        ),
        (
            "Mr. John Doe",
            {
                "first_name": "John",
                "middle_name": None,
                "last_name": "Doe",
                "name_suffix": None,
                "name_preferred": None,
                "name_prefix": "Mr",
            },
        ),
        (
            "Mr John Doe Jr.",
            {
                "first_name": "John",
                "middle_name": None,
                "last_name": "Doe",
                "name_suffix": "Jr",
                "name_preferred": None,
                "name_prefix": "Mr",
            },
        ),
        (
            "Dr. John Doe, Jr.",
            {
                "first_name": "John",
                "middle_name": None,
                "last_name": "Doe",
                "name_suffix": "Jr",
                "name_preferred": None,
                "name_prefix": "Dr",
            },
        ),
        (
            "Mr. John Doe, Jr. (John)",
            {
                "first_name": "John",
                "middle_name": None,
                "last_name": "Doe",
                "name_suffix": "Jr",
                "name_preferred": "John",
                "name_prefix": "Mr",
            },
        ),
        (
            "John Doe, Jr",
            {
                "first_name": "John",
                "middle_name": None,
                "last_name": "Doe",
                "name_suffix": "Jr",
                "name_preferred": None,
                "name_prefix": None,
            },
        ),
        (
            "John Doe Jr",
            {
                "first_name": "John",
                "middle_name": None,
                "last_name": "Doe",
                "name_suffix": "Jr",
                "name_preferred": None,
                "name_prefix": None,
            },
        ),
        (
            "Rev. Dr. Martin 'MLK' Luther King, Jr.",
            {
                "first_name": "Martin",
                "middle_name": "Luther",
                "last_name": "King",
                "name_suffix": "Jr",
                "name_preferred": "MLK",
                "name_prefix": "Rev Dr",
            },
        ),
        (
            "Doe, John",
            {
                "first_name": "John",
                "middle_name": None,
                "last_name": "Doe",
                "name_suffix": None,
                "name_preferred": None,
                "name_prefix": None,
            },
        ),
        (
            "Doe, John Jr.",
            {
                "first_name": "John",
                "middle_name": None,
                "last_name": "Doe",
                "name_suffix": "Jr",
                "name_preferred": None,
                "name_prefix": None,
            },
        ),
        (
            "Doe, John, Jr. (John)",
            {
                "first_name": "John",
                "middle_name": None,
                "last_name": "Doe",
                "name_suffix": "Jr",
                "name_preferred": "John",
                "name_prefix": None,
            },
        ),
    ],
)
def test_divide_full_name(full_name, expected):
    assert divide_full_name_nameparser(full_name) == expected


PEOPLE_WITH_ORGANIZATION_LIKE_LETTERS = [
    "Vincent Price",  # inc
    "Abraham Lincoln",  # inc
    "Harry Sinclair",  # inc
    "Al Corpuz",  # corp
    "Bob Partyka",  # party
    "Jane Spacek",  # pac
    "Ann Unionville",  # union
    "Pat Fundy",  # fund
]
ORGANIZATIONS = [
    "Acme Inc.",
    "Widgets LLC",
    "Friends of Bob PAC",
    "Smith & Associates",
    "Texas Democratic Party",
    "Citizens for Clean Water",
    "Local 150 Union",
]


def transactors(full_names: list[str], transactor_type: str | None) -> pd.DataFrame:
    """Build a Transactor table with the given names and one type for all

    Args:
        full_names: A full name for each row.
        transactor_type: The type every row starts with, or None.

    Returns:
        A table with full_name, last_name (empty) and transactor_type.
    """
    return pd.DataFrame(
        {
            "full_name": full_names,
            "last_name": None,
            "transactor_type": pd.Series(
                [transactor_type] * len(full_names), dtype=object
            ),
        }
    )


def test_people_are_not_labelled_organizations_by_letters_inside_their_names():
    """Patterns match whole words, so Vincent and Corpuz stay unknown"""
    table = fill_in_transactor_types(
        transactors(PEOPLE_WITH_ORGANIZATION_LIKE_LETTERS, None)
    )
    assert set(table["transactor_type"]) == {"Unknown"}
    assert not table["transactor_type_inferred"].any()


def test_missing_types_are_inferred_from_organization_words():
    """Names with an organization word get Organization, flagged as inferred"""
    table = fill_in_transactor_types(transactors(ORGANIZATIONS, None))
    assert set(table["transactor_type"]) == {"Organization"}
    assert table["transactor_type_inferred"].all()


@pytest.mark.parametrize("supplied_type", ["Individual", "Organization"])
def test_a_type_the_state_supplied_is_never_changed(supplied_type):
    """Supplied types are kept whatever the name looks like

    Args:
        supplied_type: The type the state gave every row.
    """
    names = PEOPLE_WITH_ORGANIZATION_LIKE_LETTERS + ORGANIZATIONS
    table = fill_in_transactor_types(transactors(names, supplied_type))
    assert set(table["transactor_type"]) == {supplied_type}
    assert not table["transactor_type_inferred"].any()


NAME_COLUMNS = [
    "full_name",
    "first_name",
    "middle_name",
    "last_name",
    "name_prefix",
    "name_suffix",
    "name_preferred",
]


def names_table(rows: list[dict]) -> pd.DataFrame:
    """Build a names table, with missing name parts as None

    Args:
        rows: One dict of name parts per row.

    Returns:
        A table with every column in NAME_COLUMNS.
    """
    return pd.DataFrame(rows, columns=NAME_COLUMNS).astype(object)


def name_parts(table: pd.DataFrame, columns: list[str]) -> list[dict]:
    """List some name parts per row, with missing values as None

    Args:
        table: Names table.
        columns: Columns to list.

    Returns:
        One dict per row.
    """
    values = table[columns].astype(object)
    return values.where(values.notna(), None).to_dict("records")


def test_supplied_name_parts_are_kept_and_only_the_missing_one_filled():
    """A supplied first name isn't split up when only the last name is missing"""
    table = clean_individuals_names(
        names_table(
            [
                {"full_name": "Mary Ann Smith", "first_name": "Mary Ann"},
                {"full_name": "Smith, John Q", "last_name": "Smith-Jones"},
            ]
        )
    )
    assert name_parts(table, ["first_name", "middle_name", "last_name"]) == [
        {"first_name": "Mary Ann", "middle_name": None, "last_name": "Smith"},
        {"first_name": "John", "middle_name": None, "last_name": "Smith-Jones"},
    ]


def test_all_missing_parts_are_filled_when_first_and_last_are_missing():
    """With no first or last name, the full name fills every missing part"""
    table = clean_individuals_names(
        names_table([{"full_name": "Dr. Jane Q. Public Jr.", "name_prefix": "Rev."}])
    )
    assert name_parts(table, NAME_COLUMNS[1:]) == [
        {
            "first_name": "Jane",
            "middle_name": "Q",
            "last_name": "Public",
            "name_prefix": "Rev.",  # supplied, so kept
            "name_suffix": "Jr",
            "name_preferred": None,
        }
    ]


def test_accented_letters_are_kept():
    """Parsing keeps letters in any alphabet and curly apostrophes"""
    table = clean_individuals_names(
        names_table([{"full_name": "José Nuñez-García"}, {"full_name": "Zoë O’Brien"}])
    )
    assert name_parts(table, ["first_name", "last_name"]) == [
        {"first_name": "José", "last_name": "Nuñez-García"},
        {"first_name": "Zoë", "last_name": "O’Brien"},
    ]


def test_digits_and_symbols_are_dropped_from_parsed_name_parts():
    """Digits and symbols are removed, and a part left empty becomes None"""
    assert _clean_name_components(
        {"first_name": "Ann3", "last_name": "O'Neil_!", "name_suffix": "42"}
    ) == {"first_name": "Ann", "last_name": "O'Neil", "name_suffix": None}


def test_parsed_address_parts_fill_gaps_without_replacing_supplied_values():
    """Supplied city, state and zip are kept; unit and PO box reach the schema"""
    addresses = pd.DataFrame(
        {
            "full_address": [
                "123 N Main St Apt 4B, Chicago, IL 60601",
                "PO Box 77, Springfield, IL 62701-1234",
            ],
            "line_1": None,
            "line_2": None,
            "city": ["CHICAGO", None],
            "state": [None, None],
            "zipcode": ["60602", None],
        }
    )
    cleaned = clean_address(addresses)
    columns = [
        "city",
        "state",
        "zipcode",
        "street_name",
        "secondary_address_identifier",
        "secondary_address_number",
        "po_box",
    ]
    values = cleaned[columns].astype(object)
    assert values.where(values.notna(), None).to_dict("records") == [
        {
            "city": "CHICAGO",  # supplied
            "state": "IL",  # parsed, written like states write it
            "zipcode": "60602",  # supplied, though the address says 60601
            "street_name": "main",
            "secondary_address_identifier": "apartment",
            "secondary_address_number": "4b",
            "po_box": None,
        },
        {
            "city": "Springfield",
            "state": "IL",
            "zipcode": "62701",
            "street_name": None,
            "secondary_address_identifier": None,
            "secondary_address_number": None,
            "po_box": "77",
        },
    ]


def test_rows_sharing_an_id_keep_values_from_every_row():
    """A phone number on the second row for an id isn't thrown away"""
    table = pd.DataFrame(
        {
            "id": ["u1", "u1"],
            "full_name": ["Jane Doe", "Jane Doe"],
            "first_name": [None, None],
            "middle_name": None,
            "last_name": None,
            "name_prefix": None,
            "name_suffix": None,
            "name_preferred": None,
            "transactor_type": ["Individual", "Individual"],
            "phone_number": [None, "312-555-1212"],
        }
    )
    cleaned = clean_transactors(table)
    assert cleaned[["id", "phone_number", "first_name"]].to_dict("records") == [
        {"id": "u1", "phone_number": "3125551212", "first_name": "Jane"}
    ]


def test_win_reads_true_false_and_missing_values_correctly(tmp_path):
    """ElectionResult.win keeps False and nulls through clean and DuckDB

    Args:
        tmp_path: Temporary directory for this test (pytest fixture).
    """
    schema = DataSchema(DEFAULT_SCHEMA_PATH).schema["ElectionResult"]
    results = pd.DataFrame(
        {
            "id": ["a", "b", "c", "d", "e", "f"],
            "candidate_id": "c1",
            "election_id": "e1",
            "win": pd.Series(["False", None, "TRUE", "n", 1, False], dtype=object),
        }
    )
    cleaned = clean_table_columns(results, schema)
    assert str(cleaned["win"].dtype) == "boolean"
    expected = [False, None, True, False, True, False]
    assert (
        cleaned["win"].astype(object).where(cleaned["win"].notna(), None).tolist()
        == expected
    )

    parquet_path = tmp_path / "ElectionResult.parquet"
    cleaned.to_parquet(parquet_path)
    con = connect_duckdb(tmp_path / "test.duckdb")
    try:
        create_or_append_parquet_to_db(con, parquet_path)
        stored = con.execute("SELECT win FROM ElectionResult ORDER BY id").fetchall()
    finally:
        con.close()
    assert [row[0] for row in stored] == expected


def test_unreadable_booleans_become_null_with_a_warning():
    """Text that isn't true or false is reported, not read as True"""
    schema = DataSchema(DEFAULT_SCHEMA_PATH).schema["ElectionResult"]
    results = pd.DataFrame(
        {"id": ["a"], "candidate_id": "c1", "election_id": "e1", "win": ["maybe"]}
    )
    with pytest.warns(UserWarning, match="1 values in 'win'.*'maybe'"):
        cleaned = clean_table_columns(results, schema)
    assert cleaned["win"].isna().all()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (3125551212.0, "3125551212"),
        ("3125551212.0", "3125551212"),
        ("(312) 555-1212", "3125551212"),
        ("", None),
        ("n/a", None),
        (None, None),
    ],
)
def test_phone_numbers_keep_only_their_own_digits(raw, expected):
    """A phone read as a number doesn't gain a digit from its ".0"

    Args:
        raw: Phone number as read.
        expected: Cleaned phone number.
    """
    assert clean_phone_number(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (501, "00501"),
        ("501", "00501"),
        (1234, "01234"),
        (60601.0, "60601"),
        ("60601-1234", "60601"),
        (606011234.0, "60601"),
        (" 60601 ", "60601"),
        ("12", None),
        ("ABCDE", None),
        (None, None),
    ],
)
def test_zips_that_lost_leading_zeros_are_padded(raw, expected):
    """Zips read as numbers get their leading zeros back

    Args:
        raw: Zip code as read.
        expected: Cleaned zip code.
    """
    assert clean_zipcode(raw) == expected
