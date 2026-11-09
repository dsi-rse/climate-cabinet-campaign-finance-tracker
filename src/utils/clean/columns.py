"""Ensure all columns are present in the correct type"""

import pandas as pd

from utils.schema import DataSchema, TableSchema, parse_decimal_type

# How many out-of-range values a decimal error lists
_MAX_LISTED_OUT_OF_RANGE = 5


def _to_decimal_column(
    values: pd.Series, column_name: str, precision: int, scale: int
) -> pd.Series:
    """Hold a decimal(precision, scale) column as Float64 rounded to scale places

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
        Columns of type "decimal(p,s)" are Float64 rounded to s places.

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
