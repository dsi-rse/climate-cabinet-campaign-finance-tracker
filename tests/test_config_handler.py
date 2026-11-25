import re
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
                {
                    "raw_name": "FILERTYPE",
                    "type": "str",
                    "standard_name": "recipient--transactor_type_specific",
                },
            ],
            "enum_mapper": {
                "recipient--transactor_type_specific": {
                    "1": "Candidate",
                    "2": "Committee",
                }
            },
            "read_csv_params": {"sep": ",", "encoding": "latin-1"},
            "duplicate_columns": {"reported_state": ["recipient--address--state"]},
            "new_empty_columns": ["description"],
            "state_code_columns": ["reported_state"],
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
            "state_code": "zz",
            "table_name": "Transaction",
            "path_pattern": ".*",
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
            "state_code": "zz",
            "table_name": "Transaction",
            "path_pattern": ".*",
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
        "FILERTYPE": "str",
    }


def test_column_mapper(sample_config):
    handler = ConfigHandler("contributions", config_file_path=sample_config)
    assert handler.column_mapper == {
        "FILERID": "recipient_id",
        "CONTDATE1": "date-1",
        "FILERTYPE": "recipient--transactor_type_specific",
    }


def test_relevant_columns(sample_config):
    handler = ConfigHandler("contributions", config_file_path=sample_config)
    assert set(handler.relevant_columns) == {"FILERID", "CONTDATE1", "FILERTYPE"}


def test_enum_mapper(sample_config):
    handler = ConfigHandler("contributions", config_file_path=sample_config)
    assert handler.enum_mapper == {
        "recipient--transactor_type_specific": {"1": "Candidate", "2": "Committee"}
    }


