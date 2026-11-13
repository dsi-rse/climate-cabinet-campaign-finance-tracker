"""Database I/O utilities for CSV and Parquet formats."""

import shutil
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

import pandas as pd
import pyarrow.parquet as pq

from utils.constants import ID_SUFFIX, SPLIT

FileFormat = Literal["csv", "parquet"]

# CSV tables are read as text, so values like zip "01234" and id "0042" keep
# their leading zeros and don't change type with the rows around them. Only
# empty fields are missing; text such as "NA" or "null" is kept as written.
_CSV_TEXT_READ_PARAMS = {"dtype": str, "keep_default_na": False, "na_values": [""]}


@contextmanager
def staged_directory(final_directory: Path) -> Iterator[Path]:
    """Write a step's output to a staging folder and swap it in on success

    The staging folder is a hidden sibling of final_directory. If the block
    finishes, final_directory is replaced by the staging folder, so files from
    an earlier run that this run didn't write are gone. If the block raises,
    the staging folder is deleted and final_directory is left as it was.

    Args:
        final_directory: Folder the output should end up in, such as
            <data>/normalized/<state>.

    Yields:
        The staging folder to write to.
    """
    final_directory = Path(final_directory)
    staging = final_directory.parent / f".{final_directory.name}.staging"
    previous = final_directory.parent / f".{final_directory.name}.previous"
    # a run that died between the two renames below left the last good output
    # as .previous and no final folder; put it back before anything is removed
    if previous.exists() and not final_directory.exists():
        previous.rename(final_directory)
    for leftover in (staging, previous):
        if leftover.exists():
            shutil.rmtree(leftover)
    staging.mkdir(parents=True)
    try:
        yield staging
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    if final_directory.exists():
        final_directory.rename(previous)
    try:
        staging.rename(final_directory)
    except BaseException:
        # keep the last good output if the swap itself fails
        if previous.exists() and not final_directory.exists():
            previous.rename(final_directory)
        shutil.rmtree(staging, ignore_errors=True)
        raise
    if previous.exists():
        shutil.rmtree(previous)


def replace_file_atomically(file_path: Path, write: Callable[[Path], None]) -> None:
    """Write a file through a temporary file, so a crash never leaves it cut short

    Args:
        file_path: File to write.
        write: Function that writes the content to the path it is given.
    """
    file_path = Path(file_path)
    temporary_path = file_path.with_name(f".{file_path.name}.tmp")
    try:
        write(temporary_path)
        temporary_path.replace(file_path)
    finally:
        temporary_path.unlink(missing_ok=True)


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
        yield from pd.read_csv(file_path, chunksize=chunk_size, **_CSV_TEXT_READ_PARAMS)
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


def load_table(file_path: Path, format: FileFormat) -> pd.DataFrame:
    """Load a whole table file

    Args:
        file_path: The table's file.
        format: "csv" or "parquet". CSV columns are read as text.

    Returns:
        The table, or an empty DataFrame for an empty CSV file.
    """
    return _load_table(file_path, format)


def table_column(file_path: Path, format: FileFormat, column: str) -> pd.Series | None:
    """Read one column of a table file without loading the rest

    Args:
        file_path: The table's file.
        format: "csv" or "parquet".
        column: Column to read.

    Returns:
        The column, or None if the file has no such column.
    """
    if format == "csv":
        try:
            header = pd.read_csv(file_path, nrows=0).columns
        except pd.errors.EmptyDataError:
            return None
        if column not in header:
            return None
        return pd.read_csv(file_path, usecols=[column], **_CSV_TEXT_READ_PARAMS)[column]
    if column not in pq.read_schema(file_path).names:
        return None
    return pd.read_parquet(file_path, columns=[column])[column]


def _load_table(file_path: Path, format: FileFormat) -> pd.DataFrame:
    """Load a single table file."""
    if format == "csv":
        try:
            return pd.read_csv(file_path, **_CSV_TEXT_READ_PARAMS)
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
        appending = mode == "append" and file_path.exists()
        rows = _align_to_csv_header(df, file_path, save_index) if appending else df
        rows.to_csv(
            file_path,
            index=save_index,
            header=not appending,
            mode="a" if appending else "w",
        )

    elif format == "parquet":
        if mode == "append" and file_path.exists():
            # TODO: this reads the entire file into memory. There should be a better way
            existing_df = pd.read_parquet(file_path)
            combined_df = pd.concat([existing_df, df], ignore_index=True)
            combined_df.to_parquet(file_path, index=save_index)
        else:
            proper_type_df = convert_string_columns(df)
            proper_type_df.to_parquet(file_path, index=save_index)


def _align_to_csv_header(
    chunk: pd.DataFrame, file_path: Path, save_index: bool
) -> pd.DataFrame:
    """Put a chunk's columns in the order of an existing CSV file's header

    Appended rows are written without a header, so each value lands under the
    header in the same position. Columns the file has and the chunk doesn't
    are left empty.

    Args:
        chunk: Chunk to append.
        file_path: Existing CSV file.
        save_index: Whether the chunk's index is written as the first column.

    Returns:
        The chunk with the file's columns, in the file's order.

    Raises:
        ValueError: if the chunk has columns the file's header doesn't, since
            they can't be appended without rewriting the file.
    """
    header = list(pd.read_csv(file_path, nrows=0).columns)
    if save_index:
        header = header[1:]
    extra_columns = [column for column in chunk.columns if column not in header]
    if extra_columns:
        raise ValueError(
            f"Can't append to {file_path}: the chunk has columns its header"
            f" doesn't: {extra_columns}"
        )
    return chunk.reindex(columns=header)


def _count_rows(file_path: Path) -> int:
    """Count rows in a data file."""
    if file_path.suffix == ".csv":
        with file_path.open() as file:
            return sum(1 for _ in file) - 1  # Exclude header
    elif file_path.suffix == ".parquet":
        return pq.read_metadata(file_path).num_rows


def _read_chunk(
    file_path: Path, start_row: int, end_row: int, format: FileFormat
) -> pd.DataFrame:
    """Read a specific chunk from a file."""
    if format == "csv":
        return pd.read_csv(
            file_path,
            skiprows=range(1, start_row + 1),
            nrows=end_row - start_row,
            **_CSV_TEXT_READ_PARAMS,
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
