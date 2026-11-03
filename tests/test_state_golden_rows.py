"""Check who paid whom, how much, when and what kind, one row per case

Each test standardizes a synthetic fixture file (see
tests/data/standardize/make_az_mi_tx_fixtures.py) with the state's real config
and checks individual rows, so a mapping that swaps donor and recipient, drops
a name, or reads the wrong date column fails here rather than only showing up
in the final data.
"""

from pathlib import Path

import pandas as pd
import pytest
from utils.standardize.source import DataSourceStandardizationPipeline

CONFIG_DIRECTORY = (
    Path(__file__).parents[1] / "src" / "utils" / "standardize" / "finance" / "config"
)
RAW_FIXTURE_DIRECTORY = Path(__file__).parent / "data" / "standardize" / "raw"


def standardize(state: str, form: str) -> pd.DataFrame:
    pipeline = DataSourceStandardizationPipeline(
        state_code=state,
        form_code=form,
        config_file=CONFIG_DIRECTORY / f"{state}.yaml",
    )
    return pipeline.load_and_standardize_data_source(
        state_data_directory=RAW_FIXTURE_DIRECTORY / state
    )


def only_row(table: pd.DataFrame, column: str, value) -> pd.Series:
    """The single row where column equals value"""
    matches = table[table[column] == value]
    assert len(matches) == 1, f"{len(matches)} rows have {column} == {value!r}"
    return matches.iloc[0]


def assert_row(row: pd.Series, expected: dict) -> None:
    """Compare as stripped strings; nulls compare as None"""
    actual = {
        column: (
            None
            if column not in row.index or pd.isna(row[column])
            else str(row[column]).strip()
        )
        for column in expected
    }
    assert actual == expected


# ---- Arizona ----


@pytest.fixture(scope="module")
def az_advanced_search():
    return standardize("az", "advanced_search_transactions")


@pytest.fixture(scope="module")
def az_access_export():
    return standardize("az", "mdb_transactions")


def test_az_income_is_paid_to_the_committee(az_advanced_search):
    row = only_row(az_advanced_search, "amount", 150.0)
    assert_row(
        row,
        {
            "donor--first_name": "Alex",
            "donor--last_name": "Donor",
            "donor--address--city": "Phoenix",
            "recipient_id": "900001",
            "recipient--full_name": "Friends of Testperson",
            "date": "2024-03-07",
            "transaction_type": "contribution",
        },
    )


@pytest.mark.parametrize(
    ("amount", "payee", "date"),
    [
        (250.0, "Example Print Shop", "2023-01-09"),
        (1000.0, "Committee to Elect Sam Sample", "2023-01-10"),
        (12.5, "Example Diner", "2023-01-11"),
    ],
)
def test_az_expense_is_paid_by_the_committee(az_advanced_search, amount, payee, date):
    row = only_row(az_advanced_search, "amount", amount)
    assert_row(
        row,
        {
            "donor_id": "900001",
            "donor--full_name": "Friends of Testperson",
            "recipient_id": None,
            "recipient--full_name": payee,
            "date": date,
            "transaction_type": "expense",
        },
    )


def test_az_payee_details_move_with_the_payee(az_advanced_search):
    row = only_row(az_advanced_search, "amount", 250.0)
    assert_row(
        row,
        {
            "recipient--address--city": "Tucson",
            "recipient--address--state": "AZ",
            "donor--address--city": None,
        },
    )


def test_az_direction_column_is_not_kept(az_advanced_search, az_access_export):
    assert "transaction_direction" not in az_advanced_search.columns
    assert "transaction_direction" not in az_access_export.columns


def test_az_access_export_contribution(az_access_export):
    row = only_row(az_access_export, "amount", 50.0)
    assert_row(
        row,
        {
            "donor_id": "800001",
            "recipient_id": "900001",
            "date": "2003-09-10",
            "transaction_type": "contribution",
        },
    )


