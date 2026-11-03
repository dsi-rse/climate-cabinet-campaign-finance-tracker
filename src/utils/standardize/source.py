"""Represents raw input data from states"""

import re
from pathlib import Path

import pandas as pd
from tqdm import tqdm

from utils.constants import RAW_DATA_DIRECTORY
from utils.standardize.config import ConfigHandler


class DataReader:
    """Reads raw state finance data into a pandas dataframe

    The finance data should be in a tabular form and should have a
    single schema.
    """

    @property
    def default_raw_data_paths(self) -> list[str | Path]:
        """Default paths to any files of this type"""
        return self._default_paths

    @property
    def dtype_dict(self) -> dict[str, str]:
        """Maps raw column names in"""
        return self._dtype_dict

    def __init__(
        self,
        config_handler: ConfigHandler | None = None,
    ) -> None:
        """Initialize new data reader with optional year filtering"""
        self._dtype_dict = config_handler.dtype_dict
        self.read_csv_params = config_handler.read_csv_params
        self.columns = config_handler.raw_column_order
        if config_handler.year_filter_filepath_regex:
            self.year_filter_filepath_regex = re.compile(
                config_handler.year_filter_filepath_regex
            )
        else:
            self.year_filter_filepath_regex = None
        self.year_column = config_handler.year_column
        self.filter = config_handler._filter

    def _is_filepath_in_year_range(
        self,
        path: Path,
        start_year: int | None = None,
        end_year: int | None = None,
    ) -> bool:
        """Filter file paths by year extracted from path"""
        if not self.year_filter_filepath_regex:
            return True

        match = self.year_filter_filepath_regex.search(str(path))
        if match:
            try:
                year = int(match.group(1))
                if (start_year is None or year >= start_year) and (
                    end_year is None or year <= end_year
                ):
                    return True
                else:
                    return False
            except (ValueError, IndexError):
                print(f"Error parsing year from path: {path}")
                return False
        else:
            return False

    def _filter_dataframe_to_year_range(
        self,
        table: pd.DataFrame,
        start_year: int | None = None,
        end_year: int | None = None,
    ) -> pd.DataFrame:
        """Filter table by year extracted from column"""
        if (start_year is None and end_year is None) or self.year_column is None:
            return table

        if self.year_column not in table.columns:
            print(f"Warning: Year column {self.year_column} not found in table")
            return table

        if start_year is not None:
            table = table[table[self.year_column] >= start_year]
        if end_year is not None:
            table = table[table[self.year_column] <= end_year]
        return table

    def _filter_data(self, raw_table: pd.DataFrame) -> pd.DataFrame:
        """Filter data based on filter configuration

        This uses the 'filter' key from the state config, mapping raw column names
        to a list of values to keep or (if the value appears in the 'NOT' key) to
        drop.
        """
        for column_name, filter_values in self.filter.items():
            if column_name not in raw_table.columns:
                continue
            if "NOT" in filter_values:
                raw_table = raw_table[
                    ~raw_table[column_name].isin(filter_values["NOT"])
                ]
            else:
                raw_table = raw_table[raw_table[column_name].isin(filter_values)]
        return raw_table

    def _drop_blank_rows(self, raw_table: pd.DataFrame) -> pd.DataFrame:
        """Drop rows with all blank values"""
        return raw_table.dropna(how="all")

    def read_tabular_data(
        self,
        path: str | Path,
        start_year: int | None = None,
        end_year: int | None = None,
    ) -> pd.DataFrame:
        """Read raw tabular data from state provided files into a DataFrame

        Args:
            path: Path to raw data file. If None, uses default_raw_data_paths
            start_year: Start year of elections to run pipeline on
            end_year: End year of elections to run pipeline on

        This method should maintain the data as closely as possible. The only
        permissible modification is the dropping of segments of data that are
        malformed and not able to be read into a dataframe. These rows should
        be (TODO #107) reported.
        """
        if not self._is_filepath_in_year_range(path, start_year, end_year):
            return pd.DataFrame()

        table = pd.read_csv(
            str(path),
            dtype=self.dtype_dict,
            **self.read_csv_params,
        )
        table = self._drop_blank_rows(table)
        table = self._filter_dataframe_to_year_range(table, start_year, end_year)
        table = self._filter_data(table)
        return table


