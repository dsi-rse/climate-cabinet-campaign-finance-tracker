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
    use_lone_last_name_as_full_name,
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


def test_an_organization_word_in_the_last_name_alone_is_enough():
    """A state that gives only last_name "Acme Inc" still gets Organization"""
    table = fill_in_transactor_types(
        pd.DataFrame(
            {
                "full_name": [None, None],
                "last_name": ["Acme Inc", "Vincent"],
                "transactor_type": pd.Series([None, None], dtype=object),
            }
        )
    )
    assert table["transactor_type"].tolist() == ["Organization", "Unknown"]
    assert table["transactor_type_inferred"].tolist() == [True, False]


def test_a_supplied_first_name_makes_an_untyped_row_an_individual():
    """Michigan gives donors a first name and no type; organization words still win"""
    table = fill_in_transactor_types(
        pd.DataFrame(
            {
                "full_name": None,
                "first_name": ["NANCY", "Pat", None],
                "last_name": ["BEREZNICKI", "Acme Inc", "IOSCO COUNTY"],
                "transactor_type": pd.Series([None, None, None], dtype=object),
            }
        )
    )
    assert table["transactor_type"].tolist() == [
        "Individual",
        "Organization",
        "Unknown",
    ]
    assert table["transactor_type_inferred"].tolist() == [True, True, False]


def test_a_title_suffix_or_marker_after_the_last_name_is_peeled_off():
    """Michigan writes a title, suffix or " ^" marker after the last name"""
    table = clean_individuals_names(
        names_table(
            [
                {"last_name": "COBB ^", "first_name": "ANN"},
                {"last_name": "KOSKE MR.^", "first_name": "PAUL"},
                {"last_name": "WRIGHT JR", "first_name": "SAM"},
                {"last_name": "MC LARTY", "first_name": "JO"},
                {"last_name": "*BITTERMAN", "first_name": "LEE"},
                {"last_name": "KOSKE MR.^", "first_name": "PAUL", "name_prefix": "Dr"},
                # a one-word last name is kept even when it is a title word
                {"last_name": "JR", "first_name": "KIM"},
                {"last_name": " ^ ", "first_name": "KIM"},
            ]
        )
    )
    assert name_parts(table, ["last_name", "name_prefix", "name_suffix"]) == [
        {"last_name": "COBB", "name_prefix": None, "name_suffix": None},
        {"last_name": "KOSKE", "name_prefix": "MR", "name_suffix": None},
        {"last_name": "WRIGHT", "name_prefix": None, "name_suffix": "JR"},
        {"last_name": "MC LARTY", "name_prefix": None, "name_suffix": None},
        {"last_name": "BITTERMAN", "name_prefix": None, "name_suffix": None},
        # a supplied prefix is kept
        {"last_name": "KOSKE", "name_prefix": "Dr", "name_suffix": None},
        {"last_name": "JR", "name_prefix": None, "name_suffix": None},
        {"last_name": " ^ ", "name_prefix": None, "name_suffix": None},
    ]


def test_a_lone_last_name_is_the_whole_name_unless_the_record_is_a_person():
    """A last-name-or-organization column (MI, AZ) holds organizations' names"""
    table = use_lone_last_name_as_full_name(
        pd.DataFrame(
            {
                "transactor_type": [
                    "Unknown",
                    "Organization",
                    "Individual",
                    "Individual",
                    "Unknown",
                ],
                "first_name": [None, None, None, "ANN", None],
                "last_name": ["IOSCO COUNTY", "ACME INC", "SMITH", "COBB", "X"],
                "full_name": [None, None, None, None, "X Corp"],
            },
            dtype=object,
        )
    )
    assert table[["full_name", "last_name"]].to_dict("records") == [
        {"full_name": "IOSCO COUNTY", "last_name": None},
        {"full_name": "ACME INC", "last_name": None},
        # a person recorded by last name only keeps it
        {"full_name": None, "last_name": "SMITH"},
        {"full_name": None, "last_name": "COBB"},
        # a full name already given is kept
        {"full_name": "X Corp", "last_name": "X"},
    ]


def test_clean_transactors_types_then_moves_organization_names():
    """End to end for a state that gives donors a first name and no type"""
    table = clean_transactors(
        pd.DataFrame(
            {
                "id": ["1", "2", "3"],
                "transactor_type": pd.Series([None, None, None], dtype=object),
                "first_name": ["NANCY", None, None],
                "last_name": ["BEREZNICKI ^", "IOSCO COUNTY", "EXAMPLE INDUSTRY PAC"],
                "full_name": None,
                "middle_name": None,
                "name_prefix": None,
                "name_suffix": None,
                "name_preferred": None,
                "phone_number": None,
            }
        )
    ).sort_values("id")
    assert table[["transactor_type", "first_name", "last_name", "full_name"]].astype(
        object
    ).where(lambda t: t.notna(), None).to_dict("records") == [
        {
            "transactor_type": "Individual",
            "first_name": "NANCY",
            "last_name": "BEREZNICKI",
            "full_name": None,
        },
        {
            "transactor_type": "Unknown",
            "first_name": None,
            "last_name": None,
            "full_name": "IOSCO COUNTY",
        },
        {
            "transactor_type": "Organization",
            "first_name": None,
            "last_name": None,
            "full_name": "EXAMPLE INDUSTRY PAC",
        },
    ]


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


