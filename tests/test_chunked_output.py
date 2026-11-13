"""Chunked steps give the same output as unchunked ones, with nothing left over

Covers three problems with how steps save their output:
- re-runs kept rows and tables from earlier runs, and CSV chunks could be
  appended under the wrong headers
- an entity found in more than one chunk got one row per chunk
- CSV tables lost leading zeros, depending on which rows shared a chunk
"""

import shutil
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest
from utils.clean.columns import clean_table_columns
from utils.cli.core import main
from utils.constants import DEFAULT_SCHEMA_PATH
from utils.ids import (
    _column_id_parts,
    _id_part,
    load_id_mapping,
    save_id_mapping,
    source_id_uuid,
)
from utils.io import load_database, save_database, staged_directory
from utils.normalize import combine_duplicates_across_chunks
from utils.schema import DataSchema

RAW_FIXTURE_DIRECTORY = Path(__file__).parent / "data" / "standardize" / "raw"


@pytest.fixture
def data_directory(tmp_path):
    """Set up a data folder with the Texas fixtures, one donor in both files

    The first contributor in each contribution file has the same name and
    city, so normalize combines them into one Transactor. With small chunks,
    the two rows land in different chunks.

    Args:
        tmp_path: Temporary directory for this test (pytest fixture).

    Returns:
        The data folder, with raw/tx filled in.
    """
    state_directory = tmp_path / "raw" / "tx"
    shutil.copytree(RAW_FIXTURE_DIRECTORY / "tx", state_directory)
    path = state_directory / "contribs_100.csv"
    path.write_text(path.read_text().replace("Laterperson0", "Testperson0"))
    return tmp_path


def run(step: str, data_directory: Path, *options: str) -> None:
    """Run one cft step on Texas

    Args:
        step: Step name, such as "normalize".
        data_directory: The data folder.
        *options: More command line options.
    """
    assert (
        main(
            [step, "--states", "tx", "--data-directory", str(data_directory), *options]
        )
        == 0
    )


def read_tables(directory: Path, format: str = "parquet") -> dict[str, pd.DataFrame]:
    """Read every table in a step's output folder

    Args:
        directory: A state's output folder.
        format: "csv" or "parquet".

    Returns:
        Table name to table.
    """
    return next(load_database(directory, format=format))


def sorted_rows(table: pd.DataFrame) -> list[tuple]:
    """List a table's rows as text, sorted, so tables can be compared

    Args:
        table: Table to list.

    Returns:
        Each row as a tuple of text values (missing values as ""), sorted.
    """
    values = table.reindex(sorted(table.columns), axis=1).astype(object)
    values = values.where(values.notna(), "").astype(str)
    return sorted(values.itertuples(index=False, name=None))


@pytest.mark.parametrize("format", ["parquet", "csv"])
@pytest.mark.parametrize("chunk_size", ["1", "2", "4"])
def test_chunked_normalize_and_clean_match_unchunked(
    data_directory, format, chunk_size
):
    """Chunked runs give the same tables, with one row per id

    Each step is run once without chunks (into its default folder) and once
    with chunks (into another folder), on the same unchunked input.

    Args:
        data_directory: Data folder with the Texas fixtures.
        format: Input and output format for normalize and clean.
        chunk_size: Rows per chunk for normalize and clean.
    """
    formats = ["--input-format", format, "--output-format", format]
    run("standardize", data_directory, "--output-format", format)
    for step, output_name in (("normalize", "normalized"), ("clean", "cleaned")):
        run(step, data_directory, *formats)
        chunked_directory = data_directory / f"{output_name}_chunked"
        run(
            step,
            data_directory,
            *formats,
            "--chunk-size",
            chunk_size,
            "--output-directory",
            str(chunked_directory),
        )
        whole = read_tables(data_directory / output_name / "tx", format)
        chunked = read_tables(chunked_directory / "tx", format)
        assert set(chunked) == set(whole)
        for name, table in chunked.items():
            if "id" in table:
                assert table["id"].dropna().is_unique, f"{step} {name}"
            assert sorted_rows(table) == sorted_rows(whole[name]), f"{step} {name}"


def test_rerun_removes_tables_and_files_from_earlier_runs(data_directory):
    """Files a re-run doesn't write are gone, and the id mapping is kept

    Args:
        data_directory: Data folder with the Texas fixtures.
    """
    run("standardize", data_directory)
    run("normalize", data_directory)
    output = data_directory / "normalized" / "tx"
    stale = pd.DataFrame({"id": ["old"], "city": ["OLD RUN"]})
    stale.to_parquet(output / "Stale.parquet")
    # an entry for an input that is gone can only survive if the re-run
    # loads the previous mapping (every other uuid is remade from the rows)
    carried = {("gone", None, "tx", "Transactor"): "carried-over-uuid"}
    first_mapping = {**load_id_mapping(output / "id_mapping.tsv"), **carried}
    save_id_mapping(first_mapping, output / "id_mapping.tsv")

    run("normalize", data_directory, "--chunk-size", "2")

    assert not (output / "Stale.parquet").exists()
    assert load_id_mapping(output / "id_mapping.tsv") == first_mapping
    assert not list(output.parent.glob(".tx.*"))