class SchemaTransformer:
    """Transforms column names (? and adds additional columns)"""

    def __init__(self, config_handler: ConfigHandler | None = None) -> None:
        """Create a new SchemaTransformer object

        Args:
            config_handler: ConfigHandler, documented # TODO
        """
        self.column_mapper = config_handler.column_mapper
        self.relevant_columns = config_handler.relevant_columns
        self.duplicate_columns = config_handler.duplicate_columns
        self.new_empty_columns = config_handler.new_empty_columns
        self.state_code_columns = config_handler.state_code_columns
        self.state_code = config_handler.state_code
        self.overloaded_columns = config_handler.overloaded_columns

    def _split_overloaded_columns(
        self, standard_data_table: pd.DataFrame
    ) -> pd.DataFrame:
        """Split columns with multiple pieces of information into multiple columns

        Each raw column name that appears as a key in the 'overloaded_columns' key
        in the state config file is split into multiple columns. The only tables that
        are split are those that match the 'filter' in 'overloaded_columns'. See
        CONTRIBUTING.md for more details.

        This can only be done if the pieces of information are separated in a standard
        and consistent way. The DataStandardizer should not make assumptions.
        """
        for column_name, column_info in self.overloaded_columns.items():
            if column_name not in standard_data_table.columns:
                continue
            overloaded_mask = pd.Series(True, index=standard_data_table.index)
            for filter_column, filter_values in column_info.get("filter", {}).items():
                mask = standard_data_table[filter_column].isin(filter_values)
                overloaded_mask = overloaded_mask & mask
            extracted_names = standard_data_table.loc[
                overloaded_mask, column_name
            ].str.extract(column_info["pattern"])
            standard_data_table = standard_data_table.merge(
                extracted_names, how="left", left_index=True, right_index=True
            )
        return standard_data_table

    def _rename_columns(self, standard_data_table: pd.DataFrame) -> pd.DataFrame:
        """Rename columns"""
        standard_data_table = standard_data_table.rename(columns=self.column_mapper)
        return standard_data_table

    def _drop_unused_columns(self, standard_data_table: pd.DataFrame) -> pd.DataFrame:
        """Drop columns with information that are not used in internal schema"""
        standard_data_table = standard_data_table.loc[
            :, standard_data_table.columns.isin(self.relevant_columns)
        ]
        return standard_data_table

    def _add_duplicate_columns(self, standard_data_table: pd.DataFrame) -> pd.DataFrame:
        """Add new columns that are copies of existing columns

        This can be wanted in cases where a column represents a property of two
        separate entities. For example, a raw file might have a 'state' column
        that denotes both the state of the election the candidate is running for
        and the state in which the transaction took place. Duplicating here allows
        for the normalization logic to decompose tables without worrying about
        state specific configuration.
        """
        for base_column, duplicate_column_list in self.duplicate_columns.items():
            for duplicate_column in duplicate_column_list:
                standard_data_table[duplicate_column] = standard_data_table[base_column]
        return standard_data_table

    def _add_new_columns(self, standard_data_table: pd.DataFrame) -> pd.DataFrame:
        """Add new blank columns TODO: is this ever really needed?"""
        for column in self.new_empty_columns:
            standard_data_table[column] = None
        return standard_data_table

    def _add_state_code(self, standard_data_table: pd.DataFrame) -> pd.DataFrame:
        """Add state code to relevent columns"""
        for column in self.state_code_columns:
            standard_data_table[column] = self.state_code
        return standard_data_table

    def standardize_schema(self, raw_data_table: pd.DataFrame) -> pd.DataFrame:
        """Rename columns, remove unused columns, and add missing columns

        Args:
            raw_data_table: single dataframe with raw column names
        """
        raw_data_table = self._split_overloaded_columns(raw_data_table)
        relevant_raw_table = self._drop_unused_columns(raw_data_table)
        standard_relevant_column_table = self._rename_columns(relevant_raw_table)
        standard_relevant_column_table = self._add_state_code(
            standard_relevant_column_table
        )
        standard_relevant_column_table = self._add_new_columns(
            standard_relevant_column_table
        )
        standard_schema_table = self._add_duplicate_columns(
            standard_relevant_column_table
        )

        return standard_schema_table