def test_a_full_name_put_in_the_last_name_field_is_split_up():
    """A comma last name with no first or full name is the whole name"""
    table = clean_individuals_names(names_table([{"last_name": "Public, Jane Q"}]))
    assert name_parts(
        table, ["full_name", "first_name", "middle_name", "last_name"]
    ) == [
        {
            "full_name": "Public, Jane Q",
            "first_name": "Jane",
            "middle_name": "Q",
            "last_name": "Public",
        }
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
    """Supplied city, state and zip are kept; unit and PO box reach the schema

    City and state come out in one case however the state wrote them, a
    supplied zip that isn't one is filled from the address, and an address
    with no city or state leaves them missing.
    """
    addresses = pd.DataFrame(
        {
            "full_address": [
                "123 N Main St Apt 4B, Chicago, IL 60601",
                "PO Box 77, Springfield, IL 62701-1234",
                None,
            ],
            "line_1": [None, None, "1 Example St"],
            "line_2": None,
            "city": ["CHICAGO", "springfield   ", None],
            "state": [None, "il", None],
            "zipcode": ["60602-1111", "N/A", None],
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
            "city": "Chicago",  # supplied, in one case
            "state": "IL",  # parsed
            "zipcode": "60602",  # supplied, though the address says 60601
            "street_name": "main",
            "secondary_address_identifier": "apartment",
            "secondary_address_number": "4b",
            "po_box": None,
        },
        {
            "city": "Springfield",  # supplied, stripped
            "state": "IL",  # supplied
            "zipcode": "62701",  # parsed, since "N/A" isn't a zip
            "street_name": None,
            "secondary_address_identifier": None,
            "secondary_address_number": None,
            "po_box": "77",
        },
        {
            "city": None,
            "state": None,
            "zipcode": None,
            "street_name": "example",
            "secondary_address_identifier": None,
            "secondary_address_number": None,
            "po_box": None,
        },
    ]


def test_a_zip_read_as_a_number_is_not_mistaken_for_a_unit_number():
    """With no city or state, a "2139.0" zip stays out of the unit columns"""
    addresses = pd.DataFrame(
        {
            "full_address": [None, None],
            "line_1": ["500 Elm Ave Apt 3", "PO Box 42"],
            "line_2": None,
            "city": None,
            "state": None,
            "zipcode": ["2139.0", "60601.0"],
        }
    ).astype("string")
    cleaned = clean_address(addresses)
    columns = ["zipcode", "secondary_address_number", "po_box"]
    values = cleaned[columns].astype(object)
    assert values.where(values.notna(), None).to_dict("records") == [
        {"zipcode": "02139", "secondary_address_number": "3", "po_box": None},
        {"zipcode": "60601", "secondary_address_number": None, "po_box": "42"},
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
            "id": list("abcdefghi"),
            "candidate_id": "c1",
            "election_id": "e1",
            # "1.0" is how a whole number written from a float column reads
            # back from CSV
            "win": pd.Series(
                ["False", None, "TRUE", "n", 1, False, "1.0", "0.0", " true "],
                dtype=object,
            ),
        }
    )
    cleaned = clean_table_columns(results, schema)
    assert str(cleaned["win"].dtype) == "boolean"
    expected = [False, None, True, False, True, False, True, False, True]
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


def test_impossible_dates_become_null_with_a_warning():
    """A date outside what a timestamp can hold doesn't stop the state

    Pennsylvania's 2024 file has a contribution dated 24201018 (year 2420).
    """
    import datetime

    schema = DataSchema(DEFAULT_SCHEMA_PATH).schema["Transaction"]
    transactions = pd.DataFrame(
        {
            "amount": [1.0, 2.0, 3.0],
            "donor_id": ["d1", "d2", "d3"],
            "recipient_id": ["r1", "r2", "r3"],
            "date": [datetime.date(2024, 10, 18), datetime.date(2420, 10, 18), None],
        }
    )
    with pytest.warns(UserWarning, match="1 values in 'date'.*2420-10-18"):
        cleaned = clean_table_columns(transactions, schema)
    assert str(cleaned["date"].dtype) == "datetime64[ns]"
    assert cleaned["date"].tolist()[0] == pd.Timestamp("2024-10-18")
    assert cleaned["date"].isna().tolist() == [False, True, True]


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
