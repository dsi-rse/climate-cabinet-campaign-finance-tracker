"""Code for management and creation of unique identifiers

Ids are uuid5s: the same inputs always give the same id, so re-running a step
on the same data gives the same ids. A source id is hashed with its year, state
and table. A row without a source id is hashed with its table and its values.
"""

import json
import re
import uuid
from collections.abc import Iterable
from pathlib import Path

import pandas as pd

from utils.constants import DEFAULT_SCHEMA_PATH
from utils.schema import DataSchema, TableSchema

# Matches any RFC 4122 uuid: the uuid5s made here, and uuid4s made by earlier
# versions of the pipeline. Values that don't match are treated as raw ids.
UUID_REGEX = re.compile(
    r"^[a-f0-9]{8}-[a-f0-9]{4}-[1-8][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$",
    re.IGNORECASE,
)
# kept for code that imported the old name
UUID4_REGEX = UUID_REGEX

# Namespace for every id the pipeline makes. Changing it changes every id.
ID_NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL,
    "https://github.com/dsi-rse/climate-cabinet-campaign-finance-tracker/ids",
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


def _id_part(value: object) -> str | None:
    """Write one value the same way however it was read

    Args:
        value: A value from a table or an id mapping key.

    Returns:
        None for a missing value. Otherwise the value as text, with whole
        numbers written without a decimal point, so 222, 222.0 and "222" all
        give "222".
    """
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    return normalize_id_to_string(value)


def deterministic_uuid(parts: Iterable[object]) -> str:
    """Make a uuid5 from a sequence of values

    Args:
        parts: The values that identify the thing being given an id, in a
            fixed order. Missing values and numbers are written as in _id_part.

    Returns:
        The uuid as a string. The same parts always give the same uuid.
    """
    name = json.dumps([_id_part(part) for part in parts])
    return str(uuid.uuid5(ID_NAMESPACE, name))


def _row_content_uuid(
    table_name: str, columns: list[str], values: Iterable[object]
) -> str:
    """Make a uuid5 from a table name and one row's non-missing values

    Args:
        table_name: Name of the table the id identifies a row of.
        columns: Names of the columns the values come from.
        values: The row's values, in the same order as columns.

    Returns:
        The uuid as a string. Columns that are missing in the row don't change
        it, so adding an empty column keeps the same ids.
    """
    pairs = sorted(
        (column, part)
        for column, value in zip(columns, values)
        if (part := _id_part(value)) is not None
    )
    return deterministic_uuid([table_name, *(item for pair in pairs for item in pair)])


def replace_null_ids_with_uuids(
    table: pd.DataFrame,
    id_column: str,
    table_name: str = "",
    content_columns: list[str] | None = None,
) -> None:
    """For each null value in id_column, fill in a uuid made from the row

    Rows with the same table name and the same values in content_columns get
    the same uuid, in any run and any chunk.

    Args:
        table: DataFrame where each row requires a UUID.
        id_column: Name of the column to be populated with UUIDs.
        table_name: Name of the table the id identifies a row of. It is part of
            the uuid, so rows of different tables with the same values get
            different ids.
        content_columns: Columns whose values identify the row. Defaults to
            every column except id_column.

    Modifies:
        table: Assigns UUIDs to rows where `id_column` is missing.
    """
    missing_mask = table[id_column].isna()
    if not missing_mask.any():
        return
    if content_columns is None:
        content_columns = [column for column in table.columns if column != id_column]
    rows = table.loc[missing_mask, content_columns].itertuples(index=False, name=None)
    table.loc[missing_mask, id_column] = [
        _row_content_uuid(table_name, content_columns, row) for row in rows
    ]


def source_id_uuid(key: tuple[str, object, object, str]) -> str:
    """Make the uuid for a source id

    Args:
        key: (raw id, year, reported state, table name), as used in id
            mappings.

    Returns:
        The uuid as a string. The same raw id, year, state and table always
        give the same uuid, whether the raw id was read as 222, 222.0 or "222".
    """
    return deterministic_uuid(["source_id", *key])


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
        & (~table[id_column].astype("string").str.match(UUID_REGEX, na=False))
    ].iterrows():
        key = (
            normalize_id_to_string(row[id_column]),
            row.get("year"),
            row.get("reported_state"),
            table_name,
        )
        if key not in existing_mapping:
            new_mappings[key] = source_id_uuid(key)

    return new_mappings


def get_raw_ids_mask(table: pd.DataFrame, id_column: str) -> pd.Series:
    """Create a mask that is true where raw ids exist

    Args:
        table: DataFrame with existing `id_column`.
        id_column: Name of the `id` column
    """
    # na=True because a null is *not* a raw id
    not_raw_id_mask = table[id_column].astype("string").str.match(UUID_REGEX, na=True)
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

    replace_null_ids_with_uuids(table, id_column, table_name=id_table_name)
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
