from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from utils.standardize.config import ConfigHandler
from utils.standardize.source import (
    DataReader,
    DataSourceStandardizationPipeline,
    DataStandardizer,
    SchemaTransformer,
)


# ---- FIXTURES ----
@pytest.fixture
def mock_config_handler():
    """Creates a mock configuration handler with dummy mappings."""
    mock = MagicMock(spec=ConfigHandler)
    mock.dtype_dict = {
        "DONOR": "str",
        "RECIPID": "Int32",
        "TRANSDATE": "str",
        "DONORCOMPNAME": "str",
        "amount": "Float32",
        "EOFFICE": "str",
        "USELESS": "str",
    }
    mock.read_csv_params = {"sep": ","}
    mock.raw_data_file_paths = [Path("contributions.csv")]
    mock.raw_column_order = [
        "DONOR",
        "RECIPID",
        "TRANSDATE",
        "DONORCOMPNAME",
        "amount",
        "EOFFICE",
        "USELESS",
    ]
    mock.column_mapper = {
        "DONOR": "donor--name",
        "RECIPID": "recipient_id",
        "TRANSDATE": "date",
        "DONORCOMPNAME": "donor--employer--organization--full_name",
        "EOFFICE": "donor--election_result--election--office_sought",
    }
    mock.relevant_columns = [
        "donor--name",
        "recipient_id",
        "date",
        "amount",
        "donor--employer--organization--full_name",
        "donor--election_result--election--office_sought",
    ]
    mock.duplicate_columns = {"amount": ["duplicate_amount"]}
    mock.enum_mapper = {
        "donor--election_result--election--office_sought": {
            "SEN": "State Senator",
            "REP": "State Representative",
        }
    }
    mock.column_to_date_format = {"date": "%m/%d/%Y"}
    mock.year_filter_filepath_regex = None
    mock.year_column = None
    mock._null_values = {}
    mock._filter = {}
    return mock


@pytest.fixture
def raw_data():
    """Sample raw data to use in tests"""
    return pd.DataFrame(
        {
            "DONOR": ["person 1", "person 2", "person 3"],
            "RECIPID": [1234, 4567, 910],
            "TRANSDATE": ["1/20/2025", "12/31/2024", "3/4/2024"],
            "DONORCOMPNAME": ["big company", "medium company", "small comp"],
            "amount": [120.34, 1200, 3400.23],
            "EOFFICE": ["SEN", "SEN", "REP"],
            "USELESS": ["non", "sense", "data"],
        }
    )


@pytest.fixture
def renamed_columns_data():
    """Sample raw data with standardized column names"""
    return pd.DataFrame(
        {
            "donor--name": ["person 1", "person 2", "person 3"],
            "recipient_id": [1234, 4567, 910],
            "date": ["1/20/2025", "12/31/2024", "3/4/2024"],
            "donor--employer--organization--full_name": [
                "big company",
                "medium company",
                "small comp",
            ],
            "amount": [120.34, 1200, 3400.23],
            "donor--election_result--election--office_sought": ["SEN", "SEN", "REP"],
            "USELESS": ["non", "sense", "data"],
        }
    )


@pytest.fixture
def standardized_schema():
    """Sample raw data with standardized column names"""
    return pd.DataFrame(
        {
            "donor--name": ["person 1", "person 2", "person 3"],
            "recipient_id": [1234, 4567, 910],
            "date": ["1/20/2025", "12/31/2024", "3/4/2024"],
            "donor--employer--organization--full_name": [
                "big company",
                "medium company",
                "small comp",
            ],
            "amount": [120.34, 1200, 3400.23],
            "duplicate_amount": [120.34, 1200, 3400.23],
            "donor--election_result--election--office_sought": ["SEN", "SEN", "REP"],
        }
    )


@pytest.fixture
def standardized_data():
    """Sample raw data with standardized column names"""
    return pd.DataFrame(
        {
            "donor--name": ["person 1", "person 2", "person 3"],
            "recipient_id": [1234, 4567, 910],
            "date": ["2025-01-20", "2024-12-31", "2024-03-04"],
            "donor--employer--organization--full_name": [
                "big company",
                "medium company",
                "small comp",
            ],
            "amount": [120.34, 1200, 3400.23],
            "duplicate_amount": [120.34, 1200, 3400.23],
            "donor--election_result--election--office_sought": [
                "State Senator",
                "State Senator",
                "State Representative",
            ],
        }
    )


