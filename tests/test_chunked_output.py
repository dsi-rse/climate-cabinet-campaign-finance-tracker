"""Re-runs of a step leave nothing from earlier runs, and CSV appends line up

Steps used to keep rows and tables from earlier runs, and CSV chunks could be
appended under the wrong headers.
"""

import shutil
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest
from utils.cli.core import main
from utils.ids import load_id_mapping, save_id_mapping
from utils.io import load_database, save_database, staged_directory

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
    first_mapping = load_id_mapping(output / "id_mapping.tsv")

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

    with (
        mock.patch.object(pd.DataFrame, "to_csv", side_effect=OSError("disk full")),
        pytest.raises(OSError, match="disk full"),
    ):
        save_id_mapping({("2", None, "TX", "Transactor"): "u-2"}, path)

    assert path.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == ["id_mapping.tsv"]
