"""Header checks on raw files and the report of values standardize turns into nulls"""

import argparse
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest
import yaml
from utils.cli.core import run_standardize
from utils.io import load_database
from utils.standardize import standardize_state
from utils.standardize.config import ConfigHandler
from utils.standardize.source import (
    DataSourceStandardizationPipeline,
    DataStandardizer,
    HeaderMismatchError,
)

RAW_FIXTURE_DIRECTORY = Path(__file__).parent / "data" / "standardize" / "raw"
COLUMNS = ["ID", "NAME", "AMOUNT"]
ROWS = ["1,Ann,10", "2,Bo,20", "3,Cy,30"]


def write_form(tmp_path, read_csv_params, amount_is_text_number=False, **form):
    """Write a one-form config that reads COLUMNS by position, and return its path"""
    amount = {"raw_name": "AMOUNT", "type": "str", "standard_name": "amount"}
    if amount_is_text_number:
        amount["post_load_float"] = True
    config = {
        "form": {
            "state_code": "zz",
            "table_name": "Transaction",
            "path_pattern": r"^.*\.csv$",
            "read_csv_params": read_csv_params,
            "column_details": [
                {"raw_name": "ID", "type": "str", "standard_name": "donor_id"},
                {
                    "raw_name": "NAME",
                    "type": "str",
                    "standard_name": "donor--full_name",
                },
                amount,
            ],
            **form,
        }
    }
    config_path = tmp_path / "zz.yaml"
    config_path.write_text(yaml.safe_dump(config))
    return config_path


def load(tmp_path, config_path, *lines, encoding="utf-8"):
    """Write lines to a raw file and standardize it with the form"""
    raw_directory = tmp_path / "raw"
    raw_directory.mkdir(exist_ok=True)
    (raw_directory / "file.csv").write_text("\n".join(lines) + "\n", encoding=encoding)
    pipeline = DataSourceStandardizationPipeline("zz", "form", config_file=config_path)
    table = pipeline.load_and_standardize_data_source(
        state_data_directory=raw_directory
    )
    return pipeline, table


# ---- header checks ----


def test_matching_header_loads_every_row(tmp_path):
    config = write_form(tmp_path, {"header": 0})
    # case, spacing and a byte order mark don't count as differences
    _, table = load(tmp_path, config, "\ufeffid, Name ,AMOUNT", *ROWS)
    assert table["donor_id"].tolist() == ["1", "2", "3"]


def test_reordered_columns_raise_instead_of_shifting_values(tmp_path):
    config = write_form(tmp_path, {"header": 0})
    with pytest.raises(HeaderMismatchError) as error:
        load(tmp_path, config, "ID,AMOUNT,NAME", "1,10,Ann")
    message = str(error.value)
    assert "column 2 is 'AMOUNT' in the file and 'NAME' in the form" in message
    assert "column 3 is 'NAME' in the file and 'AMOUNT' in the form" in message


def test_added_column_raises(tmp_path):
    config = write_form(tmp_path, {"header": 0})
    with pytest.raises(
        HeaderMismatchError, match="has 4 columns and the form expects 3"
    ):
        load(tmp_path, config, "ID,NAME,AMOUNT,MEMO", "1,Ann,10,x")


def test_missing_header_row_raises_with_a_hint(tmp_path):
    config = write_form(tmp_path, {"header": 0})
    # with header: 0, the first data row would be dropped as a header
    with pytest.raises(HeaderMismatchError, match="may have no header row"):
        load(tmp_path, config, *ROWS)


def test_headerless_form_loads_the_first_row(tmp_path):
    config = write_form(tmp_path, {"header": None})
    _, table = load(tmp_path, config, *ROWS)
    assert table["donor_id"].tolist() == ["1", "2", "3"]


def test_headerless_form_raises_if_the_file_has_a_header(tmp_path):
    config = write_form(tmp_path, {"header": None})
    with pytest.raises(HeaderMismatchError, match="first row is a header"):
        load(tmp_path, config, ",".join(COLUMNS), *ROWS)


def test_headerless_form_raises_on_a_different_column_count(tmp_path):
    config = write_form(tmp_path, {"header": None})
    with pytest.raises(HeaderMismatchError, match="has 4 columns"):
        load(tmp_path, config, "1,Ann,10,extra", "2,Bo,20,extra")


def test_header_is_compared_after_decoding(tmp_path):
    # the AZ Access export is latin-1 with a UTF-8 byte order mark
    config = write_form(tmp_path, {"header": 0, "encoding": "latin-1"})
    _, table = load(
        tmp_path, config, "\ufeffID,NAME,AMOUNT", "1,Zoë,10", encoding="utf-8"
    )
    assert table["donor_id"].tolist() == ["1"]


def test_forms_read_by_header_name_are_not_checked(tmp_path):
    config = write_form(tmp_path, {"header": 0}, include_column_order=False)
    _, table = load(tmp_path, config, "AMOUNT,ID,NAME", "10,1,Ann")
    assert table[["donor_id", "donor--full_name", "amount"]].to_numpy().tolist() == [
        ["1", "Ann", "10"]
    ]


def test_every_fixture_header_matches_its_form():
    """Each fixture is copied from a real file, so a mismatch is a config error"""
    for config_path in sorted(
        (Path(__file__).parents[1] / "src/utils/standardize/finance/config").glob(
            "*.yaml"
        )
    ):
        config = yaml.safe_load(config_path.read_text())
        for form, details in config.items():
            if details.get("meta"):
                continue
            pipeline = DataSourceStandardizationPipeline(
                config_path.stem, form, config_file=config_path
            )
            state_directory = RAW_FIXTURE_DIRECTORY / config_path.stem
            if not state_directory.exists():
                continue
            for path in pipeline._raw_data_file_paths(state_directory):
                pipeline.data_reader._check_header(path)


