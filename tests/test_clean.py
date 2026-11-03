"""Tests for the clean module"""

import pytest
from utils.clean.transactor import divide_full_name_nameparser


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
