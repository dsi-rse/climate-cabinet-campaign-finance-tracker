"""Database I/O utilities for CSV and Parquet formats."""

from collections.abc import Generator
from pathlib import Path
from typing import Literal

import pandas as pd
import pyarrow.parquet as pq

from utils.constants import ID_SUFFIX, SPLIT

FileFormat = Literal["csv", "parquet"]


def load_database(
    path: Path,
    format: FileFormat = "csv",
    chunk_size: int | None = None,
) -> Generator[dict[str, pd.DataFrame], None, None]:
    """Load database from directory containing table files.

    Args:
        path: Directory containing data files
        format: File format to read ('csv' or 'parquet')
        chunk_size: If specified, returns chunked generator for memory efficiency

    Returns:
        Generator yielding database chunks (dict of table_name -> DataFrame).
        If chunk_size is None, yields a single chunk containing the full database.
    """
    if chunk_size is None:
        # Yield the full database as a single chunk
        yield _load_database_full(path, format)
        return
    yield from _load_database_chunked(path, format, chunk_size)


def save_database(
    database: dict[str, pd.DataFrame],
    path: Path,
    format: FileFormat = "csv",
    mode: Literal["overwrite", "append"] = "overwrite",
) -> None:
    """Save database to directory with one file per table.

    Args:
        database: Dictionary mapping table names to DataFrames
        path: Directory to save files
        format: File format to use ('csv' or 'parquet')
        mode: Whether to overwrite existing files or append
    """
    path.mkdir(parents=True, exist_ok=True)

    for table_name, df in database.items():
        print(f"Saving table {table_name} to {path / f'{table_name}.{format}'}")
        file_path = path / f"{table_name}.{format}"
        _save_table(df, file_path, format, mode)


def load_table_chunked(
    file_path: Path,
    chunk_size: int,
    format: FileFormat = "csv",
) -> Generator[pd.DataFrame, None, None]:
    """Load single table in memory-efficient chunks.

    Args:
        file_path: Path to data file
        chunk_size: Number of rows per chunk
        format: File format to read

    Yields:
        DataFrame chunks
    """
    if format == "csv":
        yield from pd.read_csv(file_path, chunksize=chunk_size)
    elif format == "parquet":
        total_rows = _count_rows(file_path)
        for start in range(0, total_rows, chunk_size):
            end = min(start + chunk_size, total_rows)
            yield _read_parquet_chunk(file_path, start, end)
    else:
        raise ValueError(f"Invalid format: {format}")


# Private utility functions


def _load_database_full(path: Path, format: FileFormat) -> dict[str, pd.DataFrame]:
    """Load entire database into memory."""
    database = {}

    for file_path in _get_data_files(path, format):
        table_name = file_path.stem
        database[table_name] = _load_table(file_path, format)

    return database


def _load_database_chunked(
    path: Path, format: FileFormat, chunk_size: int
) -> Generator[dict[str, pd.DataFrame], None, None]:
    """Load database in chunks across all tables simultaneously."""
    table_files = {f.stem: f for f in _get_data_files(path, format)}
    table_positions = dict.fromkeys(table_files, 0)
    table_totals = {
        name: _count_rows(file_path) for name, file_path in table_files.items()
    }

    while any(pos < table_totals[name] for name, pos in table_positions.items()):
        chunk_db = {}

        for table_name, file_path in table_files.items():
            start = table_positions[table_name]
            if start >= table_totals[table_name]:
                continue

            end = min(start + chunk_size, table_totals[table_name])
            chunk_db[table_name] = _read_chunk(file_path, start, end, format)
            table_positions[table_name] = end

        if chunk_db:
            yield chunk_db


def _get_data_files(path: Path, format: FileFormat) -> list[Path]:
    """Get all data files with the specified format."""
    return [f for f in path.iterdir() if f.suffix == f".{format}"]


def _load_table(file_path: Path, format: FileFormat) -> pd.DataFrame:
    """Load a single table file."""
    if format == "csv":
        try:
            return pd.read_csv(file_path)
        except pd.errors.EmptyDataError:
            return pd.DataFrame()
    elif format == "parquet":
        return pd.read_parquet(file_path)