class DataStandardizer:
    """Ensures data conforms to expected types"""

    def __init__(self, config_handler: ConfigHandler | None = None) -> None:
        """Create a new DataStandardizer object

        Args:
            config_handler: ConfigHandler, documented # TODO
        """
        self.enum_mapper = config_handler.enum_mapper
        self.column_to_date_format = config_handler.column_to_date_format
        self.null_values = config_handler._null_values
        self.post_load_float_columns = config_handler.post_load_float_columns

    def _standardize_enums(self, standard_schema_table: pd.DataFrame) -> pd.DataFrame:
        """Rename entity type columns"""
        for column_name, column_enum_map in self.enum_mapper.items():
            if column_name not in standard_schema_table.columns:
                continue
            # map each value in the table's column according to the provided enum mapper
            standard_schema_table[column_name] = standard_schema_table[
                column_name
            ].astype(str)
            standard_schema_table[column_name] = standard_schema_table[column_name].map(
                column_enum_map
            )
        return standard_schema_table

    def _standardize_date_format(
        self, standard_schema_table: pd.DataFrame
    ) -> pd.DataFrame:
        """For each column with a configured date format, convert dates to ISO standard

        Args:
            standard_schema_table: table that has already passed through
                SchemaTransformer (most revelantly the column names should
                be standard)
        """
        for date_column, date_format in self.column_to_date_format.items():
            na_mask = standard_schema_table.loc[:, date_column].isna()
            temp_column = f"tmp-{date_column}"
            standard_schema_table[temp_column] = pd.NA

            # handle unix timestamp
            if "%unix_ms" in date_format:
                # Create regex pattern from date_format by replacing %unix_ms with (\d+)
                regex_pattern = re.escape(date_format).replace(r"%unix_ms", r"(\d+)")
                unix_ms_series = (
                    standard_schema_table.loc[~na_mask, date_column]
                    .str.extract(regex_pattern)[0]
                    .astype(float)
                )
                # Convert milliseconds to seconds and then to datetime
                unix_s_series = unix_ms_series / 1000
                standard_schema_table.loc[~na_mask, temp_column] = pd.to_datetime(
                    unix_s_series, unit="s", errors="coerce"
                ).dt.date
            # Handle regular date formats
            else:
                standard_schema_table.loc[~na_mask, temp_column] = pd.to_datetime(
                    standard_schema_table.loc[~na_mask, date_column],
                    format=date_format,
                    errors="coerce",
                ).dt.date

            standard_schema_table = standard_schema_table.drop(columns=date_column)
            standard_schema_table = standard_schema_table.rename(
                columns={temp_column: date_column}
            )
        return standard_schema_table

    def _standardize_null_values(
        self, standard_schema_table: pd.DataFrame
    ) -> pd.DataFrame:
        """Replace implicit null values with actual null values"""
        for column_name, null_values in self.null_values.items():
            if column_name not in standard_schema_table.columns:
                continue
            for implicit_null_value in null_values:
                standard_schema_table[column_name] = standard_schema_table[
                    column_name
                ].replace(implicit_null_value, pd.NA)
        return standard_schema_table

    def _standardize_transaction_direction(
        self, standard_schema_table: pd.DataFrame
    ) -> pd.DataFrame:
        """Flip donor and recipient columns if 'transaction_direction' is 'reverse'

        In state config, if a raw column is mapped to transaction_direction and
        the enum mapper maps some values to 'reverse' those rows will switch
        donor and recipient columns.
        """
        if "transaction_direction" not in standard_schema_table.columns:
            return standard_schema_table
        reverse_mask = standard_schema_table["transaction_direction"] == "reverse"
        reverse_table = standard_schema_table.loc[reverse_mask, :]
        new_reverse_table_columns = []
        for column in standard_schema_table.columns:
            if column.startswith("donor"):
                new_reverse_table_columns.append(column.replace("donor", "recipient"))
            elif column.startswith("recipient"):
                new_reverse_table_columns.append(column.replace("recipient", "donor"))
            else:
                new_reverse_table_columns.append(column)
        reverse_table.columns = new_reverse_table_columns
        standard_direction_table = pd.concat(
            [standard_schema_table.loc[~reverse_mask, :], reverse_table]
        )
        return standard_direction_table

    def _standardize_organization_name(
        self, standard_schema_table: pd.DataFrame
    ) -> pd.DataFrame:
        """Convert any specified organization names to full_name and transactor_type organization"""
        organization_name_columns = [
            column
            for column in standard_schema_table.columns
            if column.endswith("organization_name")
        ]
        for column in organization_name_columns:
            column_prefix = column[: -len("organization_name")]
            organization_name_rows = standard_schema_table[column].notna()
            standard_schema_table.loc[
                organization_name_rows, f"{column_prefix}full_name"
            ] = standard_schema_table.loc[organization_name_rows, column]
            standard_schema_table.loc[
                organization_name_rows, f"{column_prefix}transactor_type"
            ] = "Organization"
            standard_schema_table = standard_schema_table.drop(columns=[column])

        return standard_schema_table

    def _convert_strings_to_floats(
        self, standard_schema_table: pd.DataFrame
    ) -> pd.DataFrame:
        """Convert strings to floats"""
        for column in self.post_load_float_columns:
            if column not in standard_schema_table.columns:
                continue
            standard_schema_table[column] = pd.to_numeric(
                standard_schema_table[column].str.replace(",", "").str.replace("$", ""),
                errors="coerce",
            )
        return standard_schema_table

    def standardize_data(
        self,
        standard_schema_table: pd.DataFrame,
        enum_mapper: dict[dict[str, str]] | None = None,
        column_to_date_format: dict[str, str] | None = None,
    ) -> pd.DataFrame:
        """Transform data like enums and dates to correct format

        Transformation should only be done where there is no ambiguity. For
        example: 'January 30, 2025' can be safely transformed to '2025-1-30',
        but 'First Name Last Name' in a 'Full Name' column cannot be split
        without assumptions

        Args:
            standard_schema_table: table with TODO standard schema
            enum_mapper: dict mapping column names to dicts mapping
                raw values in the enum column to their standard values
            column_to_date_format: dict mapping column names to their date format
        """
        standard_schema_table = self._standardize_null_values(standard_schema_table)
        if enum_mapper is not None:
            self.enum_mapper = enum_mapper
        if column_to_date_format is not None:
            self.column_to_date_format = column_to_date_format
        standard_schema_table = self._standardize_enums(standard_schema_table)
        standard_data_table = self._standardize_date_format(standard_schema_table)
        standard_data_table = self._standardize_transaction_direction(
            standard_data_table
        )
        standard_data_table = self._standardize_organization_name(standard_data_table)
        standard_data_table = self._convert_strings_to_floats(standard_data_table)
        return standard_data_table