@pytest.fixture
def mock_config_handler_with_year_filter():
    """Creates a mock configuration handler with year filtering enabled."""
    mock = MagicMock(spec=ConfigHandler)
    mock.dtype_dict = {
        "DONOR": "str",
        "EYEAR": "Int32",
        "TRANSDATE": "str",
        "amount": "Float32",
    }
    mock.read_csv_params = {"sep": ","}
    mock.raw_data_file_paths = [
        Path("2020/contributions.csv"),
        Path("2021/contributions.csv"),
        Path("2022/contributions.csv"),
        Path("2023/contributions.csv"),
    ]
    mock.raw_column_order = ["DONOR", "EYEAR", "TRANSDATE", "amount"]
    mock.year_filter_filepath_regex = "^(\\d{4})/"
    mock.year_column = "EYEAR"
    mock._filter = {}
    return mock


@pytest.fixture
def year_filtered_data():
    """Sample data with year column"""
    return pd.DataFrame(
        {
            "DONOR": ["person 1", "person 2", "person 3", "person 4"],
            "EYEAR": [2020, 2021, 2022, 2023],
            "TRANSDATE": ["1/20/2020", "12/31/2021", "3/4/2022", "6/15/2023"],
            "amount": [120.34, 1200, 3400.23, 500.00],
        }
    )


# ---- UNIT TESTS ----
class TestDataReader:
    """Tests for DataReader class."""

    @patch("pandas.read_csv")
    def test_read_tabular_data(self, mock_read_csv, mock_config_handler, raw_data):
        """Ensure read_tabular_data reads CSV and applies dtype mapping."""
        mock_read_csv.return_value = pd.DataFrame(raw_data)
        reader = DataReader(mock_config_handler)

        result = reader.read_tabular_data(Path("contributions.csv"))

        mock_read_csv.assert_called_once_with(
            "contributions.csv", dtype=mock_config_handler.dtype_dict, sep=","
        )
        assert not result.empty
        assert list(result.columns) == [
            "DONOR",
            "RECIPID",
            "TRANSDATE",
            "DONORCOMPNAME",
            "amount",
            "EOFFICE",
            "USELESS",
        ]

    def test_missing_file_error(self, mock_config_handler):
        """Ensure an error is raised if the file is missing."""
        reader = DataReader(mock_config_handler)
        with pytest.raises(FileNotFoundError):
            reader.read_tabular_data(Path("non_existent_file.csv"))

    @patch("pandas.read_csv")
    def test_read_tabular_data_with_year_filtering(
        self, mock_read_csv, mock_config_handler, raw_data
    ):
        """Test read_tabular_data with year filtering parameters"""
        mock_read_csv.return_value = raw_data
        reader = DataReader(mock_config_handler)

        result = reader.read_tabular_data(
            Path("contributions.csv"), start_year=2020, end_year=2022
        )

        mock_read_csv.assert_called_once_with(
            "contributions.csv", dtype=mock_config_handler.dtype_dict, sep=","
        )
        assert not result.empty


class TestSchemaTransformer:
    """Tests for SchemaTransformer class."""

    def test_rename_columns(self, mock_config_handler, raw_data):
        """Test that column names are correctly renamed."""
        transformer = SchemaTransformer(mock_config_handler)
        renamed_column_data = transformer._rename_columns(raw_data)

        assert "donor--name" in renamed_column_data.columns
        assert "DONOR" not in renamed_column_data.columns

    def test_drop_unused_columns(self, mock_config_handler, renamed_columns_data):
        """Ensure only relevant columns remain."""
        transformer = SchemaTransformer(mock_config_handler)
        relevant_columns_data = transformer._drop_unused_columns(renamed_columns_data)

        assert set(relevant_columns_data.columns) == {
            "donor--name",
            "recipient_id",
            "date",
            "amount",
            "donor--employer--organization--full_name",
            "donor--election_result--election--office_sought",
        }

    def test_add_duplicate_columns(self, mock_config_handler, renamed_columns_data):
        """Ensure additional empty columns are added."""
        transformer = SchemaTransformer(mock_config_handler)
        duplicate_columns_data = transformer._add_duplicate_columns(
            renamed_columns_data
        )

        assert "duplicate_amount" in duplicate_columns_data.columns
        assert (
            duplicate_columns_data["duplicate_amount"]
            .eq(duplicate_columns_data["amount"])
            .all()
        )


class TestDataStandardizer:
    """Tests for DataStandardizer class."""

    def test_standardize_enums(self, mock_config_handler, renamed_columns_data):
        """Ensure enum values are mapped correctly."""
        standardizer = DataStandardizer(mock_config_handler)
        standardized_enum_data = standardizer._standardize_enums(renamed_columns_data)

        assert standardized_enum_data[
            "donor--election_result--election--office_sought"
        ].tolist() == [
            "State Senator",
            "State Senator",
            "State Representative",
        ]  # Enum mapping check

    def test_standardize_column_to_date_format(
        self, mock_config_handler, renamed_columns_data
    ):
        """Ensure date formats are correctly converted."""
        standardizer = DataStandardizer(mock_config_handler)
        standardized_date_data = standardizer._standardize_date_format(
            renamed_columns_data
        )
        assert [str(date) for date in standardized_date_data["date"].tolist()] == [
            "2025-01-20",
            "2024-12-31",
            "2024-03-04",
        ]