def test_read_csv_params_include_column_order(sample_config):
    """Test read_csv_params when include_column_order is True"""
    handler = ConfigHandler("contributions", config_file_path=sample_config)
    expected_params = {
        "sep": ",",
        "encoding": "latin-1",
        "names": ["FILERID", "CYCLE", "CONTDATE1", "FILERTYPE"],
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
    assert handler.duplicate_columns == {
        "reported_state": ["recipient--address--state"]
    }


def test_new_empty_columns(sample_config):
    handler = ConfigHandler("contributions", config_file_path=sample_config)
    assert handler.new_empty_columns == ["description"]


def test_state_code_columns(sample_config):
    handler = ConfigHandler("contributions", config_file_path=sample_config)
    assert handler.state_code_columns == ["reported_state"]


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
                    "standard_name": "amount",
                },
                {
                    "raw_name": "EXTRA_COLUMN",
                    "type": "float",
                    "standard_name": "description",
                },
            ],
            "column_order": ["EXTRA_COLUMN", "PARENT_COLUMN"],
            "enum_mapper": {"description": {"A": "Alpha", "B": "Beta"}},
            "read_csv_params": {"sep": "|"},
            "state_code": "CA",
            "table_name": "Transaction",
            "path_pattern": "(?i)^base/.*\\.txt$",
        },
        "derived_form": {
            "inherits": "base_form",
            "column_details": [
                {
                    "raw_name": "CHILD_COLUMN",
                    "type": "int",
                    "standard_name": "date",
                },
                {
                    "raw_name": "EXTRA_COLUMN",
                    "type": "float",
                    "standard_name": "description",
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
                    "standard_name": "recipient--full_name",
                },
            ],
            # the parent's mapping is for a column this form no longer has
            "enum_mapper": {"recipient--full_name": {"A": "Alpha"}},
        },
        "base_2": {
            "column_details": [
                {
                    "raw_name": "PARENT_COLUMN",
                    "type": "str",
                    "standard_name": "amount",
                },
                {
                    "raw_name": "EXTRA_COLUMN_2",
                    "type": "str",
                    "standard_name": "recipient_id",
                },
                {
                    "raw_name": "PARENT_COLUMN_2",
                    "type": "str",
                    "standard_name": "donor_id",
                },
            ],
            "state_code": "zz",
            "table_name": "Transaction",
            "path_pattern": ".*",
        },
        "multi_override_form": {
            "inherits": "base_2",
            "column_details": [
                {
                    "raw_name": "EXTRA_COLUMN",
                    "type": "str",
                    "standard_name": "description",
                },
                {
                    "raw_name": "EXTRA_COLUMN_2",
                    "type": "str",
                    "standard_name": "recipient--full_name",
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
        extra_columns[0]["standard_name"] == "recipient--full_name"
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
        handler.table_name == "Transaction"
    ), "table name should be inherited from base_form"
    assert handler.read_csv_params["sep"] == "|", "Read CSV params should be inherited"
    assert handler.enum_mapper == {
        "description": {"A": "Alpha", "B": "Beta"}
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
                    "standard_name": "reported_election_year",
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


# ---- validation: what a config may say ----


def minimal_config() -> dict:
    """A Transaction form that passes validation, to break one thing at a time"""
    return {
        "contributions": {
            "state_code": "zz",
            "table_name": "Transaction",
            "path_pattern": "(?i)^contrib.*\\.csv$",
            "state_code_columns": ["reported_state"],
            "reported_by": "recipient",
            "column_details": [
                {"raw_name": "FILERID", "type": "str", "standard_name": "recipient_id"},
                {
                    "raw_name": "NAME",
                    "type": "str",
                    "standard_name": "donor--full_name",
                },
                {
                    "raw_name": "TYPE",
                    "type": "str",
                    "standard_name": "donor--transactor_type",
                },
                {"raw_name": "AMT", "type": "Float64", "standard_name": "amount"},
                {
                    "raw_name": "DT",
                    "type": "str",
                    "standard_name": "date",
                    "date_format": "%m/%d/%Y",
                },
                {"raw_name": "NOTE", "type": "str"},
            ],
            "enum_mapper": {
                "donor--transactor_type": {"I": "Individual", "O": "Organization"}
            },
            "null_values": {"amount": [0.0]},
            "filter": {"NOTE": {"NOT": ["void"]}},
        }
    }


def write_config(tmp_path, config) -> Path:
    path = tmp_path / "zz.yaml"
    with path.open("w") as f:
        safe_dump(config, f)
    return path


def test_minimal_config_is_valid(tmp_path):
    handler = ConfigHandler(
        "contributions", config_file_path=write_config(tmp_path, minimal_config())
    )
    assert handler.table_name == "Transaction"


def set_in(config, *path_and_value):
    *path, value = path_and_value
    target = config
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return config


@pytest.mark.parametrize(
    ("break_it", "message"),
    [
        (
            lambda c: set_in(c, "contributions", "colum_order", ["FILERID"]),
            "unknown key(s) colum_order",
        ),
        (
            lambda c: c["contributions"]["column_details"].append(
                {"raw_name": "X", "type": "str", "standard_nme": "amount"}
            ),
            "column 'X': unknown key(s) standard_nme",
        ),
        (
            lambda c: set_in(
                c,
                "contributions",
                "column_details",
                1,
                "standard_name",
                "donor--adress--city",
            ),
            "'adress' is not a relation of Transactor",
        ),
        (
            lambda c: set_in(
                c, "contributions", "column_details", 3, "standard_name", "amoun"
            ),
            "'amoun' is not an attribute of Transaction",
        ),
        (
            lambda c: set_in(
                c,
                "contributions",
                "column_details",
                3,
                "standard_name",
                "donor--occupation--x",
            ),
            "'occupation' is not a relation of Transactor",
        ),
        (
            lambda c: set_in(
                c,
                "contributions",
                "enum_mapper",
                {"recipient--transactor_type": {"I": "Individual"}},
            ),
            "'recipient--transactor_type' is not a column this form produces",
        ),
        (
            lambda c: set_in(
                c,
                "contributions",
                "enum_mapper",
                "donor--transactor_type",
                "I",
                "Person",
            ),
            "maps to Person, not among its values in table.yaml: Individual, Organization",
        ),
        (
            lambda c: set_in(c, "contributions", "null_values", {"amount-1": [0]}),
            "null_values: 'amount-1' is not a column this form produces",
        ),
        (
            lambda c: set_in(c, "contributions", "filter", {"NOTES": ["x"]}),
            "filter: 'NOTES' is not a raw column",
        ),
        (
            lambda c: set_in(c, "contributions", "filter", {"NOTE": {"NOT": "void"}}),
            "must be a list, or NOT: [values]",
        ),
        (
            lambda c: set_in(
                c,
                "contributions",
                "overloaded_columns",
                {"NAME": {"pattern": "^(?P<first>\\w+) (?P<generated_last>\\w+)$"}},
            ),
            "extracts 'first', which is not in column_details",
        ),
        (lambda c: c["contributions"].pop("table_name"), "table_name is required"),
        (
            lambda c: set_in(c, "contributions", "table_name", "Transactions"),
            "table_name 'Transactions' is not a table in table.yaml (one of",
        ),
        (lambda c: c["contributions"].pop("path_pattern"), "path_pattern is required"),
        (
            lambda c: set_in(c, "contributions", "path_pattern", "(unclosed"),
            "not a valid regex",
        ),
        (
            lambda c: set_in(c, "contributions", "reported_by", "payer"),
            "reported_by must be one of donor, recipient, not 'payer'",
        ),
        (
            lambda c: set_in(
                c, "contributions", "column_details", 3, "optional", "yes"
            ),
            "column 'AMT': optional must be true or false",
        ),
        (
            lambda c: set_in(c, "contributions", "column_details", 3, "type", "Int3"),
            "type 'Int3' is not a pandas dtype",
        ),
        (
            lambda c: set_in(
                c, "contributions", "column_details", 1, "standard_name", "amount"
            ),
            "standard_name 'amount' is given to 2 columns the form reads ('NAME', 'AMT')",
        ),
        (
            lambda c: set_in(
                c,
                "contributions",
                "column_details",
                5,
                "standard_name",
                "transaction_direction",
            ),
            "transaction_direction needs an enum_mapper entry",
        ),
        (
            lambda c: set_in(
                c,
                "contributions",
                "column_details",
                5,
                {"raw_name": "NOTE", "type": "str", "date_format": "%Y"},
            ),
            "column 'NOTE': date_format needs a standard_name",
        ),
        (
            lambda c: c["contributions"]["column_details"].append(
                {"raw_name": "AMT", "type": "str"}
            ),
            "raw_name repeated in column_details: AMT",
        ),
        (
            lambda c: set_in(
                c, "contributions", "duplicate_columns", {"donor_id": ["recipient_id"]}
            ),
            "duplicate_columns: 'donor_id' is not a column this form produces",
        ),
        (
            lambda c: set_in(c, "contributions", "year_column", "YEAR"),
            "year_column 'YEAR' is not a raw column",
        ),
        (
            lambda c: set_in(c, "contributions", "year_filter_filepath_regex", "(x"),
            "year_filter_filepath_regex '(x' is not a valid regex",
        ),
        (
            lambda c: set_in(
                c, "contributions", "year_filter_filepath_regex", "\\d{4}"
            ),
            "needs a capture group around the year",
        ),
        (
            lambda c: set_in(c, "contributions", "read_csv_params", ["sep"]),
            "read_csv_params must be a mapping",
        ),
        (
            lambda c: set_in(c, "contributions", "include_column_order", "yes"),
            "include_column_order must be true or false",
        ),
        (
            lambda c: set_in(c, "contributions", "column_details", ["FILERID"]),
            "column_details must be a list of mappings",
        ),
        (
            lambda c: c["contributions"]["column_details"].append({"raw_name": "X"}),
            "column {'raw_name': 'X'}: needs raw_name and type",
        ),
        (
            lambda c: set_in(
                c, "contributions", "state_code_columns", "reported_state"
            ),
            "state_code_columns must be a list of standard names",
        ),
        (
            lambda c: set_in(
                c, "contributions", "state_code_columns", ["reported_stat"]
            ),
            "state_code_columns: 'reported_stat': 'reported_stat' is not an attribute",
        ),
        (
            lambda c: set_in(c, "contributions", "new_empty_columns", ["descriptoin"]),
            "new_empty_columns: 'descriptoin': 'descriptoin' is not an attribute",
        ),
        (
            lambda c: set_in(c, "contributions", "duplicate_columns", ["amount"]),
            "duplicate_columns must map a standard name to a list of names",
        ),
        (
            lambda c: set_in(
                c, "contributions", "duplicate_columns", {"amount": "amount-2"}
            ),
            "duplicate_columns: 'amount' must map to a list of names",
        ),
        (
            lambda c: set_in(
                c, "contributions", "duplicate_columns", {"amount": ["amoutn"]}
            ),
            "duplicate_columns 'amount': 'amoutn': 'amoutn' is not an attribute",
        ),
        (
            lambda c: set_in(c, "contributions", "enum_mapper", ["x"]),
            "enum_mapper must map standard names to mappings of values",
        ),
        (
            lambda c: set_in(
                c, "contributions", "enum_mapper", {"donor--transactor_type": ["I"]}
            ),
            "enum_mapper: 'donor--transactor_type' must map raw values to standard values",
        ),
        (
            lambda c: set_in(c, "contributions", "null_values", ["amount"]),
            "null_values must map standard names to lists of values",
        ),
        (
            lambda c: set_in(c, "contributions", "null_values", {"amount": 0.0}),
            "null_values: 'amount' must map to a list of values",
        ),
        (
            lambda c: set_in(c, "contributions", "filter", ["NOTE"]),
            "filter must map raw column names",
        ),
        (
            lambda c: set_in(c, "contributions", "filter", {"NOTE": "void"}),
            "filter: 'NOTE' must be a list, or NOT: [values]",
        ),
        (
            lambda c: set_in(c, "contributions", "overloaded_columns", ["NAME"]),
            "overloaded_columns must map raw column names to pattern and filter",
        ),
        (
            lambda c: set_in(
                c,
                "contributions",
                "overloaded_columns",
                {"NAMEX": {"pattern": "(?P<NOTE>.*)"}},
            ),
            "overloaded_columns: 'NAMEX' is not a raw column",
        ),
        (
            lambda c: set_in(c, "contributions", "overloaded_columns", {"NAME": {}}),
            "overloaded_columns: 'NAME' needs a pattern",
        ),
        (
            lambda c: set_in(
                c,
                "contributions",
                "overloaded_columns",
                {"NAME": {"pattern": "(unclosed"}},
            ),
            "overloaded_columns: 'NAME' pattern does not compile",
        ),
        (
            lambda c: set_in(
                c,
                "contributions",
                "overloaded_columns",
                {"NAME": {"pattern": "(?P<NOTE>.*)", "filter": {"TYP": ["x"]}}},
            ),
            "overloaded_columns: 'NAME' filters on 'TYP'",
        ),
        (
            lambda c: set_in(c, "contributions", "path_pattern", 123),
            "path_pattern 123 is not a valid regex",
        ),
        (
            lambda c: c["contributions"].pop("state_code"),
            "state_code is required",
        ),
        (
            lambda c: set_in(
                c, "contributions", "column_details", 1, "standard_name", "donor--id"
            ),
            "a related record's id is named donor_id, not donor--id",
        ),
        (
            lambda c: set_in(
                c,
                "contributions",
                "column_details",
                1,
                "standard_name",
                "donor--address--city-2",
            ),
            "only an attribute of the form's own table can repeat",
        ),
    ],
)
def test_a_config_mistake_is_reported(tmp_path, break_it, message):
    config = minimal_config()
    break_it(config)
    with pytest.raises(
        utils.standardize.config.ConfigError, match=re.escape(message)
    ) as error:
        ConfigHandler("contributions", config_file_path=write_config(tmp_path, config))
    assert str(error.value).startswith("zz.yaml, form 'contributions':")


def test_every_problem_is_reported_at_once(tmp_path):
    config = minimal_config()
    config["contributions"]["colum_order"] = ["FILERID"]
    config["contributions"]["column_details"][3]["standard_name"] = "amoun"
    with pytest.raises(utils.standardize.config.ConfigError) as error:
        ConfigHandler("contributions", config_file_path=write_config(tmp_path, config))
    assert "colum_order" in str(error.value)
    assert "'amoun'" in str(error.value)


def test_config_error_is_a_value_error():
    assert issubclass(utils.standardize.config.ConfigError, ValueError)


def test_mistakes_in_a_parent_block_are_reported_for_the_child(tmp_path):
    config = {
        "base": {"meta": True, "state_code": "zz", "colum_order": []},
        **minimal_config(),
    }
    config["contributions"]["inherits"] = "base"
    with pytest.raises(utils.standardize.config.ConfigError, match="base: unknown key"):
        ConfigHandler("contributions", config_file_path=write_config(tmp_path, config))


def test_reported_by_and_transaction_direction_need_a_transaction_form(tmp_path):
    config = minimal_config()
    config["contributions"]["table_name"] = "Transactor"
    config["contributions"]["column_details"] = [
        {"raw_name": "ID", "type": "str", "standard_name": "id"},
        {"raw_name": "DIR", "type": "str", "standard_name": "transaction_direction"},
    ]
    config["contributions"]["enum_mapper"] = {
        "transaction_direction": {"out": "reverse"}
    }
    config["contributions"].pop("null_values")
    config["contributions"].pop("filter")
    with pytest.raises(utils.standardize.config.ConfigError) as error:
        ConfigHandler("contributions", config_file_path=write_config(tmp_path, config))
    assert "reported_by only applies to Transaction forms, not Transactor" in str(
        error.value
    )
    assert "transaction_direction only applies to Transaction forms" in str(error.value)


def test_transaction_direction_may_only_reverse(tmp_path):
    config = minimal_config()
    config["contributions"]["column_details"][5]["standard_name"] = (
        "transaction_direction"
    )
    config["contributions"]["enum_mapper"]["transaction_direction"] = {"out": "flip"}
    with pytest.raises(
        utils.standardize.config.ConfigError,
        match="maps to flip, not among its values in table.yaml: reverse",
    ):
        ConfigHandler("contributions", config_file_path=write_config(tmp_path, config))


@pytest.mark.parametrize(
    ("table", "name", "expected"),
    [
        ("Transaction", "amount", ("Transaction", "amount")),
        ("Transaction", "amount-2", ("Transaction", "amount")),
        ("Transaction", "donor--address--city", ("Address", "city")),
        (
            "Transaction",
            "donor--employer--organization--full_name",
            ("Transactor", "full_name"),
        ),
        ("Transaction", "donor--organization_name", ("Transactor", "full_name")),
        (
            "Transaction",
            "transaction_direction",
            ("Transaction", "transaction_direction"),
        ),
        (
            "Transactor",
            "candidate--member--election_result--election--office_sought",
            ("Election", "office_sought"),
        ),
        ("Transactor", "treasurer--member_id", ("Membership", "member_id")),
        ("Transactor", "organization_name", ("Transactor", "full_name")),
    ],
)
def test_resolve_standard_name(table, name, expected):
    assert utils.standardize.config.resolve_standard_name(table, name) == expected


def test_resolve_standard_name_rejects_an_unknown_table():
    with pytest.raises(
        utils.standardize.config.ConfigError, match="'Donor' is not a table"
    ):
        utils.standardize.config.resolve_standard_name("Donor", "amount")


@pytest.mark.parametrize(
    "break_it",
    [
        lambda c: set_in(c, "contributions", "table_name", "Transactions"),
        lambda c: c["contributions"].pop("table_name"),
        lambda c: c["contributions"].pop("path_pattern"),
        lambda c: c["contributions"].pop("state_code"),
    ],
)
def test_a_form_level_mistake_is_reported_once(tmp_path, break_it):
    """A bad or missing table_name doesn't repeat for every column"""
    config = minimal_config()
    break_it(config)
    with pytest.raises(utils.standardize.config.ConfigError) as error:
        ConfigHandler("contributions", config_file_path=write_config(tmp_path, config))
    assert str(error.value).count("\n  ") == 1, str(error.value)


def inheriting_config() -> dict:
    """minimal_config() split into a meta base and a form, the shape every state uses"""
    config = {"base": {"meta": True, "state_code": "zz"}, **minimal_config()}
    config["contributions"].pop("state_code")
    config["contributions"]["inherits"] = "base"
    return config


def test_the_inheriting_shape_is_valid(tmp_path):
    handler = ConfigHandler(
        "contributions", config_file_path=write_config(tmp_path, inheriting_config())
    )
    assert handler.state_code == "zz"


@pytest.mark.parametrize(
    ("key", "message"),
    [
        ("table_name", "table_name is required"),
        ("path_pattern", "path_pattern is required"),
    ],
)
def test_required_keys_are_required_under_a_meta_base(tmp_path, key, message):
    """A form that inherits a meta base is not itself meta"""
    config = inheriting_config()
    config["contributions"].pop(key)
    with pytest.raises(utils.standardize.config.ConfigError, match=message):
        ConfigHandler("contributions", config_file_path=write_config(tmp_path, config))


def test_state_code_is_required_under_a_meta_base(tmp_path):
    config = inheriting_config()
    config["base"].pop("state_code")
    with pytest.raises(
        utils.standardize.config.ConfigError, match="state_code is required"
    ):
        ConfigHandler("contributions", config_file_path=write_config(tmp_path, config))


def test_a_meta_block_needs_no_table_or_path(tmp_path):
    ConfigHandler("base", config_file_path=write_config(tmp_path, inheriting_config()))


def test_an_unknown_parent_is_reported_with_the_blocks_in_the_file(tmp_path):
    config = inheriting_config()
    config["contributions"]["inherits"] = "bsae"
    with pytest.raises(
        utils.standardize.config.ConfigError,
        match=re.escape(
            "zz.yaml, form 'contributions': contributions inherits 'bsae', which is not a block in the file (blocks: base, contributions)"
        ),
    ):
        ConfigHandler("contributions", config_file_path=write_config(tmp_path, config))


def test_an_inheritance_loop_is_reported(tmp_path):
    config = inheriting_config()
    config["base"]["inherits"] = "contributions"
    with pytest.raises(
        utils.standardize.config.ConfigError,
        match="inherits loops: contributions -> base -> contributions",
    ):
        ConfigHandler("contributions", config_file_path=write_config(tmp_path, config))


@pytest.mark.parametrize(
    ("table", "name", "message"),
    [
        (
            "Transaction",
            "donor--transaction_direction",
            "'transaction_direction' is not an attribute of Transactor",
        ),
        ("Transaction", "donor--id", "a related record's id is named donor_id"),
        (
            "Transaction",
            "donor--address--city-2",
            "only an attribute of the form's own table can repeat",
        ),
        ("Transaction", "amount--x", "'amount' is not a relation of Transaction"),
        (
            "Transactor",
            "organization_name--x",
            "'organization_name' is not a relation of Transactor",
        ),
    ],
)
def test_resolve_standard_name_rejects(table, name, message):
    with pytest.raises(utils.standardize.config.ConfigError, match=re.escape(message)):
        utils.standardize.config.resolve_standard_name(table, name)
