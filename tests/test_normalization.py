from pathlib import Path
from uuid import UUID

import pandas as pd
import pytest
import yaml
from utils.constants import DEFAULT_SCHEMA_PATH
from utils.normalize import Normalizer
from utils.schema import DataSchema

BASE_FILEPATH = Path(__file__).resolve().parent.parent

# uuids standing in for source ids that were already mapped before splitting
SOURCE_ID_A = "aaaaaaaa-aaaa-4aaa-8aaa-000000000001"
SOURCE_ID_B = "aaaaaaaa-aaaa-4aaa-8aaa-000000000002"
RECIPIENT_ID = "708e0a88-c449-432f-b6cf-e11a0c681921"


@pytest.fixture()
def determined_uuids(mocker):
    side_effect = (
        UUID(f"00000000-0000-4000-8000-{i:012}", version=4) for i in range(1, 10000)
    )
    return mocker.patch("uuid.uuid4", side_effect=side_effect)


def make_df_standard_for_testing(df: pd.DataFrame, columns: pd.Index) -> pd.DataFrame:
    """Sort df by columns and reset index to facilitate testing


    pd.testing.assert_frame_equal requires the index to be considered.
    Doing this allows us to ensure two otherwise equivalent dfs will
    have the same index for each logical row
    """
    return df.sort_values(by=columns.tolist()).reset_index(drop=True)


def load_database(database_path):
    """Loads all database tables from a given directory."""
    full_database = {}
    for normalization_directory in ["unnormalized", "1NF", "3NF"]:
        database_normalization_level_path = database_path / normalization_directory
        if database_normalization_level_path.exists():
            database = {}
            for csv_file in Path(database_normalization_level_path).glob("*.csv"):
                table_name = csv_file.stem
                table = pd.read_csv(csv_file)
                # if "id" in table.columns:
                #     table = table.set_index("id")
                #     if "Unnamed: 0" in table.columns:
                #         table = table.drop(columns=["Unnamed: 0"])
                database[table_name] = table
            full_database[normalization_directory] = database
    return full_database


def load_schema(database_path: Path) -> DataSchema:
    """Loads a dataschema from given directory"""
    schema_file = database_path / "schema.yaml"
    if schema_file.exists():
        return DataSchema(schema_file)
    return None


@pytest.fixture
def database_fixture(request):
    """Loads full database and config dynamically"""
    database_name = request.param
    database_path = BASE_FILEPATH / "tests" / "data" / "normalization" / database_name
    return {
        "data": load_database(database_path),
        "schema": load_schema(database_path),
    }


@pytest.mark.parametrize(
    "database_fixture",
    [("campaign-finance-sample"), ("campaign-finance-tricky")],
    indirect=True,
)
def test_1NF_from_unnormalized(database_fixture):
    """Tests removing repeating columns (Level 0 to Level 1)."""
    unnormalized_database = database_fixture["data"]["unnormalized"]
    database_1NF = database_fixture["data"]["1NF"]
    schema = database_fixture["schema"]
    normalizer = Normalizer(unnormalized_database, schema)

    for table_name, table in database_1NF.items():
        expected_value = table.copy()

        normalizer.convert_to_1NF_from_unnormalized(table_name)
        normalized_transactions = normalizer.database[table_name]
        normalized_transactions = make_df_standard_for_testing(
            normalized_transactions, normalized_transactions.columns
        )
        expected_value = make_df_standard_for_testing(
            expected_value, normalized_transactions.columns
        )
        normalized_transactions.columns.name = None

        pd.testing.assert_frame_equal(
            normalized_transactions,
            expected_value,
            check_like=True,
            check_dtype=False,
        )