@pytest.mark.parametrize(
    ("amount", "date"),
    [
        # written ($745.00): negative because it left the committee
        (745.0, "1999-12-31"),
        # "Pay a Bill" is written as a positive amount but is still a payment
        (421.37, "1996-12-02"),
    ],
)
def test_az_access_export_payment_is_paid_by_the_committee(
    az_access_export, amount, date
):
    row = only_row(az_access_export, "amount", amount)
    assert_row(
        row,
        {
            "donor_id": "900001",
            "recipient_id": "800002",
            "date": date,
            "transaction_type": "expense",
        },
    )


def test_az_access_export_amounts_are_parsed_and_non_negative(az_access_export):
    assert az_access_export["amount"].notna().all()
    assert (az_access_export["amount"] >= 0).all()


# ---- Michigan ----


@pytest.fixture(scope="module")
def mi_receipts():
    return standardize("mi", "legacy_download_contributions_or_receipts")


@pytest.fixture(scope="module")
def mi_expenditures():
    return standardize("mi", "legacy_download_expenditures")


def test_mi_independent_committee_is_an_organization(mi_receipts):
    row = only_row(mi_receipts, "amount", 50.0)
    assert_row(
        row,
        {
            "recipient_id": "9001",
            "recipient--full_name": "EXAMPLE INDUSTRY POLITICAL ACTION COMMITTEE",
            "recipient--transactor_type": "Organization",
            "recipient--transactor_type_specific": "Committee",
            "donor--first_name": "JANE",
            "donor--full_name": "TESTPERSON",
            "date": "2022-03-14",
        },
    )


def test_mi_no_committee_is_labelled_an_individual(mi_receipts, mi_expenditures):
    assert set(mi_receipts["recipient--transactor_type"]) == {"Organization"}
    assert set(mi_expenditures["donor--transactor_type"]) == {"Organization"}


def test_mi_committee_contribution_to_a_candidate_committee(mi_expenditures):
    row = only_row(mi_expenditures, "amount", 25000.0)
    assert_row(
        row,
        {
            "donor_id": "9001",
            "donor--full_name": "EXAMPLE INDUSTRY POLITICAL ACTION COMMITTEE",
            "donor--transactor_type_specific": "Committee",
            "recipient_id": None,
            "recipient--full_name": "FRIENDS OF SAM SAMPLE",
            "recipient--address--city": "DETROIT",
            "recipient--candidate--member--full_name": "SAM SAMPLE",
            "recipient--candidate--member--election_result--election--office_sought": "State Representative",
            "recipient--candidate--member--election_result--election--district": "42",
            "date": "2022-10-10",
        },
    )


@pytest.mark.parametrize(
    ("amount", "first_name", "payee"),
    [(68.67, None, "EXAMPLE PRINT SHOP"), (65.0, "ALEX", "SAMPLEPERSON")],
)
def test_mi_expenditure_is_paid_by_the_filer(
    mi_expenditures, amount, first_name, payee
):
    row = only_row(mi_expenditures, "amount", amount)
    assert_row(
        row,
        {
            "donor_id": "9002",
            "donor--full_name": "FRIENDS OF SAM SAMPLE",
            "donor--transactor_type_specific": "Candidate Committee",
            "recipient--first_name": first_name,
            "recipient--full_name": payee,
        },
    )


# ---- Texas ----


def test_tx_contribution_is_dated_when_given_not_when_reported():
    table = standardize("tx", "contributions")
    row = only_row(table, "donor--last_name", "Testperson0")
    assert_row(
        row,
        {
            "recipient_id": "00090001",
            "recipient--full_name": "Example Industry PAC",
            "amount": "100.0",
            # contributionDt; the report was received 2009-02-01
            "date": "2008-12-20",
        },
    )


def test_tx_expenditure_is_dated_when_spent_not_when_reported():
    table = standardize("tx", "expenditures")
    row = only_row(table, "recipient--full_name", "Sample Campaign 0")
    assert_row(
        row,
        {
            "donor_id": "00090001",
            "donor--full_name": "Example Industry PAC",
            "amount": "500.0",
            # expendDt; the report was received 2002-11-01
            "date": "2002-09-20",
        },
    )
