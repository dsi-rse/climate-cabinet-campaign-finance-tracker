"""Ensure all columns are present in the correct type"""

import warnings

import pandas as pd

from utils.schema import DataSchema, TableSchema, parse_decimal_type

# How many out-of-range values a decimal error lists
_MAX_LISTED_OUT_OF_RANGE = 5

# Schema types held as pandas' nullable "boolean", and the text read as each
# value (compared ignoring case and surrounding spaces)
_BOOLEAN_TYPES = ("bool", "boolean")
_TRUE_TEXT = frozenset({"true", "t", "yes", "y", "1", "1.0"})
_FALSE_TEXT = frozenset({"false", "f", "no", "n", "0", "0.0"})
# How many unreadable values a boolean warning lists
_MAX_LISTED_UNREADABLE = 5


def _to_boolean_column(values: pd.Series, column_name: str) -> pd.Series:
    """Convert a column to pandas' nullable boolean type without guessing

    astype(bool) treats any non-empty text, and missing values, as True, so
    "False" and nulls both became True. Here, booleans and the numbers 1 and 0
    keep their meaning, text is read from _TRUE_TEXT and _FALSE_TEXT, and
    missing values stay missing.

    Args:
        values: The column to convert.
        column_name: Name of the column, used in the warning.

    Returns:
        The column as "boolean". Values that can't be read become missing,
        and a warning lists how many there were, with examples.
    """
    text = values.astype("string").str.strip().str.lower()
    result = pd.Series(pd.NA, index=values.index, dtype="boolean")
    result[text.isin(_TRUE_TEXT).fillna(False).to_numpy()] = True
    result[text.isin(_FALSE_TEXT).fillna(False).to_numpy()] = False
    unreadable = values.notna().to_numpy() & result.isna().to_numpy()
    if unreadable.any():
        examples = values[unreadable].astype(str).unique()[:_MAX_LISTED_UNREADABLE]
        warnings.warn(
            f"{int(unreadable.sum())} values in {column_name!r} are not true or"
            f" false and became null: {list(examples)}",
            stacklevel=3,
        )
    return result


def _to_decimal_column(
    values: pd.Series, column_name: str, precision: int, scale: int
) -> pd.Series:
    """Hold a decimal(precision, scale) column as Float64 rounded to scale places

    Args:
        values: The column to convert. Anything pandas can cast to Float64
            works, including numeric strings; missing values stay null.
        column_name: Name of the column, used in the error message.
        precision: Total number of digits the decimal type allows.
        scale: Number of those digits after the decimal point.

    Returns:
        The column as Float64, rounded to `scale` decimal places.

    Raises:
        ValueError: if a value has more digits before the decimal point than
            the type allows, since DuckDB would refuse to load the whole table
    """
    numbers = values.astype("Float64").round(scale)
    limit = 10 ** (precision - scale)
    out_of_range = numbers.abs() >= limit
    if out_of_range.any():
        examples = numbers[out_of_range].head(_MAX_LISTED_OUT_OF_RANGE).tolist()
        raise ValueError(
            f"{int(out_of_range.sum())} values in {column_name!r} are too large for"
            f" decimal({precision},{scale}), which allows less than {limit:,}:"
            f" {examples}"
        )
    return numbers


def clean_table_columns(
    table_data: pd.DataFrame, table_schema: TableSchema
) -> pd.DataFrame:
    """Based on config, ensure all columns are present in the correct type

    Args:
        table_data: Dataframe of the table to clean
        table_schema: Schema for the table.

    Returns:
        Cleaned dataframe with all columns present and in the correct type.
        Columns of type "decimal(p,s)" are Float64 rounded to s places, and
        columns of type "bool" are pandas' nullable "boolean".

    Raises:
        ValueError: if a decimal column has a value too large for its type
    """
    for column_name in table_schema.attributes:
        if column_name not in table_data.columns:
            table_data[column_name] = None

    for column_name, column_type in table_schema.types.items():
        decimal_type = parse_decimal_type(column_type)
        if decimal_type is not None:
            table_data[column_name] = _to_decimal_column(
                table_data[column_name], column_name, *decimal_type
            )
        elif column_type in _BOOLEAN_TYPES:
            table_data[column_name] = _to_boolean_column(
                table_data[column_name], column_name
            )
        elif pd.api.types.is_integer_dtype(pd.api.types.pandas_dtype(column_type)):
            # through numbers first, so text like "2020.0" (a whole number
            # written from a float column and read back from CSV) converts;
            # a value with a fraction still raises
            table_data[column_name] = pd.to_numeric(table_data[column_name]).astype(
                column_type
            )
        else:
            table_data[column_name] = table_data[column_name].astype(column_type)
    return table_data


def clean_database_columns(
    database: dict[str, pd.DataFrame], schema_file: dict
) -> dict[str, pd.DataFrame]:
    """Based on config, ensure all columns are present in the correct type

    Args:
        database: Dictionary mapping table names to dataframes
        schema_file: Path to a yaml file with details about the database schema.
            All table names in the database should be keys in the yaml file and have
            the following attributes:
                - 'attributes': a list of all column names
                - 'types': (optional) a dictionary mapping column names to their type.
                  If a column is not in the 'types' dictionary, it will be assumed to
                  be a string.

    Returns:
        Cleaned database with all columns present and in the correct type
    """
    database_schema = DataSchema(schema_file)
    for table_name, table_data in database.items():
        database[table_name] = clean_table_columns(
            table_data, database_schema.schema[table_name]
        )
    return database