@pytest.fixture
def schema_fixture(request, tmp_path):
    """Dynamic schema fixture that creates the appropriate schema based on the test parameter."""
    schema_type = request.param
    if schema_type == "simple_schema_fixture":
        # Create the simple schema
        schema_data = yaml.safe_load("""
        Transaction:
          required_attributes:
            - amount
            - donor_id
            - recipient_id
          attributes:
            - amount
            - date
            - year
            - month
            - donor_id
            - recipient_id
          relations:
            - prefix: donor
              table: Transactor
              direction: forward
            - prefix: recipient
              table: Transactor
              direction: forward

        Transactor:
          required_attributes:
            - id
          attributes:
            - id
            - full_name
            - phone_number
          relations:
            - prefix: address
              table: Address
              direction: reverse
              reverse_relation_name: transactor_id
            - prefix: employer
              table: Membership
              direction: reverse
              reverse_relation_name: member_id
              relationship_column_values:
                membership_type: Member

        Address:
          required_attributes:
            - transactor_id
          attributes:
            - street_address
            - city
            - state
            - zip_code
            - transactor_id
          relations:
            - prefix: transactor
              table: Transactor
              direction: forward

        Membership:
          required_attributes:
            - member_id
            - organization_id
          attributes:
            - member_id
            - organization_id
            - membership_type
          relations:
            - prefix: member
              table: Transactor
              direction: forward
            - prefix: organization
              table: Transactor
              direction: forward
    """)
        schema_file = tmp_path / "simple_schema.yaml"
        schema_file.write_text(yaml.dump(schema_data))
        return schema_file
    elif schema_type == "full_schema_fixture":
        # Create the full schema
        with (BASE_FILEPATH / "src" / "utils" / "table.yaml").open() as file:
            schema_data = yaml.safe_load(file)
        schema_file = tmp_path / "committee_schema.yaml"
        schema_file.write_text(yaml.dump(schema_data))
        return schema_file
    else:
        raise ValueError(f"Unknown schema type: {schema_type}")