def test_failed_step_leaves_the_previous_output(data_directory):
    """A step that fails keeps the last good output and leaves no staging folder

    Args:
        data_directory: Data folder with the Texas fixtures.
    """
    run("standardize", data_directory)
    run("normalize", data_directory)
    output = data_directory / "normalized" / "tx"
    before = {path.name: path.read_bytes() for path in output.iterdir()}

    with (
        mock.patch(
            "utils.cli.core.Normalizer.normalize_database",
            side_effect=RuntimeError("boom"),
        ),
        pytest.raises(RuntimeError, match="boom"),
    ):
        run("normalize", data_directory)

    after = {path.name: path.read_bytes() for path in output.iterdir()}
    # the failed run's manifest replaces the old one; everything else is kept
    before.pop("_run_manifest.json")
    after.pop("_run_manifest.json")
    assert after == before
    assert not list(output.parent.glob(".tx.*"))


def test_staged_directory_replaces_the_folder_only_on_success(tmp_path):
    """The staging folder is swapped in when the block ends and dropped if it raises

    Args:
        tmp_path: Temporary directory for this test (pytest fixture).
    """
    final = tmp_path / "tx"
    final.mkdir()
    (final / "old.csv").write_text("old")

    with pytest.raises(ValueError), staged_directory(final) as staging:
        (staging / "new.csv").write_text("new")
        raise ValueError
    assert [path.name for path in final.iterdir()] == ["old.csv"]

    with staged_directory(final) as staging:
        (staging / "new.csv").write_text("new")
    assert [path.name for path in final.iterdir()] == ["new.csv"]
    assert sorted(path.name for path in tmp_path.iterdir()) == ["tx"]


def test_csv_append_puts_values_under_their_headers(tmp_path):
    """A chunk with columns in another order is appended under the right headers

    Args:
        tmp_path: Temporary directory for this test (pytest fixture).
    """
    first = pd.DataFrame({"id": ["u1"], "full_name": ["Jane"], "email": ["j@x"]})
    second = pd.DataFrame({"full_name": ["Alex"], "id": ["u2"]})
    save_database({"Transactor": first}, tmp_path, format="csv")
    save_database({"Transactor": second}, tmp_path, format="csv", mode="append")

    table = read_tables(tmp_path, "csv")["Transactor"]
    assert table.astype(object).where(table.notna(), None).to_dict("records") == [
        {"id": "u1", "full_name": "Jane", "email": "j@x"},
        {"id": "u2", "full_name": "Alex", "email": None},
    ]


def test_csv_append_with_a_new_column_fails(tmp_path):
    """A chunk with a column the file doesn't have can't be appended

    Args:
        tmp_path: Temporary directory for this test (pytest fixture).
    """
    save_database({"T": pd.DataFrame({"id": ["u1"]})}, tmp_path, format="csv")
    with pytest.raises(ValueError, match="phone_number"):
        save_database(
            {"T": pd.DataFrame({"id": ["u2"], "phone_number": ["1"]})},
            tmp_path,
            format="csv",
            mode="append",
        )


def test_id_mapping_is_not_left_half_written(tmp_path):
    """If writing the id mapping fails, the previous file is unchanged

    Args:
        tmp_path: Temporary directory for this test (pytest fixture).
    """
    path = tmp_path / "id_mapping.tsv"
    save_id_mapping({("1", None, "TX", "Transactor"): "u-1"}, path)
    before = path.read_bytes()

    def write_part_then_fail(self: pd.DataFrame, path: Path, *args, **kwargs):
        Path(path).write_text("partial")
        raise OSError("disk full")

    with (
        mock.patch.object(
            pd.DataFrame, "to_csv", autospec=True, side_effect=write_part_then_fail
        ),
        pytest.raises(OSError, match="disk full"),
    ):
        save_id_mapping({("2", None, "TX", "Transactor"): "u-2"}, path)

    assert path.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == ["id_mapping.tsv"]


@pytest.mark.parametrize("chunk_size", [None, 1])
def test_csv_tables_keep_leading_zeros(tmp_path, chunk_size):
    """Zips and ids read from CSV keep their leading zeros at any chunk size

    Args:
        tmp_path: Temporary directory for this test (pytest fixture).
        chunk_size: Rows per chunk, or None for the whole table.
    """
    (tmp_path / "Address.csv").write_text(
        "id,zipcode,city\n0042,01234,NA\nabc,60601,\n"
    )
    chunks = list(load_database(tmp_path, format="csv", chunk_size=chunk_size))
    rows = pd.concat([chunk["Address"] for chunk in chunks])
    assert rows["id"].tolist() == ["0042", "abc"]
    assert rows["zipcode"].tolist() == ["01234", "60601"]
    # only empty fields are missing; "NA" is kept as written
    assert rows["city"].iloc[0] == "NA"
    assert pd.isna(rows["city"].iloc[1])


