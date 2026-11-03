"""Code for management and creation of unique identifiers"""

import re
import uuid
from pathlib import Path

import pandas as pd

from utils.constants import DEFAULT_SCHEMA_PATH
from utils.schema import DataSchema, TableSchema

# Precompiled regex for UUID4 validation
UUID4_REGEX = re.compile(
    r"^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$",
    re.IGNORECASE,
)

UUIDMapping = dict[tuple[str, int | None, str | None, str], str]


def normalize_id_to_string(value: int | float | str | None) -> str:
    """Convert an ID value to a consistent string representation.

    Handles the case where numeric IDs might be stored as int or float,
    ensuring they always map to the same string key.
    """
    if pd.isna(value):
        return value
    # If it's a numeric type, convert to int first to remove decimal places
    try:
        if isinstance(value, int | float) and not pd.isna(value):
            # Convert to int if it's a whole number, otherwise keep as float
            if float(value).is_integer():
                return str(int(float(value)))
            else:
                return str(float(value))
    except (ValueError, TypeError):
        pass

    return str(value)


def replace_null_ids_with_uuids(table: pd.DataFrame, id_column: str) -> None:
    """For each null value in id_column, replace it with a new UUID

    Args:
        table: DataFrame where each row requires a UUID.
        id_column: Name of the column to be populated with UUIDs.

    Modifies:
        table: Assigns UUIDs to rows where `id_column` is missing.
    """
    missing_mask = table[id_column].isna()
    table.loc[missing_mask, id_column] = [
        str(uuid.uuid4()) for _ in range(missing_mask.sum())
    ]


def map_ids_to_uuids(
    table: pd.DataFrame,
    table_name: str,
    id_mapping: UUIDMapping,
    id_column: str = "id",
    mask: pd.Series = None,
) -> None:
    """Replace values in `id_column` if their row exists in `id_mapping`.

    Args:
        table: DataFrame with an existing `id_column`.
        table_name: Table name used in the mapping.
        id_mapping: Mapping of (raw id, year, state, table_name) to UUIDs.
        id_column: Name of the column to replace with UUIDs.
        mask: Optional boolean mask to filter which rows to update

    Modifies:
        table: Updates `id_column` with mapped UUIDs where applicable.
    """
    if mask is None:
        mask = pd.Series(True, index=table.index)
    table[id_column] = table[id_column].astype("string")
    table.loc[mask, id_column] = table.loc[mask].apply(
        lambda row: id_mapping.get(
            (
                normalize_id_to_string(row[id_column]),
                row.get("year"),
                row.get("reported_state"),
                table_name,
            ),
            row[id_column],
        ),
        axis=1,
    )


def create_new_uuid_mapping(
    table: pd.DataFrame,
    table_name: str,
    id_column: str = "id",
    existing_mapping: UUIDMapping = None,
) -> UUIDMapping:
    """Create UUIDs for rows with invalid or missing UUIDs in `id_column`.

    This function will skip any rows for which the id_column is NA,
    a valid uuid, or already exists in the existing_mapping.

    Args:
        table: DataFrame with an existing `id_column`.
        table_name: Name of the table (used in mapping).
        id_column: Name of the `id` column, defaults to 'id'.
        existing_mapping: Existing ID mapping to check against.

    Returns:
        A dictionary mapping (raw_id, year, state, table_name) → new UUID.
    """
    if existing_mapping is None:
        existing_mapping = {}

    new_mappings = {}
    for _, row in table[
        (table[id_column].notna())
        & (~table[id_column].astype("string").str.match(UUID4_REGEX, na=False))
    ].iterrows():
        key = (
            normalize_id_to_string(row[id_column]),
            row.get("year"),
            row.get("reported_state"),
            table_name,
        )
        if key not in existing_mapping:
            new_mappings[key] = str(uuid.uuid4())

    return new_mappings


def get_raw_ids_mask(table: pd.DataFrame, id_column: str) -> pd.Series:
    """Create a mask that is true where raw ids exist

    Args:
        table: DataFrame with existing `id_column`.
        id_column: Name of the `id` column
    """
    # na=True because a null is *not* a raw id
    not_raw_id_mask = table[id_column].astype("string").str.match(UUID4_REGEX, na=True)
    # negate to get mask where all raw ids are true
    return ~not_raw_id_mask


