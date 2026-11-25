"""Merge raw state campaign finance into standardized schema"""

from pathlib import Path

import pandas as pd

from utils.constants import STATE_FINANCE_CONFIG_DIRECTORY
from utils.standardize.source import COERCED_VALUE_REPORT_COLUMNS
from utils.standardize.source_standardization_registry import (
    get_registered_sources,
    register_all_data_source_pipelines,
)


def standardize_state(
    state: str,
    start_year: int = None,
    end_year: int = None,
    input_directory: Path | None = None,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
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
        A dictionary mapping table name to tables of that type, and a table of
        the raw values that became null during standardization because they
        were not in an enum mapping, did not fit the date format, or were not
        numbers. It has one row per state, form, file, column, step and
        distinct raw value, with how many times that value occurred.

    Raises:
        ValueError: if no config exists for the state, or input_directory has
            no folder for it
        ConfigError: if a form in the state's config fails its checks
        HeaderMismatchError: if a file's columns are not the ones its form
            reads by position
    """
    state = state.lower()
    register_all_data_source_pipelines(state)
    all_data_source_pipelines = get_registered_sources()
    if state not in all_data_source_pipelines:
        known = sorted(p.stem for p in STATE_FINANCE_CONFIG_DIRECTORY.glob("*.yaml"))
        raise ValueError(
            f"No standardize config for state {state!r}. States with a config:"
            f" {', '.join(known)}. To add one, see CONTRIBUTING.md."
        )

    state_directory = (
        _state_directory(input_directory, state)
        if input_directory is not None
        else None
    )
    database = {}
    coerced_value_tables = []
    for source in all_data_source_pipelines[state]:
        standardized_source_table = source.load_and_standardize_data_source(
            start_year=start_year,
            end_year=end_year,
            state_data_directory=state_directory,
        )
        if source.table_name not in database:
            database[source.table_name] = pd.DataFrame()

        database[source.table_name] = pd.concat(
            [database[source.table_name], standardized_source_table],
        )
        # special sources may override loading and not record coerced values
        source_coerced_values = getattr(source, "coerced_values", None)
        if (
            isinstance(source_coerced_values, pd.DataFrame)
            and not source_coerced_values.empty
        ):
            coerced_value_tables.append(source_coerced_values)

    coerced_values = (
        pd.concat(coerced_value_tables, ignore_index=True)
        if coerced_value_tables
        else pd.DataFrame(columns=COERCED_VALUE_REPORT_COLUMNS)
    )
    return database, coerced_values


def _state_directory(input_directory: Path, state: str) -> Path:
    """The state's folder under the raw directory, whatever its case

    Args:
        input_directory: The raw data directory, with one folder per state.
        state: Lowercase state code.

    Returns:
        The folder named for the state, matched ignoring case.

    Raises:
        ValueError: if there is no such folder, naming the ones there are.
    """
    exact = input_directory / state
    if exact.is_dir():
        return exact
    matches = (
        [
            path
            for path in input_directory.iterdir()
            if path.is_dir() and path.name.lower() == state
        ]
        if input_directory.is_dir()
        else []
    )
    if matches:
        return matches[0]
    folders = (
        sorted(
            path.name
            for path in input_directory.iterdir()
            if path.is_dir() and not path.name.startswith(("_", "."))
        )
        if input_directory.is_dir()
        else []
    )
    raise ValueError(
        f"No raw data folder for {state!r} under {input_directory}"
        f" (folders there: {', '.join(folders) or 'none'}). Run collect for the"
        " state, or give --input-directory."
    )