@pytest.mark.parametrize(
    "description,input_data,table_name,relation_prefix,expected_output_data,expected_extracted_data,should_raise_error,error_message,schema_fixture",
    [
        # Simple case
        (
            "simple",
            {
                "donor_id": [None],
                "donor--full_name": ["Fake Name"],
                "amount": [100],
                "recipient_id": ["708e0a88-c449-432f-b6cf-e11a0c681921"],
                "reported_state": ["CA"],
            },
            "Transaction",
            "donor",
            {
                "donor_id": ["00000000-0000-4000-8000-000000000001"],
                "amount": [100],
                "recipient_id": ["708e0a88-c449-432f-b6cf-e11a0c681921"],
                "reported_state": ["CA"],
            },
            {
                "id": ["00000000-0000-4000-8000-000000000001"],
                "full_name": ["Fake Name"],
                "reported_state": ["CA"],
            },
            False,
            None,
            "simple_schema_fixture",
        ),
        # Map repeated IDs
        (
            "map-repeated-ids",
            {
                "donor_id": [None, None, None],
                "donor--full_name": ["Fake Name", "Fake Name", "Other Name"],
                "amount": [100, 100, 2.4],
                "recipient_id": [
                    "708e0a88-c449-432f-b6cf-e11a0c681921",
                    "f87748de-addb-4336-a42c-8f65f2d83990",
                    "708e0a88-c449-432f-b6cf-e11a0c681921",
                ],
                "reported_state": ["CA", "CA", "CA"],
            },
            "Transaction",
            "donor",
            {
                "donor_id": [
                    "00000000-0000-4000-8000-000000000001",
                    "00000000-0000-4000-8000-000000000001",
                    "00000000-0000-4000-8000-000000000002",
                ],
                "amount": [100, 100, 2.4],
                "recipient_id": [
                    "708e0a88-c449-432f-b6cf-e11a0c681921",
                    "f87748de-addb-4336-a42c-8f65f2d83990",
                    "708e0a88-c449-432f-b6cf-e11a0c681921",
                ],
                "reported_state": ["CA", "CA", "CA"],
            },
            {
                "id": [
                    "00000000-0000-4000-8000-000000000001",
                    "00000000-0000-4000-8000-000000000002",
                ],
                "full_name": ["Fake Name", "Other Name"],
                "reported_state": ["CA", "CA"],
            },
            False,
            None,
            "simple_schema_fixture",
        ),
        # Missing ID matches existing ID
        (
            "missing-id-matches-existing-id",
            {
                "donor_id": [
                    None,
                    "00000000-0000-4000-8000-000000000001",
                    "00000000-0000-4000-8000-000000000002",
                ],
                "donor--full_name": ["Fake Name", "Fake Name", "Other Name"],
                "amount": [100, 100, 2.4],
                "recipient_id": [
                    "708e0a88-c449-432f-b6cf-e11a0c681921",
                    "f87748de-addb-4336-a42c-8f65f2d83990",
                    "b4c70c73-964c-4d52-a48a-b4ad16d8b4b0",
                ],
                "reported_state": ["CA", "CA", "CA"],
            },
            "Transaction",
            "donor",
            {
                "donor_id": [
                    "00000000-0000-4000-8000-000000000001",
                    "00000000-0000-4000-8000-000000000001",
                    "00000000-0000-4000-8000-000000000002",
                ],
                "amount": [100, 100, 2.4],
                "recipient_id": [
                    "708e0a88-c449-432f-b6cf-e11a0c681921",
                    "f87748de-addb-4336-a42c-8f65f2d83990",
                    "b4c70c73-964c-4d52-a48a-b4ad16d8b4b0",
                ],
                "reported_state": ["CA", "CA", "CA"],
            },
            {
                "id": [
                    "00000000-0000-4000-8000-000000000001",
                    "00000000-0000-4000-8000-000000000002",
                ],
                "full_name": ["Fake Name", "Other Name"],
                "reported_state": ["CA", "CA"],
            },
            False,
            None,
            "simple_schema_fixture",
        ),
        # Reverse relation
        (
            "reverse-relation",
            {
                "id": [
                    "00000000-0000-4000-8000-000000000001",
                    "00000000-0000-4000-8000-000000000002",
                ],
                "full_name": ["Fake Name", "Other Name"],
                "phone_number": ["1234567890", "1234567890"],
                "address--city": ["New York", "Los Angeles"],
                "address--state": ["NY", "CA"],
                "reported_state": ["CA", "CA"],
            },
            "Transactor",
            "address",
            {
                "id": [
                    "00000000-0000-4000-8000-000000000001",
                    "00000000-0000-4000-8000-000000000002",
                ],
                "full_name": ["Fake Name", "Other Name"],
                "phone_number": ["1234567890", "1234567890"],
                "reported_state": ["CA", "CA"],
            },
            {
                "transactor_id": [
                    "00000000-0000-4000-8000-000000000001",
                    "00000000-0000-4000-8000-000000000002",
                ],
                "city": ["New York", "Los Angeles"],
                "state": ["NY", "CA"],
                "reported_state": ["CA", "CA"],
            },
            False,
            None,
            "simple_schema_fixture",
        ),
        # Reverse relation with missing ID - should raise error
        (
            "reverse-relation-with-missing-id",
            {
                "id": [None, "00000000-0000-4000-8000-000000000002"],
                "full_name": ["Fake Name", "Other Name"],
                "phone_number": ["1234567890", "1234567890"],
                "address--city": ["New York", "Los Angeles"],
                "address--state": ["NY", "CA"],
                "reported_state": ["CA", "CA"],
            },
            "Transactor",
            "address",
            None,
            None,
            True,
            "Table 'Transactor' has no id column or has NaN ids.",
            "simple_schema_fixture",
        ),
        # Reverse relation with null values
        (
            "reverse-relation-with-null-values",
            {
                "id": [
                    "00000000-0000-4000-8000-000000000001",
                    "00000000-0000-4000-8000-000000000002",
                ],
                "full_name": ["Fake Name", "Other Name"],
                "phone_number": ["1234567890", "1234567890"],
                "address--city": [None, None],
                "address--state": [None, None],
                "reported_state": ["CA", "CA"],
            },
            "Transactor",
            "address",
            {
                "id": [
                    "00000000-0000-4000-8000-000000000001",
                    "00000000-0000-4000-8000-000000000002",
                ],
                "full_name": ["Fake Name", "Other Name"],
                "phone_number": ["1234567890", "1234567890"],
                "reported_state": ["CA", "CA"],
            },
            {"transactor_id": [], "city": [], "state": [], "reported_state": []},
            False,
            None,
            "simple_schema_fixture",
        ),
        # Relation table
        (
            "relation-table",
            {
                "id": [
                    "00000000-0000-4000-8000-000000000001",
                    "00000000-0000-4000-8000-000000000002",
                ],
                "full_name": ["Fake Name", "Other Name"],
                "employer--organization--full_name": [
                    "Fake Organization",
                    "Fake Organization",
                ],
                "phone_number": ["1234567890", "1234567890"],
                "reported_state": ["CA", "CA"],
            },
            "Transactor",
            "employer",
            {
                "id": [
                    "00000000-0000-4000-8000-000000000001",
                    "00000000-0000-4000-8000-000000000002",
                ],
                "full_name": ["Fake Name", "Other Name"],
                "phone_number": ["1234567890", "1234567890"],
                "reported_state": ["CA", "CA"],
            },
            {
                "member_id": [
                    "00000000-0000-4000-8000-000000000001",
                    "00000000-0000-4000-8000-000000000002",
                ],
                "organization--full_name": ["Fake Organization", "Fake Organization"],
                "membership_type": ["Member", "Member"],
                "reported_state": ["CA", "CA"],
            },
            False,
            None,
            "simple_schema_fixture",
        ),
        (
            "forward-relation-with-nans",
            {
                "donor_id": [None, None, None],
                "donor--full_name": ["Fake Name", "Fake Name", "Other Name"],
                "donor--address--city": [pd.NA, pd.NA, pd.NA],
                "donor--address--state": [pd.NA, pd.NA, pd.NA],
                "amount": [100, 100, 2.4],
                "recipient_id": [
                    "708e0a88-c449-432f-b6cf-e11a0c681921",
                    "f87748de-addb-4336-a42c-8f65f2d83990",
                    "708e0a88-c449-432f-b6cf-e11a0c681921",
                ],
                "reported_state": ["CA", "CA", "CA"],
            },
            "Transaction",
            "donor",
            {
                "donor_id": [
                    "00000000-0000-4000-8000-000000000001",
                    "00000000-0000-4000-8000-000000000001",
                    "00000000-0000-4000-8000-000000000002",
                ],
                "amount": [100, 100, 2.4],
                "recipient_id": [
                    "708e0a88-c449-432f-b6cf-e11a0c681921",
                    "f87748de-addb-4336-a42c-8f65f2d83990",
                    "708e0a88-c449-432f-b6cf-e11a0c681921",
                ],
                "reported_state": ["CA", "CA", "CA"],
            },
            {
                "id": [
                    "00000000-0000-4000-8000-000000000001",
                    "00000000-0000-4000-8000-000000000002",
                ],
                "full_name": ["Fake Name", "Other Name"],
                "reported_state": ["CA", "CA"],
                "address--city": [pd.NA, pd.NA],
                "address--state": [pd.NA, pd.NA],
            },
            False,
            None,
            "simple_schema_fixture",
        ),
        # Two source ids with identical attributes are two entities, not one
        (
            "distinct-source-ids-with-same-attributes-stay-distinct",
            {
                "donor_id": [SOURCE_ID_A, SOURCE_ID_B],
                "donor--full_name": ["Bob", "Bob"],
                "amount": [1, 2],
                "recipient_id": [RECIPIENT_ID, RECIPIENT_ID],
                "reported_state": ["CA", "CA"],
            },
            "Transaction",
            "donor",
            {
                "donor_id": [SOURCE_ID_A, SOURCE_ID_B],
                "amount": [1, 2],
                "recipient_id": [RECIPIENT_ID, RECIPIENT_ID],
                "reported_state": ["CA", "CA"],
            },
            {
                "id": [SOURCE_ID_A, SOURCE_ID_B],
                "full_name": ["Bob", "Bob"],
                "reported_state": ["CA", "CA"],
            },
            False,
            None,
            "simple_schema_fixture",
        ),
        # A source id on a row with no donor attributes is kept, not nulled
        (
            "source-id-without-attributes-is-kept",
            {
                "donor_id": [SOURCE_ID_A, SOURCE_ID_A, SOURCE_ID_B],
                "donor--full_name": [None, "Alice", None],
                "amount": [1, 2, 3],
                "recipient_id": [RECIPIENT_ID, RECIPIENT_ID, RECIPIENT_ID],
                "reported_state": ["CA", "CA", "CA"],
            },
            "Transaction",
            "donor",
            {
                "donor_id": [SOURCE_ID_A, SOURCE_ID_A, SOURCE_ID_B],
                "amount": [1, 2, 3],
                "recipient_id": [RECIPIENT_ID, RECIPIENT_ID, RECIPIENT_ID],
                "reported_state": ["CA", "CA", "CA"],
            },
            {
                "id": [SOURCE_ID_A],
                "full_name": ["Alice"],
                "reported_state": ["CA"],
            },
            False,
            None,
            "simple_schema_fixture",
        ),
        # A row without a source id whose attributes match several source ids
        # is not attached to any of them; it becomes its own entity
        (
            "ambiguous-attribute-match-gets-new-entity",
            {
                "donor_id": [None, SOURCE_ID_A, SOURCE_ID_B],
                "donor--full_name": ["Bob", "Bob", "Bob"],
                "amount": [1, 2, 3],
                "recipient_id": [RECIPIENT_ID, RECIPIENT_ID, RECIPIENT_ID],
                "reported_state": ["CA", "CA", "CA"],
            },
            "Transaction",
            "donor",
            {
                "donor_id": [
                    "00000000-0000-4000-8000-000000000001",
                    SOURCE_ID_A,
                    SOURCE_ID_B,
                ],
                "amount": [1, 2, 3],
                "recipient_id": [RECIPIENT_ID, RECIPIENT_ID, RECIPIENT_ID],
                "reported_state": ["CA", "CA", "CA"],
            },
            {
                "id": [
                    SOURCE_ID_A,
                    SOURCE_ID_B,
                    "00000000-0000-4000-8000-000000000001",
                ],
                "full_name": ["Bob", "Bob", "Bob"],
                "reported_state": ["CA", "CA", "CA"],
            },
            False,
            None,
            "simple_schema_fixture",
        ),
    ],
    indirect=["schema_fixture"],
)
def test_split_prefixed_columns(
    description,
    input_data,
    table_name,
    relation_prefix,
    expected_output_data,
    expected_extracted_data,
    should_raise_error,
    error_message,
    schema_fixture,
    determined_uuids,
):
    """Tests _split_prefixed_columns based on docstring examples."""

    # Create normalizer
    database = {}
    normalizer = Normalizer(database, schema_fixture)

    # Create input table
    input_table = pd.DataFrame(input_data)

    if should_raise_error:
        with pytest.raises(ValueError, match=rf"{error_message}.*"):
            normalizer._split_prefixed_columns(input_table, table_name, relation_prefix)
    else:
        # Call the method
        result_table, result_extracted_table = normalizer._split_prefixed_columns(
            input_table, table_name, relation_prefix
        )

        # Create expected tables
        expected_output_table = pd.DataFrame(expected_output_data)
        expected_extracted_table = pd.DataFrame(expected_extracted_data)

        # Standardize for comparison
        result_table = make_df_standard_for_testing(result_table, result_table.columns)
        expected_output_table = make_df_standard_for_testing(
            expected_output_table, result_table.columns
        )

        result_extracted_table = make_df_standard_for_testing(
            result_extracted_table, result_extracted_table.columns
        )
        expected_extracted_table = make_df_standard_for_testing(
            expected_extracted_table, result_extracted_table.columns
        )

        # Assert results
        pd.testing.assert_frame_equal(
            result_table,
            expected_output_table,
            check_like=True,
            check_dtype=False,
        )

        pd.testing.assert_frame_equal(
            result_extracted_table,
            expected_extracted_table,
            check_like=True,
            check_dtype=False,
        )