class DataSourceStandardizationPipeline:
    """Run pipeline for a single data source from file to standardized dataframe"""

    @property
    def table_name(self) -> str:
        """Table name that pipeline standardizes"""
        return self.config_handler.table_name

    def __init__(
        self,
        state_code: str,
        form_code: str,
        config_file: Path = None,
        data_reader: DataReader = None,
        schema_transformer: SchemaTransformer = None,
        data_standardizer: DataStandardizer = None,
    ) -> None:
        """Initialize data source standardization for a single data source

        Args:
            state_code: two letter abbreviation for state
            form_code: code to identify data source form type in state config file
            config_file: Path to config file. If not provided, state's default
                config file in config/finance/{state_code}.yaml will be used
            data_reader: logic for reading data into raw dataframe. If none provided
                will use default logic with provided config
            schema_transformer: logic for transforming schema of raw data. If none
                provided, will use default logic with provided config
            data_standardizer: logic for standardizing data of the raw file. If
                none provided, will use default logic with provided config.
        """
        self.state_code = state_code.lower()
        self.form_code = form_code
        if config_file is not None:
            self.config_handler = ConfigHandler(form_code, config_file_path=config_file)
        else:
            self.config_handler = ConfigHandler(form_code, state_code=self.state_code)
        if data_reader is None:
            self.data_reader = DataReader(self.config_handler)
        else:
            self.data_reader = data_reader
        if schema_transformer is None:
            self.schema_transformer = SchemaTransformer(self.config_handler)
        else:
            self.schema_transformer = schema_transformer
        if data_standardizer is None:
            self.data_standardizer = DataStandardizer(self.config_handler)
        else:
            self.data_standardizer = data_standardizer

    def _raw_data_file_paths(
        self, state_data_directory: Path | None = None
    ) -> list[Path]:
        """All files matching raw data pattern at compile time

        Args:
            state_data_directory: Directory containing raw data for state.
        """
        if state_data_directory is None:
            state_data_directory = RAW_DATA_DIRECTORY / self.state_code.upper()
        matching_files = [
            path
            for path in state_data_directory.rglob("*")
            if path.is_file()
            and self.config_handler.raw_data_path_pattern.fullmatch(
                str(path.relative_to(state_data_directory)).replace("\\", "/")
            )
        ]
        return matching_files

    def load_and_standardize_data_source(
        self,
        start_year: int | None = None,
        end_year: int | None = None,
        state_data_directory: Path | None = None,
    ) -> pd.DataFrame:
        """Load and process all data for a source into a single concatenated dataframe

        Args:
            start_year: Start year of elections to run pipeline on
            end_year: End year of elections to run pipeline on
            state_data_directory: Directory containing raw data for state. This the path
                up to what is described in the config file.
                Defaults to: repo root / data / raw / state_code
        """
        raw_data_file_paths = self._raw_data_file_paths(state_data_directory)
        standardized_tables = []
        if raw_data_file_paths == []:
            return pd.DataFrame()

        progress_bar = tqdm(
            raw_data_file_paths,
            desc="Processing files",
            unit="file",
            total=len(raw_data_file_paths),
            dynamic_ncols=True,
            leave=True,
        )

        for data_path in progress_bar:
            # Update progress bar description to show current file
            progress_bar.set_description(f"Processing: {data_path.name}")

            raw_data_table = self.data_reader.read_tabular_data(
                data_path, start_year, end_year
            )
            if raw_data_table.empty:
                continue

            standard_schema_table = self.schema_transformer.standardize_schema(
                raw_data_table
            )
            standard_data_table = self.data_standardizer.standardize_data(
                standard_schema_table
            )
            standardized_tables.append(standard_data_table)

        # Clear the description when done
        progress_bar.set_description("Processing complete")
        progress_bar.close()

        if standardized_tables:
            return pd.concat(standardized_tables, ignore_index=True)
        else:
            return pd.DataFrame()
