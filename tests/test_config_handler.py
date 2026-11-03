from pathlib import Path

import pytest
import utils.standardize.config
from utils.standardize.config import ConfigHandler
from yaml import safe_dump


@pytest.fixture
def sample_config(tmp_path):
    config_data = {
        "contributions": {
            "column_details": [
                {"raw_name": "FILERID", "type": "str", "standard_name": "recipient_id"},
                {"raw_name": "CYCLE", "type": "str"},
                {
                    "raw_name": "CONTDATE1",
                    "type": "Int32",
                    "date_format": "%Y%m%d",
                    "standard_name": "date-1",
                },
            ],
            "enum_mapper": {"transactor_type": {"1": "Candidate", "2": "Committee"}},
            "read_csv_params": {"sep": ",", "encoding": "latin-1"},
            "duplicate_columns": {"CYCLE": "REPORT_CYCLE"},
            "new_empty_columns": ["additional_column"],
            "state_code_columns": ["reported_election--state"],
            "state_code": "NY",
            "table_name": "Transaction",
            "path_pattern": "(?i)^(19[0-9][0-9]|20[0-1][0-9]|2020|2021)/contrib.*\\.txt$",
        }
    }
    config_path = tmp_path / "config.yaml"
    with config_path.open("w") as f:
        safe_dump(config_data, f)
    return config_path


@pytest.fixture
def exclude_column_order_config(tmp_path):
    config_data = {
        "contributions": {
            "column_details": [
                {"raw_name": "FILERID", "type": "str", "standard_name": "recipient_id"},
                {"raw_name": "CYCLE", "type": "str"},
                {
                    "raw_name": "CONTDATE1",
                    "type": "Int32",
                    "date_format": "%Y%m%d",
                    "standard_name": "date-1",
                },
            ],
            "read_csv_params": {"sep": ",", "encoding": "latin-1"},
            "include_column_order": False,
        }
    }
    config_path = tmp_path / "modified_config.yaml"
    with config_path.open("w") as f:
        safe_dump(config_data, f)
    return config_path


@pytest.fixture
def explicit_header_config(tmp_path):
    config_data = {
        "contributions": {
            "column_details": [
                {"raw_name": "FILERID", "type": "str", "standard_name": "recipient_id"},
                {"raw_name": "CYCLE", "type": "str"},
                {
                    "raw_name": "CONTDATE1",
                    "type": "Int32",
                    "date_format": "%Y%m%d",
                    "standard_name": "date-1",
                },
            ],
            "include_column_order": False,
            "read_csv_params": {"sep": ",", "encoding": "latin-1", "header": 1},
        }
    }
    config_path = tmp_path / "explicit_header_config.yaml"
    with config_path.open("w") as f:
        safe_dump(config_data, f)
    return config_path


@pytest.fixture
def mock_raw_data_directory(tmp_path, monkeypatch):
    """Override RAW_DATA_DIRECTORY with a temporary path."""
    monkeypatch.setattr(utils.standardize.config, "RAW_DATA_DIRECTORY", tmp_path)
    return tmp_path


def test_config_handler_init(sample_config):
    handler = ConfigHandler("contributions", config_file_path=sample_config)
    assert handler._column_details, "Columns should be loaded from config"
    assert handler._enum_mapper, "Enum mapper should be loaded from config"
    assert handler._read_csv_params, "Read CSV parameters should be loaded from config"
    assert handler._table_name, "table name must be loaded from config"
    assert handler._raw_data_path_pattern, "Path pattern must be loaded from config"


def test_dtype_dict(sample_config):
    handler = ConfigHandler("contributions", config_file_path=sample_config)
    assert handler.dtype_dict == {
        "FILERID": "str",
        "CYCLE": "str",
        "CONTDATE1": "Int32",
    }


def test_column_mapper(sample_config):
    handler = ConfigHandler("contributions", config_file_path=sample_config)
    assert handler.column_mapper == {"FILERID": "recipient_id", "CONTDATE1": "date-1"}


def test_relevant_columns(sample_config):
    handler = ConfigHandler("contributions", config_file_path=sample_config)
    assert set(handler.relevant_columns) == {"FILERID", "CONTDATE1"}


def test_enum_mapper(sample_config):
    handler = ConfigHandler("contributions", config_file_path=sample_config)
    assert handler.enum_mapper == {
        "transactor_type": {"1": "Candidate", "2": "Committee"}
    }


def test_read_csv_params_include_column_order(sample_config):
    """Test read_csv_params when include_column_order is True"""
    handler = ConfigHandler("contributions", config_file_path=sample_config)
    expected_params = {
        "sep": ",",
        "encoding": "latin-1",
        "names": ["FILERID", "CYCLE", "CONTDATE1"],
    }
    assert (
        handler.read_csv_params == expected_params
    ), "read_csv_params should include column names"