# ---- INTEGRATION TEST ----
@pytest.fixture
def mock_pipeline(
    mock_config_handler, raw_data, standardized_schema, standardized_data
):
    """Creates a fully mocked DataSourceStandardizationPipeline instance to prevent real initialization."""

    # Mock DataReader
    mock_data_reader = MagicMock()
    mock_data_reader.read_tabular_data.return_value = raw_data

    # Mock SchemaTransformer
    mock_schema_transformer = MagicMock()
    mock_schema_transformer.standardize_schema.return_value = standardized_schema

    # Mock DataStandardizer
    mock_data_standardizer = MagicMock()
    mock_data_standardizer.standardize_data.return_value = standardized_data

    # Patch the pipeline's __init__ method to prevent real initialization
    with patch.object(
        DataSourceStandardizationPipeline,
        "__init__",
        lambda self, *args, **kwargs: None,
    ):
        pipeline = DataSourceStandardizationPipeline()
        pipeline.state_code = "NY"
        pipeline.form_code = "FORM1"
        pipeline.data_reader = mock_data_reader
        pipeline.schema_transformer = mock_schema_transformer
        pipeline.data_standardizer = mock_data_standardizer
        pipeline._raw_data_file_paths = MagicMock(
            return_value=[Path("contributions.csv")]
        )
    return pipeline


def test_load_standardize_data_source(mock_pipeline, standardized_data):
    """Test standardize_data_source to ensure it processes data correctly without real I/O."""

    # Run the pipeline's standardization process
    result_df = mock_pipeline.load_and_standardize_data_source()

    # Validate the output
    pd.testing.assert_frame_equal(result_df, standardized_data)

    # Ensure each step was called once with the correct arguments
    mock_pipeline.data_reader.read_tabular_data.assert_called_once_with(
        Path("contributions.csv"), None, None
    )
    mock_pipeline.schema_transformer.standardize_schema.assert_called_once()
    mock_pipeline.data_standardizer.standardize_data.assert_called_once()


