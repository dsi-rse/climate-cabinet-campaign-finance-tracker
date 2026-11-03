"""Ensure all columns are present in the correct type"""

import pandas as pd

from utils.schema import DataSchema, TableSchema


def clean_table_columns(
    table_data: pd.DataFrame, table_schema: TableSchema
) -> pd.DataFrame:
    """Based on config, ensure all columns are present in the correct type

    Args:
        table_data: Dataframe of the table to clean
        table_schema: Schema for the table.

    Returns:
        Cleaned dataframe with all columns present and in the correct type
    """
    for column_name in table_schema.attributes:
        if column_name not in table_data.columns:
            table_data[column_name] = None

    for column_name, column_type in table_schema.types.items():
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