def test_read_csv_params_no_include_column_order(exclude_column_order_config):
    """Test read_csv_params when include_column_order is False"""
    handler = ConfigHandler(
        "contributions", config_file_path=exclude_column_order_config
    )
    expected_params = {"sep": ",", "encoding": "latin-1", "header": 0}
    assert (
        handler.read_csv_params == expected_params
    ), "read_csv_params should not include column names when include_column_order is False"


def test_read_csv_params_explicit_header(explicit_header_config):
    """Test read_csv_params when an explicit header value is provided in the config"""
    handler = ConfigHandler("contributions", config_file_path=explicit_header_config)
    expected_params = {"sep": ",", "encoding": "latin-1", "header": 1}
    assert (
        handler.read_csv_params == expected_params
    ), "read_csv_params should preserve explicitly set header values"


def test_column_to_date_format(sample_config):
    handler = ConfigHandler("contributions", config_file_path=sample_config)
    assert handler.column_to_date_format == {"date-1": "%Y%m%d"}


def test_duplicate_columns(sample_config):
    handler = ConfigHandler("contributions", config_file_path=sample_config)
    assert handler.duplicate_columns == {"CYCLE": "REPORT_CYCLE"}


def test_new_empty_columns(sample_config):
    handler = ConfigHandler("contributions", config_file_path=sample_config)
    assert handler.new_empty_columns == ["additional_column"]


def test_state_code_columns(sample_config):
    handler = ConfigHandler("contributions", config_file_path=sample_config)
    assert handler.state_code_columns == ["reported_election--state"]


def test_invalid_form_code(sample_config):
    with pytest.raises(KeyError):
        ConfigHandler("invalid_form", config_file_path=sample_config)


def test_missing_file():
    with pytest.raises(FileNotFoundError):
        ConfigHandler("contributions", config_file_path=Path("non_existent.yaml"))


def test_raw_data_path_pattern(sample_config):
    handler = ConfigHandler("contributions", config_file_path=sample_config)
    assert handler.raw_data_path_pattern.fullmatch("2021/contrib_test.txt")
    assert not handler.raw_data_path_pattern.fullmatch("2018/noncontrib.csv")


@pytest.fixture
def inheritance_config(tmp_path):
    config_data = {
        "base_form": {
            "column_details": [
                {
                    "raw_name": "PARENT_COLUMN",
                    "type": "str",
                    "standard_name": "base_col",
                },
                {
                    "raw_name": "EXTRA_COLUMN",
                    "type": "float",
                    "standard_name": "extra_col",
                },
            ],
            "column_order": ["EXTRA_COLUMN", "PARENT_COLUMN"],
            "enum_mapper": {"category": {"A": "Alpha", "B": "Beta"}},
            "read_csv_params": {"sep": "|"},
            "state_code": "CA",
            "table_name": "BaseTable",
            "path_pattern": "(?i)^base/.*\\.txt$",
        },
        "derived_form": {
            "inherits": "base_form",
            "column_details": [
                {
                    "raw_name": "CHILD_COLUMN",
                    "type": "int",
                    "standard_name": "derived_col",
                },
                {
                    "raw_name": "EXTRA_COLUMN",
                    "type": "float",
                    "standard_name": "extra_col",
                },
            ],
            "column_order": ["CHILD_COLUMN", "EXTRA_COLUMN"],
        },
        "column_subset": {"inherits": "base_form", "column_order": ["PARENT_COLUMN"]},
        "override_form": {
            "inherits": "base_form",
            "column_details": [
                {
                    "raw_name": "EXTRA_COLUMN",
                    "type": "str",
                    "standard_name": "overridden_value",
                },
            ],
        },
        "base_2": {
            "column_details": [
                {
                    "raw_name": "PARENT_COLUMN",
                    "type": "str",
                    "standard_name": "base_col_2",
                },
                {
                    "raw_name": "EXTRA_COLUMN_2",
                    "type": "str",
                    "standard_name": "value_to_override",
                },
                {
                    "raw_name": "PARENT_COLUMN_2",
                    "type": "str",
                    "standard_name": "base_col_2",
                },
            ],
        },
        "multi_override_form": {
            "inherits": "base_2",
            "column_details": [
                {
                    "raw_name": "EXTRA_COLUMN",
                    "type": "str",
                    "standard_name": "overridden_value",
                },
                {
                    "raw_name": "EXTRA_COLUMN_2",
                    "type": "str",
                    "standard_name": "overridden_value_2",
                },
            ],
        },
    }
    config_path = tmp_path / "config.yaml"
    with config_path.open("w") as f:
        safe_dump(config_data, f)
    return config_path


