"""Merge raw state campaign finance into standardized schema"""

from pathlib import Path

import pandas as pd

from utils.standardize.source_standardization_registry import (
    get_registered_sources,
    register_all_data_source_pipelines,
)


def standardize_state(
    state: str,
    start_year: int = None,
    end_year: int = None,
    input_directory: Path | None = None,
) -> dict[str, pd.DataFrame]:
    """From raw datafiles, standardize data from specified state.

    Args:
        state: State to merge data from.
        start_year: Year to start filtering data from. If None,
            will default to the earliest year in the data
        end_year: Year to end filtering data at. If None,
            will default to the latest year in the data
        input_directory: Path to directory containing raw data. If None,
            will default to 'data/raw'. Will look for data in the state'
            subdirectory of this directory (i.e. data/raw/IL)

    Returns:
        dictionary mapping table name to tables of that type
    """
    register_all_data_source_pipelines(state)
    all_data_source_pipelines = get_registered_sources()

    database = {}
    for source in all_data_source_pipelines[state]:
        standardized_source_table = source.load_and_standardize_data_source(
            start_year=start_year,
            end_year=end_year,
            state_data_directory=input_directory / state,
        )
        if source.table_name not in database:
            database[source.table_name] = pd.DataFrame()

        database[source.table_name] = pd.concat(
            [database[source.table_name], standardized_source_table],
        )

    return database