def handle_existing_ids(
    table: pd.DataFrame, table_name: str, id_mapping: UUIDMapping, id_column: str
) -> None:
    """Ensure all non null ids in id_column are mapped to a uuid in id_mapping

    Args:
        table: DataFrame with `id_column`.
        table_name: Name of table name the id represents.
        id_mapping: Mapping of (raw id, year, reported_state, table_name) to UUIDs.
        id_column: Name of the column to replace with UUIDs.

    Modifies:
        table: Updates `id_column` with mapped UUIDs where applicable.
        id_mapping: Updates `id_mapping` with new id mappings
    """
    map_ids_to_uuids(table, table_name, id_mapping, id_column)
    raw_ids_mask = get_raw_ids_mask(table, id_column)
    new_mappings = create_new_uuid_mapping(
        table.loc[raw_ids_mask], table_name, id_column
    )
    id_mapping.update(new_mappings)
    # this can be done more efficiently if we filter table here
    map_ids_to_uuids(table, table_name, new_mappings, id_column, mask=raw_ids_mask)


def handle_id_column(
    table: pd.DataFrame,
    table_schema: TableSchema,
    id_table_name: str,
    id_mapping: UUIDMapping,
    id_column: str = "id",
) -> None:
    """Ensure each 'id' value in table is a uuid and all raw ids are mapped

    Args:
        table: DataFrame with or without `id_column`.
        table_schema: Schema defining properties of table.
        id_table_name: Name of the table that the id identifies a row of.
        id_mapping: Mapping of (raw id, year, reported_state, table_name) to UUIDs.
        id_column: Name of the column to replace with UUIDs.

    Modifies:
        table: Creates/Updates `id_column` to have UUIDs
        id_mapping: Updates `id_mapping` with new id mappings
    """
    if id_column not in table_schema.attributes:
        return None
    if id_column not in table.columns:
        table[id_column] = None

    replace_null_ids_with_uuids(table, id_column)
    handle_existing_ids(table, id_table_name, id_mapping, id_column)


def save_id_mapping(id_mapping: UUIDMapping, file_path: Path) -> None:
    """Save ID mapping to a TSV file.

    Args:
        id_mapping: Dictionary mapping tuples to UUIDs
        file_path: Path where to save the mapping
    """
    if not id_mapping:
        return

    # Convert mapping to DataFrame format
    rows = []
    for (raw_id, year, reported_state, table_name), uuid_value in id_mapping.items():
        rows.append(
            {
                "raw_id": raw_id,
                "year": year,
                "reported_state": reported_state,
                "table_name": table_name,
                "uuid": uuid_value,
            }
        )

    id_mapping_df = pd.DataFrame(rows)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    id_mapping_df.to_csv(file_path, sep="\t", index=False)


def load_id_mapping(file_path: Path) -> UUIDMapping:
    """Load ID mapping from a TSV file.

    Args:
        file_path: Path to the saved mapping file

    Returns:
        Dictionary mapping tuples to UUIDs
    """
    if not file_path.exists():
        return {}

    id_mapping_df = pd.read_csv(file_path, sep="\t")

    # Convert DataFrame back to mapping format
    id_mapping = {}
    for _, row in id_mapping_df.iterrows():
        key = (
            row["raw_id"],
            row["year"] if pd.notna(row["year"]) else None,
            row["reported_state"] if pd.notna(row["reported_state"]) else None,
            row["table_name"],
        )
        id_mapping[key] = row["uuid"]

    return id_mapping


def get_all_id_references(
    base_table_name: str, schema: DataSchema = None
) -> dict[str, list[str]]:
    """Make mapping table name to list of all columns that reference table_name's id

    Args:
        schema: DataSchema object
        base_table_name: Name of the table to get all id references for

    Returns:
        Dictionary mapping table name to list of all columns that reference table_name's id
    """
    if schema is None:
        schema = DataSchema(DEFAULT_SCHEMA_PATH)
    id_references = {table_name: [] for table_name in schema.schema}
    if "id" in schema.schema[base_table_name].attributes:
        id_references[base_table_name].append("id")
    for table_name in schema.schema:
        for foreign_key_column, foreign_table_name in schema.schema[
            table_name
        ].relations.items():
            if foreign_table_name == base_table_name:
                id_references[table_name].append(f"{foreign_key_column}_id")
    return id_references
