"""Code for management and creation of unique identifiers

Ids are uuid5s: the same inputs always give the same id, so re-running a step
on the same data, with the same chunking and input format, gives the same ids.
A source id is hashed with its year, state and table. A row without a source id
is hashed with its table and its values, as read.
"""

import json
import re
import uuid
from collections.abc import Iterable
from pathlib import Path

import pandas as pd

from utils.constants import DEFAULT_SCHEMA_PATH
from utils.io import replace_file_atomically
from utils.schema import DataSchema, TableSchema

# Matches any RFC 4122 uuid. Values that don't match are treated as raw ids.
UUID_REGEX = re.compile(
    r"^[a-f0-9]{8}-[a-f0-9]{4}-[1-8][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$",
    re.IGNORECASE,
)

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


# "222.0" or "-5.00", but not "0042" or "1.5"
_WHOLE_NUMBER_WITH_ZERO_DECIMALS = re.compile(r"^-?[1-9]\d*\.0+$|^0\.0+$")


def _id_part(value: object) -> str | None:
    """Write one value the same way however it was read

    Args:
        value: A value from a table or an id mapping key.

    Returns:
        None for a missing value. Otherwise the value as text, with whole
        numbers written without a decimal point, so 222, 222.0, "222" and
        "222.0" all give "222". Text like "222.0" comes from a number written
        to CSV and read back as text. Leading zeros are kept ("0042").
    """
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    text = normalize_id_to_string(value)
    if _WHOLE_NUMBER_WITH_ZERO_DECIMALS.match(text):
        return text.split(".")[0]
    return text


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
    return _uuid_from_text_parts(
        [table_name, *(item for pair in pairs for item in pair)]
    )


def _uuid_from_text_parts(parts: list[str]) -> str:
    """Make the uuid deterministic_uuid gives for parts that are already text

    Args:
        parts: Values already written by _id_part (none missing).

    Returns:
        The uuid as a string, equal to deterministic_uuid(parts).
    """
    return str(uuid.uuid5(ID_NAMESPACE, json.dumps(parts)))


def _column_id_parts(column: pd.Series) -> list[str | None]:
    """Write each value of a column as _id_part does

    Args:
        column: One column of a table.

    Returns:
        One entry per row: None for a missing value, otherwise its text.
    """
    if pd.api.types.is_string_dtype(column.dtype) and not pd.api.types.is_object_dtype(
        column.dtype
    ):
        # every value is already text or missing, so _id_part would keep it
        return column.astype(object).where(column.notna(), None).tolist()
    return [_id_part(value) for value in column]


def replace_null_ids_with_uuids(
    table: pd.DataFrame,
    id_column: str,
    table_name: str = "",
    content_columns: list[str] | None = None,
) -> None:
    """For each null value in id_column, fill in a uuid made from the row

    Rows with the same table name and the same values in content_columns get
    the same uuid in any run. Values are hashed as read, so a value read with
    a different type (for example a zip code read as a number from a csv
    chunk) gives a different uuid.

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
    # the same as _row_content_uuid for each row, but each column is written
    # once and the columns are sorted once (sorting the (column, value) pairs
    # of a row orders them by column, since a row has one value per column)
    missing_rows = table.loc[missing_mask]
    columns = sorted(content_columns)
    column_parts = [_column_id_parts(missing_rows[column]) for column in columns]
    uuids = []
    for row_parts in (
        zip(*column_parts, strict=True) if columns else [()] * len(missing_rows)
    ):
        parts = [table_name]
        for column, part in zip(columns, row_parts):
            if part is not None:
                parts.append(column)
                parts.append(part)
        uuids.append(_uuid_from_text_parts(parts))
    table.loc[missing_mask, id_column] = uuids


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


def _mapping_key(
    raw_id: object, year: object, reported_state: object, table_name: str
) -> tuple[str, int | None, str | None, str]:
    """Build the key a raw id is mapped by, however its parts were read

    Args:
        raw_id: The state's id as read: 222, 222.0 and "222" all give "222".
        year: The row's year as read: 2020, 2020.0, or missing.
        reported_state: The row's state, or missing.
        table_name: Name of the table the id identifies a row of.

    Returns:
        (raw id as text, year as an int or None, state or None, table name).
        Missing values become None, so a key built from a table row equals
        the same key read back from a saved mapping. A NaN never equals
        itself, so a key holding one could never be found again.
    """
    return (
        normalize_id_to_string(raw_id),
        None if _id_part(year) is None else int(float(year)),
        None if _id_part(reported_state) is None else str(reported_state),
        table_name,
    )


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
    # write each raw id as text the same way however it was read (222 and
    # 222.0 both become "222") before casting, so it looks up the same key
    table[id_column] = pd.Series(
        _column_id_parts(table[id_column]), index=table.index, dtype="string"
    )
    table.loc[mask, id_column] = table.loc[mask].apply(
        lambda row: id_mapping.get(
            _mapping_key(
                row[id_column], row.get("year"), row.get("reported_state"), table_name
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
        key = _mapping_key(
            row[id_column], row.get("year"), row.get("reported_state"), table_name
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
    # a year is written as a whole number and a missing one as blank, so the
    # file reads back as it was saved even when some rows have no year
    id_mapping_df["year"] = id_mapping_df["year"].astype("Int64")
    file_path.parent.mkdir(parents=True, exist_ok=True)
    # written to a temporary file and renamed, so a crash never leaves a
    # half-written mapping
    replace_file_atomically(
        file_path, lambda path: id_mapping_df.to_csv(path, sep="\t", index=False)
    )


def load_id_mapping(file_path: Path) -> UUIDMapping:
    """Load ID mapping from a TSV file.

    Args:
        file_path: Path to the saved mapping file

    Returns:
        Dictionary mapping tuples to UUIDs
    """
    if not file_path.exists():
        return {}

    # raw ids are saved already written by normalize_id_to_string, so read
    # them back as text to match the keys new rows are looked up by
    id_mapping_df = pd.read_csv(
        file_path,
        sep="\t",
        dtype={"raw_id": str, "reported_state": str, "table_name": str, "uuid": str},
        keep_default_na=False,
        na_values={"year": [""], "reported_state": [""]},
    )
    # Convert DataFrame back to mapping format
    id_mapping = {}
    for _, row in id_mapping_df.iterrows():
        key = _mapping_key(
            row["raw_id"], row["year"], row["reported_state"], row["table_name"]
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