@pytest.mark.parametrize(
    "description,input_data,table_name,expected_output_data,schema_fixture",
    [
        (
            "relationship-table",
            {
                # ids outside the mocked uuid4 sequence, so the organizations'
                # new ids can't collide with the members'
                "id": [SOURCE_ID_A, SOURCE_ID_B],
                "full_name": ["Fake Name", "Other Name"],
                "employer--organization--full_name": [
                    "Fake Organization",
                    "Other Organization",
                ],
                "employer--organization--phone_number": ["1234567890", "1234567890"],
                "reported_state": ["CA", "CA"],
            },
            "Transactor",
            {
                "Transactor": {
                    "id": [
                        SOURCE_ID_A,
                        SOURCE_ID_B,
                        "00000000-0000-4000-8000-000000000001",
                        "00000000-0000-4000-8000-000000000002",
                    ],
                    "full_name": [
                        "Fake Name",
                        "Other Name",
                        "Fake Organization",
                        "Other Organization",
                    ],
                    "phone_number": [None, None, "1234567890", "1234567890"],
                    "reported_state": ["CA", "CA", "CA", "CA"],
                },
                "Membership": {
                    "member_id": [SOURCE_ID_A, SOURCE_ID_B],
                    "organization_id": [
                        "00000000-0000-4000-8000-000000000001",
                        "00000000-0000-4000-8000-000000000002",
                    ],
                    "membership_type": ["Member", "Member"],
                    "reported_state": ["CA", "CA"],
                },
                "Address": {},
                "Transaction": {},
            },
            "simple_schema_fixture",
        ),
        (
            "campaign-committee-complex",
            {
                "full_name": [None],
                "phone_number": [None],
                "email": [None],
                "transactor_type_specific": ["Company"],
                "address--full_address": [None],
                "chairman--member--full_name": [None],
                "treasurer--member--full_name": [None],
                "candidate--member--full_name": [None],
                "candidate--member--phone_number": [None],
                "candidate--member--email": [None],
                "designee--member--full_name": [None],
                "candidate--member--election_result--election--office_sought": [None],
                "party": [None],
                "address--county": [None],
                "id": ["00000000-0000-4000-8000-000000000001"],
                "reported_state": ["AZ"],
                "last_name": ["ASHLEY"],
                "first_name": [None],
                "middle_name": [None],
                "name_suffix": [None],
                "address--line_1": ["8605 N 59th Ave"],
                "address--line_2": ["Apt 1004"],
                "address--city": ["Glendale"],
                "address--state": ["AZ"],
                "address--zipcode": ["85302"],
                "employer--role": [None],
                "employer--organization--full_name": [None],
                "chairman--member_id": [None],
                "treasurer--member_id": [None],
                "candidate--member_id": [None],
                "designee--member_id": [None],
                "sponsor--member_id": [None],
                "candidate--member--party": [None],
                "candidate--member--address--county": [None],
                "candidate--member--election_result--election--district": [None],
            },
            "Transactor",
            {
                "Transactor": {
                    "id": ["00000000-0000-4000-8000-000000000001"],
                    "full_name": [None],
                    "phone_number": [None],
                    "email": [None],
                    "transactor_type_specific": ["Company"],
                    "party": [None],
                    "reported_state": ["AZ"],
                    # Individual attributes stay on Transactor (single table
                    # inheritance)
                    "last_name": ["ASHLEY"],
                    "first_name": [None],
                    "middle_name": [None],
                    "name_suffix": [None],
                },
                "Address": {
                    "transactor_id": ["00000000-0000-4000-8000-000000000001"],
                    "full_address": [None],
                    "line_1": ["8605 N 59th Ave"],
                    "line_2": ["Apt 1004"],
                    "city": ["Glendale"],
                    "state": ["AZ"],
                    "zipcode": ["85302"],
                    "county": [None],
                    "reported_state": ["AZ"],
                },
                "Membership": {},
                "Transaction": {},
                "Election": {},
                "ElectionResult": {},
                "Individual": {},
                "Organization": {},
            },
            "full_schema_fixture",
        ),
    ],
    indirect=["schema_fixture"],
)
def test_convert_table_to_3NF_from_1NF(
    description,
    input_data,
    table_name,
    expected_output_data,
    schema_fixture,
    determined_uuids,
):
    """Tests _convert_table_to_3NF_from_1NF based on docstring examples and table.yaml schema."""
    database = {}
    normalizer = Normalizer(database, schema_fixture)
    input_table = pd.DataFrame(input_data)

    # Call the method
    result_database = normalizer._convert_table_to_3NF_from_1NF(input_table, table_name)

    expected_database = {
        name: pd.DataFrame(data) for name, data in expected_output_data.items()
    }

    assert result_database.keys() == expected_database.keys()

    for name, expected_table in expected_database.items():
        if expected_table.empty:
            assert result_database[name].empty, f"Expected table {name} to be empty"
            continue
        expected_table = make_df_standard_for_testing(
            expected_table, expected_table.columns
        )
        result_table = make_df_standard_for_testing(
            result_database[name], expected_table.columns
        )
        pd.testing.assert_frame_equal(
            result_table,
            expected_table,
            check_like=True,
            check_dtype=False,
        )


