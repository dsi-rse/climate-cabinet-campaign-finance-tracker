import re
from unittest.mock import MagicMock

import pandas as pd
import pytest
from utils.ids import (  # Update this with the actual module name
    UUID4_REGEX,
    create_new_uuid_mapping,
    get_raw_ids_mask,
    handle_id_column,
    map_ids_to_uuids,
    replace_null_ids_with_uuids,
)


@pytest.fixture
def sample_table():
    """Creates a sample DataFrame for testing"""
    return pd.DataFrame(
        {
            "id": [1, "invalid_uuid", "550e8400-e29b-41d4-a716-446655440000", None, 1],
            "year": [2023, 2022, 2021, 2020, 2023],
            "reported_state": ["CA", "NY", "TX", "FL", "CA"],
        }
    )


def test_add_uuids_to_table(sample_table):
    """Test that missing IDs are assigned UUIDs"""
    replace_null_ids_with_uuids(sample_table, "id")
    assert sample_table["id"].notna().all(), "All ID values should be filled"
    assert re.match(UUID4_REGEX, sample_table.loc[3, "id"])


def test_map_ids_to_uuids(sample_table):
    """Test that IDs are replaced if found in the mapping"""
    id_mapping = {
        ("1", 2023, "CA", "tableA"): "11111111-1111-4111-8111-111111111111",
        ("invalid_uuid", 2022, "NY", "tableA"): "22222222-2222-4222-8222-222222222222",
    }
    map_ids_to_uuids(sample_table, "tableA", id_mapping, "id")

    assert sample_table.loc[0, "id"] == "11111111-1111-4111-8111-111111111111"
    assert sample_table.loc[4, "id"] == "11111111-1111-4111-8111-111111111111"
    assert sample_table.loc[1, "id"] == "22222222-2222-4222-8222-222222222222"
    assert sample_table.loc[2, "id"] == "550e8400-e29b-41d4-a716-446655440000"


def test_create_new_uuid_mapping(sample_table):
    """Test new UUIDs are created for invalid/missing IDs"""
    new_mapping = create_new_uuid_mapping(sample_table, "tableA", "id")

    assert len(new_mapping) == 2, "All unique combinations should be mapped"
    for key, value in new_mapping.items():
        assert isinstance(value, str) and re.match(
            UUID4_REGEX, value
        ), "Generated value should be a valid UUID"
        assert key[3] == "tableA", "Table name should be correctly included in the key"


def test_get_raw_ids_mask(sample_table):
    """Test that get_raw_ids_mask correctly identifies non-UUIDs"""
    mask = get_raw_ids_mask(sample_table, "id")
    assert mask.tolist() == [
        True,
        True,
        False,
        False,
        True,
    ], "Mask should correctly identify raw/non-UUID IDs"


def test_handle_id_column(sample_table):
    """Test full handling logic"""
    mock_schema = MagicMock()
    mock_schema.attributes = ["id"]
    mock_schema.table_name = "tableA"

    id_mapping = {
        ("1", 2023, "CA", "tableA"): "11111111-1111-4111-8111-111111111111",
    }
    handle_id_column(
        sample_table, mock_schema, mock_schema.table_name, id_mapping, "id"
    )

    assert (
        id_mapping[("1", 2023, "CA", "tableA")]
        == "11111111-1111-4111-8111-111111111111"
    ), "Old mappings should remain"
    expected_id_mapping_length = 2
    assert re.match(
        UUID4_REGEX, id_mapping[("invalid_uuid", 2022, "NY", "tableA")]
    ), "New mappings should be uuids"
    assert len(id_mapping) == expected_id_mapping_length, "No extra new mappings"

    assert (
        sample_table.loc[2, "id"] == "550e8400-e29b-41d4-a716-446655440000"
    ), "Exising ids should be unchanged"
    assert re.match(
        UUID4_REGEX, sample_table.loc[3, "id"]
    ), "Nans in provided table should become uuids"
    assert (
        sample_table["id"].str.match(UUID4_REGEX).all()
    ), "All ids should now be uuids"