class TestDataReaderYearFiltering:
    """Tests for DataReader year filtering functionality."""

    def test_filter_year_by_filepath(self, mock_config_handler_with_year_filter):
        """Test filtering file paths by year"""
        reader = DataReader(mock_config_handler_with_year_filter)

        # Test filtering for years 2021-2022
        assert reader._is_filepath_in_year_range(
            Path("2021/contributions.csv"), 2021, 2022
        )
        assert reader._is_filepath_in_year_range(
            Path("2022/contributions.csv"), 2021, 2022
        )
        assert not reader._is_filepath_in_year_range(
            Path("2020/contributions.csv"), 2021, 2022
        )
        assert not reader._is_filepath_in_year_range(
            Path("2023/contributions.csv"), 2021, 2022
        )

    def test_filter_year_by_filepath_start_year_only(
        self, mock_config_handler_with_year_filter
    ):
        """Test filtering with only start year"""
        reader = DataReader(mock_config_handler_with_year_filter)

        assert reader._is_filepath_in_year_range(
            Path("2022/contributions.csv"), 2022, None
        )
        assert reader._is_filepath_in_year_range(
            Path("2023/contributions.csv"), 2022, None
        )
        assert not reader._is_filepath_in_year_range(
            Path("2021/contributions.csv"), 2022, None
        )

    def test_filter_year_by_filepath_end_year_only(
        self, mock_config_handler_with_year_filter
    ):
        """Test filtering with only end year"""
        reader = DataReader(mock_config_handler_with_year_filter)

        assert reader._is_filepath_in_year_range(
            Path("2020/contributions.csv"), None, 2021
        )
        assert reader._is_filepath_in_year_range(
            Path("2021/contributions.csv"), None, 2021
        )
        assert not reader._is_filepath_in_year_range(
            Path("2022/contributions.csv"), None, 2021
        )

    def test_filter_year_by_filepath_no_regex(self):
        """Test that filtering returns True when no regex is configured"""
        mock_handler = MagicMock()
        mock_handler.year_filter_filepath_regex = None
        mock_handler.year_column = None
        mock_handler.dtype_dict = {}
        mock_handler.read_csv_params = {}
        mock_handler.raw_column_order = []

        reader = DataReader(mock_handler)

        assert reader._is_filepath_in_year_range(Path("file1.csv"), 2021, 2022) is True
        assert reader._is_filepath_in_year_range(Path("file2.csv"), 2021, 2022) is True

    def test_filter_year_by_column(
        self, mock_config_handler_with_year_filter, year_filtered_data
    ):
        """Test filtering dataframe by year column"""
        reader = DataReader(mock_config_handler_with_year_filter)

        # Test filtering for years 2021-2022
        filtered_data = reader._filter_dataframe_to_year_range(
            year_filtered_data, 2021, 2022
        )
        expected_years = [2021, 2022]
        assert list(filtered_data["EYEAR"]) == expected_years

    def test_filter_year_by_column_start_year_only(
        self, mock_config_handler_with_year_filter, year_filtered_data
    ):
        """Test filtering with only start year"""
        reader = DataReader(mock_config_handler_with_year_filter)

        filtered_data = reader._filter_dataframe_to_year_range(
            year_filtered_data, 2022, None
        )
        expected_years = [2022, 2023]
        assert list(filtered_data["EYEAR"]) == expected_years

    def test_filter_year_by_column_end_year_only(
        self, mock_config_handler_with_year_filter, year_filtered_data
    ):
        """Test filtering with only end year"""
        reader = DataReader(mock_config_handler_with_year_filter)

        filtered_data = reader._filter_dataframe_to_year_range(
            year_filtered_data, None, 2021
        )
        expected_years = [2020, 2021]
        assert list(filtered_data["EYEAR"]) == expected_years

    def test_filter_year_by_column_no_year_column(self, year_filtered_data):
        """Test that filtering returns original data when no year column is configured"""
        mock_handler = MagicMock()
        mock_handler.year_filter_filepath_regex = None
        mock_handler.year_column = None
        mock_handler.dtype_dict = {}
        mock_handler.read_csv_params = {}
        mock_handler.raw_column_order = []

        reader = DataReader(mock_handler)

        filtered_data = reader._filter_dataframe_to_year_range(
            year_filtered_data, 2021, 2022
        )
        pd.testing.assert_frame_equal(filtered_data, year_filtered_data)

    def test_filter_year_by_column_missing_column(
        self, mock_config_handler_with_year_filter
    ):
        """Test that filtering returns original data when year column is missing from data"""
        reader = DataReader(mock_config_handler_with_year_filter)
        data_without_year = pd.DataFrame(
            {
                "DONOR": ["person 1", "person 2"],
                "amount": [120.34, 1200],
            }
        )

        filtered_data = reader._filter_dataframe_to_year_range(
            data_without_year, 2021, 2022
        )
        pd.testing.assert_frame_equal(filtered_data, data_without_year)

    @patch("pandas.read_csv")
    def test_read_tabular_data_with_year_filtering_single_file(
        self, mock_read_csv, mock_config_handler_with_year_filter, year_filtered_data
    ):
        """Test read_tabular_data with year filtering on a single file"""
        mock_read_csv.return_value = year_filtered_data
        reader = DataReader(mock_config_handler_with_year_filter)

        # Test reading a file that passes the year filter
        result = reader.read_tabular_data(
            Path("2021/contributions.csv"), start_year=2021, end_year=2022
        )

        mock_read_csv.assert_called_once_with(
            "2021/contributions.csv",
            dtype=mock_config_handler_with_year_filter.dtype_dict,
            sep=",",
        )
        # Should contain years 2021-2022 due to column filtering
        assert list(result["EYEAR"]) == [2021, 2022]

    @patch("pandas.read_csv")
    def test_read_tabular_data_filtered_out_by_filepath(
        self, mock_read_csv, mock_config_handler_with_year_filter
    ):
        """Test that read_tabular_data returns empty DataFrame when file is filtered out by filepath"""
        reader = DataReader(mock_config_handler_with_year_filter)

        # Try to read a file that doesn't match the year filter
        result = reader.read_tabular_data(
            Path("2020/contributions.csv"), start_year=2021, end_year=2022
        )

        # Should not call read_csv since file is filtered out
        mock_read_csv.assert_not_called()
        assert result.empty

    @patch("pandas.read_csv")
    def test_read_tabular_data_no_year_filtering(
        self, mock_read_csv, mock_config_handler_with_year_filter, year_filtered_data
    ):
        """Test read_tabular_data without year filtering"""
        mock_read_csv.return_value = year_filtered_data
        reader = DataReader(mock_config_handler_with_year_filter)

        result = reader.read_tabular_data(Path("2021/contributions.csv"))

        mock_read_csv.assert_called_once()
        pd.testing.assert_frame_equal(result, year_filtered_data)
