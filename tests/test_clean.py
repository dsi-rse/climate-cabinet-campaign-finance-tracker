"""Tests for the clean module"""

import pandas as pd
import pytest
from utils.clean.transactor import divide_full_name_nameparser, fill_in_transactor_types


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