def convert_string_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Convert all string columns to string type and replace NaNs with None"""
    for col in df.columns:
        if df[col].dtype == object:
            # Check if the column contains at least one string-like value
            if df[col].apply(lambda x: isinstance(x, str)).any():
                # Convert column to string type and replace NaNs with None
                df[col] = df[col].astype("string").where(df[col].notna(), None)
    return df


def convert_parquet_na_to_none(df: pd.DataFrame) -> pd.DataFrame:
    """Convert <NA> strings present in parquet files to None in pandas"""
    for col in df.columns:
        if (
            df[col].dtype == object
            or col.endswith(ID_SUFFIX)  # an id is going to interpreted as a string
            or col.split(SPLIT)[-1] == "id"
        ):
            # Check if column contains the literal '<NA>' string
            if df[col].astype(str).str.contains("^<NA>$", na=False).any():
                df[col] = df[col].astype("string")
                # Replace '<NA>' strings with proper NaN values
                # df[col] = df[col].replace("<NA>", pd.NA)
                # Convert to pandas nullable string type to preserve NA handling
    return df


def _save_table(
    df: pd.DataFrame,
    file_path: Path,
    format: FileFormat,
    mode: Literal["overwrite", "append"],
) -> None:
    """Save a single table file."""
    if df.empty:
        return
    save_index = bool(df.index.name)

    if format == "csv":
        write_header = mode == "overwrite" or not file_path.exists()
        write_mode = "w" if mode == "overwrite" else "a"
        df.to_csv(file_path, index=save_index, header=write_header, mode=write_mode)

    elif format == "parquet":
        if mode == "append" and file_path.exists():
            # TODO: this reads the entire file into memory. There should be a better way
            existing_df = pd.read_parquet(file_path)
            combined_df = pd.concat([existing_df, df], ignore_index=True)
            combined_df.to_parquet(file_path, index=save_index)
        else:
            proper_type_df = convert_string_columns(df)
            proper_type_df.to_parquet(file_path, index=save_index)


def _count_rows(file_path: Path) -> int:
    """Count rows in a data file."""
    if file_path.suffix == ".csv":
        return sum(1 for _ in file_path.open()) - 1  # Exclude header
    elif file_path.suffix == ".parquet":
        return pq.read_metadata(file_path).num_rows


def _read_chunk(
    file_path: Path, start_row: int, end_row: int, format: FileFormat
) -> pd.DataFrame:
    """Read a specific chunk from a file."""
    if format == "csv":
        return pd.read_csv(
            file_path, skiprows=range(1, start_row + 1), nrows=end_row - start_row
        )
    elif format == "parquet":
        return _read_parquet_chunk(file_path, start_row, end_row)


def _read_parquet_chunk(file_path: Path, start_row: int, end_row: int) -> pd.DataFrame:
    """Read a chunk from a parquet file using row groups for efficiency."""
    parquet_file = pq.ParquetFile(file_path)

    # Find which row groups contain the requested rows
    row_groups_to_read = []
    current_row = 0

    for i in range(parquet_file.num_row_groups):
        row_group_metadata = parquet_file.metadata.row_group(i)
        row_group_size = row_group_metadata.num_rows
        row_group_end = current_row + row_group_size

        # Check if this row group overlaps with our target range
        if current_row < end_row and row_group_end > start_row:
            row_groups_to_read.append(i)

        current_row = row_group_end

        # Stop if we've passed our target range
        if current_row >= end_row:
            break

    # Read only the necessary row groups
    if not row_groups_to_read:
        # Return empty DataFrame with correct schema
        schema_table = parquet_file.read_row_group(0, columns=None).slice(0, 0)
        return schema_table.to_pandas()

    # Read the selected row groups
    tables = []
    for row_group_idx in row_groups_to_read:
        table = parquet_file.read_row_group(row_group_idx)
        tables.append(table)

    # Concatenate row groups if multiple were read
    if len(tables) == 1:
        combined_table = tables[0]
    else:
        import pyarrow as pa

        combined_table = pa.concat_tables(tables)

    # Convert to pandas and slice to exact range
    pandas_df = combined_table.to_pandas()
    pandas_df = convert_parquet_na_to_none(pandas_df)

    # Calculate the offset within our read data
    first_row_group_start = 0
    for i in range(row_groups_to_read[0]):
        first_row_group_start += parquet_file.metadata.row_group(i).num_rows

    # Adjust indices relative to the data we actually read
    local_start = max(0, start_row - first_row_group_start)
    local_end = min(len(pandas_df), end_row - first_row_group_start)

    return pandas_df.iloc[local_start:local_end]