def test_overrides(inheritance_config):
    """Test a raw column in parent and child configs appears only as the child value"""
    handler = ConfigHandler("override_form", config_file_path=inheritance_config)
    extra_columns = [
        col for col in handler._column_details if col["raw_name"] == "EXTRA_COLUMN"
    ]
    assert len(extra_columns) == 1, "There should be exactly one EXTRA_COLUMN"
    assert (
        extra_columns[0]["standard_name"] == "overridden_value"
    ), "Standard name should be overridden"
    assert handler.dtype_dict["EXTRA_COLUMN"] == "str", "Dtype should be overridden"


def test_inherits(inheritance_config):
    """Test values get properly inherited from parent config"""
    handler = ConfigHandler("derived_form", config_file_path=inheritance_config)
    assert "PARENT_COLUMN" in [
        col["raw_name"] for col in handler._column_details
    ], "PARENT_COLUMN should be present as the derived form merges with the base form"
    assert "CHILD_COLUMN" in [
        col["raw_name"] for col in handler._column_details
    ], "CHILD_COLUMN should be present as it takes precedence over inherited column"
    assert (
        handler.table_name == "BaseTable"
    ), "table name should be inherited from base_form"
    assert handler.read_csv_params["sep"] == "|", "Read CSV params should be inherited"
    assert handler.enum_mapper == {
        "category": {"A": "Alpha", "B": "Beta"}
    }, "Enum mapper should be inherited"
    assert handler.raw_data_path_pattern.fullmatch(
        "base/data.txt"
    ), "Inherited path pattern should match"


def test_default_column_order_inheritance(inheritance_config):
    """Consistent column order with inherited column_details and no column_order"""
    handler = ConfigHandler("multi_override_form", config_file_path=inheritance_config)
    print(handler._column_order)
    print(handler._column_details)
    assert handler._column_order == [
        "PARENT_COLUMN",
        "EXTRA_COLUMN_2",
        "PARENT_COLUMN_2",
        "EXTRA_COLUMN",
    ]


def test_column_details_and_order(inheritance_config):
    handler = ConfigHandler("derived_form", config_file_path=inheritance_config)
    assert handler._column_order == [
        "CHILD_COLUMN",
        "EXTRA_COLUMN",
    ], "Columns should follow the column_order in derived_form"

    handler_base = ConfigHandler("base_form", config_file_path=inheritance_config)
    raw_names_base = [col["raw_name"] for col in handler_base._column_details]
    assert raw_names_base == [
        "PARENT_COLUMN",
        "EXTRA_COLUMN",
    ], "Base form should filter columns according to column_order"
    handler_subset = ConfigHandler("column_subset", inheritance_config)
    raw_names_subset = [col["raw_name"] for col in handler_subset._column_details]
    assert raw_names_subset == [
        "PARENT_COLUMN",
        "EXTRA_COLUMN",
    ], "Both columns should be present, since column_order only subsets for table read"


@pytest.fixture
def year_filter_config(tmp_path):
    """Configuration with year filtering settings"""
    config_data = {
        "contributions": {
            "column_details": [
                {"raw_name": "FILERID", "type": "str", "standard_name": "recipient_id"},
                {
                    "raw_name": "EYEAR",
                    "type": "Int32",
                    "standard_name": "election_year",
                },
                {
                    "raw_name": "CONTDATE1",
                    "type": "Int32",
                    "date_format": "%Y%m%d",
                    "standard_name": "date-1",
                },
            ],
            "state_code": "PA",
            "table_name": "Transaction",
            "path_pattern": "(?i)^(\\d{4})/contrib.*\\.txt$",
            "year_filter_filepath_regex": "^(\\d{4})/",
            "year_column": "EYEAR",
        }
    }
    config_path = tmp_path / "year_filter_config.yaml"
    with config_path.open("w") as f:
        safe_dump(config_data, f)
    return config_path


def test_year_filter_filepath_regex(year_filter_config):
    """Test that year_filter_filepath_regex is correctly loaded"""
    handler = ConfigHandler("contributions", config_file_path=year_filter_config)
    assert handler.year_filter_filepath_regex == "^(\\d{4})/"


def test_year_column(year_filter_config):
    """Test that year_column is correctly loaded"""
    handler = ConfigHandler("contributions", config_file_path=year_filter_config)
    assert handler.year_column == "EYEAR"


def test_year_filter_none_when_not_configured(sample_config):
    """Test that year filtering properties return None when not configured"""
    handler = ConfigHandler("contributions", config_file_path=sample_config)
    assert handler.year_filter_filepath_regex is None
    assert handler.year_column is None