def test_clean_converts_whole_numbers_written_as_decimals():
    """An integer column read as text like "2020.0" still converts

    A float column with missing values is written to CSV as "2020.0".
    """
    schema = DataSchema(DEFAULT_SCHEMA_PATH).schema["Transaction"]
    table = pd.DataFrame(
        {
            "amount": ["1.50"],
            "donor_id": ["d"],
            "recipient_id": ["r"],
            "year": ["2020.0"],
            "day": [None],
        }
    )
    cleaned = clean_table_columns(table, schema)
    assert cleaned["year"].tolist() == [2020]
    assert pd.isna(cleaned["day"].iloc[0])


def test_ids_match_however_a_number_was_written():
    """A raw id read as 222, 222.0, "222" or "222.0" gets the same uuid"""
    assert {_id_part(value) for value in (222, 222.0, "222", "222.0")} == {"222"}
    assert _id_part("0042") == "0042"
    assert _id_part("1.5") == "1.5"
    # a column read from CSV as text takes the vectorised path
    for dtype in (object, str, "string"):
        column = pd.Series(["222.0", "0042", "1.5", None], dtype=dtype)
        assert _column_id_parts(column) == ["222", "0042", "1.5", None], dtype
    assert source_id_uuid(("222.0", "2020.0", "TX", "T")) == source_id_uuid(
        (222, 2020, "TX", "T")
    )


@pytest.mark.parametrize("format", ["parquet", "csv"])
def test_combine_duplicates_across_chunks_by_table_type(tmp_path, format):
    """Rows sharing an id are combined, split-off duplicates dropped, the rest kept

    Two identical transactions are two payments, so Transaction is left alone,
    and a table with nothing to combine is not rewritten.

    Args:
        tmp_path: Temporary directory for this test (pytest fixture).
        format: "csv" or "parquet".
    """
    tables = {
        "Transactor": pd.DataFrame(
            {"id": ["a", "a", "b"], "full_name": ["Ann", None, "Bob"]}
        ),
        "Address": pd.DataFrame(
            {"transactor_id": ["a", "a", "b"], "city": ["Austin", "Austin", "Waco"]}
        ),
        "Membership": pd.DataFrame(
            {"member_id": ["a", "a"], "organization_id": ["b", "b"]}
        ),
        "Transaction": pd.DataFrame(
            {"donor_id": ["a", "a"], "recipient_id": ["b", "b"], "amount": [5, 5]}
        ),
        "Election": pd.DataFrame({"id": ["e1", "e2"], "year": [2020, 2022]}),
    }
    save_database(tables, tmp_path, format=format)

    with mock.patch(
        "utils.normalize._core.save_database", wraps=save_database
    ) as saved:
        combine_duplicates_across_chunks(tmp_path, format, DEFAULT_SCHEMA_PATH)

    rewritten = {name for call in saved.call_args_list for name in call.args[0]}
    assert rewritten == {"Transactor", "Address", "Membership"}
    result = read_tables(tmp_path, format)
    assert sorted_rows(result["Transactor"]) == [("Ann", "a"), ("Bob", "b")]
    assert len(result["Address"]) == 2
    assert len(result["Membership"]) == 1
    assert len(result["Transaction"]) == 2
    assert len(result["Election"]) == 2


def test_staged_directory_recovers_the_previous_output_after_a_crash(tmp_path):
    """A death between the two renames leaves .previous, which the next run restores

    Args:
        tmp_path: Temporary directory for this test (pytest fixture).
    """
    final = tmp_path / "tx"
    final.mkdir()
    (final / "old.csv").write_text("old")
    rename = Path.rename

    def fail_second_rename(self: Path, target: Path):
        if self.name == ".tx.staging":
            raise PermissionError("swap failed")
        return rename(self, target)

    with (
        mock.patch.object(
            Path, "rename", autospec=True, side_effect=fail_second_rename
        ),
        pytest.raises(PermissionError),
        staged_directory(final) as staging,
    ):
        (staging / "new.csv").write_text("new")
    assert [path.name for path in final.iterdir()] == ["old.csv"]
    assert sorted(path.name for path in tmp_path.iterdir()) == ["tx"]

    # as left by a process killed between the renames
    final.rename(tmp_path / ".tx.previous")
    (tmp_path / ".tx.staging").mkdir()
    with staged_directory(final) as staging:
        assert [path.name for path in final.iterdir()] == ["old.csv"]
        (staging / "new.csv").write_text("new")
    assert [path.name for path in final.iterdir()] == ["new.csv"]
    assert sorted(path.name for path in tmp_path.iterdir()) == ["tx"]
