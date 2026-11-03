"""Core functions for cleaning data from normalized database"""

from pathlib import Path

import pandas as pd

from utils.clean.address import clean_address
from utils.clean.columns import clean_database_columns
from utils.clean.transactor import clean_transactors


def clean_data(
    database: dict[str, pd.DataFrame], schema_file: Path
) -> dict[str, pd.DataFrame]:
    """Clean data from normalized database

    Args:
        database: dictionary of pandas DataFrames
        schema_file: Path to a yaml file with details about the database schema.

    Returns:
        dictionary of pandas DataFrames
    """
    database = clean_database_columns(database, schema_file)
    if "Transactor" in database:
        database["Transactor"] = clean_transactors(database["Transactor"])
    if "Address" in database:
        database["Address"] = clean_address(database["Address"])
    return database