# ---- coerced values ----


def standardizer(enum_mapper=None, date_formats=None, float_columns=()):
    """A DataStandardizer with only the given conversions configured"""
    config = MagicMock(spec=ConfigHandler)
    config.enum_mapper = enum_mapper or {}
    config.column_to_date_format = date_formats or {}
    config._null_values = {}
    config.post_load_float_columns = list(float_columns)
    config.absolute_value_columns = []
    return DataStandardizer(config)


def coerced(data_standardizer):
    """Coerced values as (column, step, raw value, count) tuples"""
    return set(
        data_standardizer.coerced_values[
            ["column", "step", "raw_value", "count"]
        ].itertuples(index=False, name=None)
    )


def test_unmapped_enum_values_are_reported_with_counts():
    data_standardizer = standardizer(
        enum_mapper={"party": {"DEM": "Democrat", "UNK": None}}
    )
    table = pd.DataFrame({"party": ["DEM", "REP", "REP", None, "UNK", "GRN"]})
    result = data_standardizer.standardize_data(table)
    assert result["party"].iloc[0] == "Democrat"
    assert result["party"].iloc[1:].isna().all()
    # nulls and values mapped to null on purpose are not reported
    assert coerced(data_standardizer) == {
        ("party", "enum", "REP", 2),
        ("party", "enum", "GRN", 1),
    }


def test_unmapped_transaction_directions_are_not_reported():
    # only reversed types are mapped; every other value becomes null by design
    data_standardizer = standardizer(
        enum_mapper={"transaction_direction": {"31": "reverse"}}
    )
    data_standardizer.standardize_data(
        pd.DataFrame({"transaction_direction": ["31", "2", "5"], "donor_id": [1, 2, 3]})
    )
    assert coerced(data_standardizer) == set()


def test_unparsed_dates_are_reported():
    data_standardizer = standardizer(date_formats={"date": "%m/%d/%Y"})
    data_standardizer.standardize_data(
        pd.DataFrame({"date": ["1/2/2024", "2024-01-02", None, "13/45/2024"]})
    )
    assert coerced(data_standardizer) == {
        ("date", "date", "2024-01-02", 1),
        ("date", "date", "13/45/2024", 1),
    }


def test_amounts_that_are_not_numbers_are_reported_but_blanks_are_not():
    data_standardizer = standardizer(float_columns=["amount"])
    data_standardizer.standardize_data(
        pd.DataFrame({"amount": ["$1,000.00", "N/A", "  ", None, "($5.00)", "N/A"]})
    )
    assert coerced(data_standardizer) == {("amount", "number", "N/A", 2)}


def test_coerced_values_cover_only_the_latest_call():
    data_standardizer = standardizer(float_columns=["amount"])
    data_standardizer.standardize_data(pd.DataFrame({"amount": ["N/A"]}))
    data_standardizer.standardize_data(pd.DataFrame({"amount": ["5"]}))
    assert coerced(data_standardizer) == set()


def test_rows_with_repeated_index_labels_are_matched_by_position():
    data_standardizer = standardizer(float_columns=["amount"])
    table = pd.DataFrame({"amount": ["1", "bad", "2"]}, index=[0, 0, 1])
    data_standardizer.standardize_data(table)
    assert coerced(data_standardizer) == {("amount", "number", "bad", 1)}


def test_pipeline_labels_coerced_values_by_file_and_logs_them(tmp_path, caplog):
    config = write_form(tmp_path, {"header": 0}, amount_is_text_number=True)
    with caplog.at_level(logging.WARNING, logger="utils.standardize.source"):
        pipeline, table = load(
            tmp_path, config, "ID,NAME,AMOUNT", "1,Ann,ten", "2,Bo,5"
        )

    assert len(table) == 2
    assert pipeline.coerced_values.to_dict("records") == [
        {
            "state": "zz",
            "form": "form",
            "file": "file.csv",
            "column": "amount",
            "step": "number",
            "raw_value": "ten",
            "count": 1,
        }
    ]
    assert "zz form file.csv: 1 values could not be read and became null" in caplog.text


# ---- standardize_state and the cft standardize command ----


def test_standardize_state_returns_coerced_values_for_every_form():
    database, coerced_values = standardize_state(
        "az", input_directory=RAW_FIXTURE_DIRECTORY
    )
    assert "Transaction" in database
    # the AZ Advanced Search fixture has a party and a district with no mapping
    assert set(zip(coerced_values["form"], coerced_values["raw_value"])) >= {
        ("advanced_search_committees", "Republican"),
        ("advanced_search_committees", "Governor"),
    }


def test_cft_standardize_writes_the_report_where_normalize_will_not_load_it(tmp_path):
    args = argparse.Namespace(
        state="az",
        start_year=None,
        end_year=None,
        input_directory=RAW_FIXTURE_DIRECTORY,
        output_directory=tmp_path,
        output_format="csv",
    )
    run_standardize(args)

    report = pd.read_csv(tmp_path / "az" / "reports" / "coerced_values.csv")
    assert list(report.columns) == [
        "state",
        "form",
        "file",
        "column",
        "step",
        "raw_value",
        "count",
    ]
    assert len(report) > 0
    # normalize loads every table file in the state folder, but not subfolders
    (loaded,) = load_database(tmp_path / "az", format="csv")
    assert "coerced_values" not in loaded