def _normalize_transactions(transactions: dict) -> tuple[dict, Normalizer]:
    """Normalize a Transaction table with the package schema"""
    normalizer = Normalizer(
        {"Transaction": pd.DataFrame(transactions)}, DEFAULT_SCHEMA_PATH
    )
    return normalizer.normalize_database(), normalizer


def _uuid_for(normalizer: Normalizer, raw_id: str) -> str:
    """Look up the uuid assigned to a raw Transactor id"""
    return normalizer.id_mapping[(raw_id, None, "MN", "Transactor")]


def test_normalize_database_keeps_source_ids_distinct_and_unique():
    """Source ids decide entity identity, and each id is one Transactor row"""
    with pytest.warns(UserWarning, match="1 Transactor id"):
        database, normalizer = _normalize_transactions(
            {
                "amount": [1.0, 2.0, 3.0, 4.0, 5.0],
                "donor_id": ["111", "111", "222", "333", "R1"],
                # 111 is reported under two spellings; 222 and 333 share a name
                "donor--full_name": ["Alice", "Alicia", "Bob", "Bob", "Cmte"],
                # R1 is a recipient in four rows and a donor in the fifth
                "recipient_id": ["R1", "R1", "R1", "R1", "111"],
                "recipient--full_name": ["Cmte", "Cmte", "Cmte", "Cmte", "Alice"],
                "reported_state": ["MN"] * 5,
            }
        )
    transactions = database["Transaction"]
    transactors = database["Transactor"].set_index("id")

    # every source id keeps its own entity: Bob 222 and Bob 333 stay apart
    expected_donors = [
        _uuid_for(normalizer, raw) for raw in ["111", "111", "222", "333", "R1"]
    ]
    expected_recipients = [
        _uuid_for(normalizer, raw) for raw in ["R1", "R1", "R1", "R1", "111"]
    ]
    assert transactions["donor_id"].tolist() == expected_donors
    assert transactions["recipient_id"].tolist() == expected_recipients

    # one row per id, even though R1 and 111 appear as both donor and recipient
    assert transactors.index.is_unique
    assert set(transactors.index) == set(expected_donors)
    # conflicting spellings for one id keep the first one seen
    assert transactors.loc[_uuid_for(normalizer, "111"), "full_name"] == "Alice"


def test_normalize_database_keeps_source_id_without_attributes():
    """A source id on a row with no counterparty attributes is not nulled"""
    database, normalizer = _normalize_transactions(
        {
            "amount": [1.0, 2.0],
            "donor_id": ["444", "555"],
            "donor--full_name": [None, "Dana"],
            "recipient_id": ["R1", "R1"],
            "recipient--full_name": [None, None],
            "reported_state": ["MN", "MN"],
        }
    )
    transactions = database["Transaction"]
    assert transactions["donor_id"].tolist() == [
        _uuid_for(normalizer, "444"),
        _uuid_for(normalizer, "555"),
    ]
    assert transactions["recipient_id"].tolist() == [_uuid_for(normalizer, "R1")] * 2
    # only donors with attributes become Transactor rows
    assert database["Transactor"]["id"].tolist() == [_uuid_for(normalizer, "555")]
