"""Each pipeline step writes a run manifest describing what it read and wrote"""

import argparse
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

import pytest
from utils.cli.core import main
from utils.provenance import (
    RUN_MANIFEST_NAME,
    describe_outputs,
    git_state,
    run_manifest_path,
    run_step_with_manifest,
)

RAW_FIXTURE_DIRECTORY = Path(__file__).parent / "data" / "standardize" / "raw"


def read_manifest(path: Path) -> dict:
    """Read a run manifest

    Args:
        path: The manifest file.

    Returns:
        The manifest's contents.
    """
    return json.loads(path.read_text())


def hashes_by_path(files: list[dict]) -> dict[str, str]:
    """Map each file in a manifest's inputs or outputs to its sha256

    Args:
        files: A manifest's "inputs" or "outputs" list.

    Returns:
        {path: sha256}
    """
    return {file["path"]: file["sha256"] for file in files}


@pytest.fixture
def step_args(tmp_path):
    """Arguments for a fake per-state step with one input file

    Args:
        tmp_path: Temporary directory for this test (pytest fixture).

    Returns:
        Parsed-argument namespace with input and output directories, a state
        and no step function yet.
    """
    input_directory = tmp_path / "in"
    (input_directory / "tx").mkdir(parents=True)
    (input_directory / "tx" / "Transaction.csv").write_text("amount\n1.00\n")
    return argparse.Namespace(
        command="fake",
        state="tx",
        input_directory=input_directory,
        output_directory=tmp_path / "out",
        chunk_size=None,
        func=print,
    )


def write_output(args: argparse.Namespace) -> int:
    """Fake step that writes one output file

    Args:
        args: Parsed arguments with an output directory and state.

    Returns:
        0, like the real step functions.
    """
    output_directory = args.output_directory / args.state
    output_directory.mkdir(parents=True, exist_ok=True)
    (output_directory / "Transaction.csv").write_text("amount\n1.00\n")
    return 0


def write_output_with_an_old_mtime(args: argparse.Namespace) -> int:
    """Fake step that writes a file and sets its modification time back

    py7zr does this when it extracts: each file gets the time stored in the
    archive, not the time it was written.

    Args:
        args: Parsed arguments with an output directory and state.

    Returns:
        0, like the real step functions.
    """
    write_output(args)
    extracted = args.output_directory / args.state / "extracted.csv"
    extracted.write_text("a\n")
    os.utime(extracted, (1_000_000_000, 1_000_000_000))
    return 0


def fail(args: argparse.Namespace) -> int:
    """Fake step that fails

    Args:
        args: Parsed arguments (unused).

    Raises:
        ValueError: always.
    """
    raise ValueError("bad input")


def test_successful_step_records_inputs_outputs_and_arguments(step_args):
    """The manifest lists hashed inputs and outputs and the arguments

    Args:
        step_args: Arguments for a fake per-state step.
    """
    step_args.pipeline_step_func = write_output
    assert run_step_with_manifest(step_args) == 0

    manifest = read_manifest(step_args.output_directory / "tx" / RUN_MANIFEST_NAME)
    expected_hash = hashlib.sha256(b"amount\n1.00\n").hexdigest()
    assert manifest["status"] == "succeeded"
    assert manifest["step"] == "fake"
    assert manifest["state"] == "tx"
    assert manifest["inputs"] == [
        {"path": "Transaction.csv", "size": 12, "sha256": expected_hash}
    ]
    # the manifest doesn't list itself
    assert hashes_by_path(manifest["outputs"]) == {"Transaction.csv": expected_hash}
    assert manifest["arguments"]["input_directory"] == str(step_args.input_directory)
    assert manifest["arguments"]["chunk_size"] is None
    assert "func" not in manifest["arguments"]
    assert "pipeline_step_func" not in manifest["arguments"]
    assert manifest["packages"]["pandas"] is not None
    assert manifest["finished_at"] >= manifest["started_at"]


def test_failed_step_still_writes_a_manifest(step_args):
    """A failing step's manifest records the error, and the error is raised

    Args:
        step_args: Arguments for a fake per-state step.
    """
    step_args.pipeline_step_func = fail
    with pytest.raises(ValueError, match="bad input"):
        run_step_with_manifest(step_args)

    manifest = read_manifest(step_args.output_directory / "tx" / RUN_MANIFEST_NAME)
    assert manifest["status"] == "failed"
    assert manifest["error"] == "ValueError: bad input"


def test_git_state_outside_a_repository_is_unknown(tmp_path):
    """Code not run from a git checkout records no commit

    Args:
        tmp_path: Temporary directory for this test (pytest fixture).
    """
    assert git_state(tmp_path) == {"commit": None, "dirty": None}


def test_git_state_in_this_repository_has_a_commit():
    """Code run from this checkout records its commit"""
    state = git_state()
    assert len(state["commit"]) == 40
    assert isinstance(state["dirty"], bool)


def test_cli_steps_chain_through_their_manifests(tmp_path):
    """Normalize's input hashes match the files standardize wrote

    Runs `cft standardize` and `cft normalize` on the Texas fixtures. The
    manifest in the standardized folder is not read as a table by normalize.

    Args:
        tmp_path: Temporary directory for this test (pytest fixture).
    """
    shutil.copytree(RAW_FIXTURE_DIRECTORY / "tx", tmp_path / "raw" / "tx")
    common = ["--states", "tx", "--data-directory", str(tmp_path)]
    assert main(["standardize", *common]) == 0
    assert main(["normalize", *common]) == 0

    standardized = read_manifest(tmp_path / "standardized" / "tx" / RUN_MANIFEST_NAME)
    normalized = read_manifest(tmp_path / "normalized" / "tx" / RUN_MANIFEST_NAME)
    assert standardized["status"] == normalized["status"] == "succeeded"
    assert {file["path"] for file in standardized["inputs"]} == {
        path.name for path in (RAW_FIXTURE_DIRECTORY / "tx").iterdir()
    }
    assert "reports/coerced_values.csv" in hashes_by_path(standardized["outputs"])
    assert hashes_by_path(normalized["inputs"]) == hashes_by_path(
        standardized["outputs"]
    )
    assert "Transaction.parquet" in hashes_by_path(normalized["outputs"])


def test_manifest_says_running_while_the_step_runs(step_args):
    """The manifest is written before the step, so a killed run shows "running"

    Args:
        step_args: Arguments for a fake per-state step.
    """
    seen = {}

    def look_at_manifest(args):
        seen.update(read_manifest(run_manifest_path(args)))
        return 0

    step_args.pipeline_step_func = look_at_manifest
    run_step_with_manifest(step_args)
    assert seen["status"] == "running"
    assert seen["finished_at"] is None
    assert hashes_by_path(seen["inputs"]) == {
        "Transaction.csv": hashlib.sha256(b"amount\n1.00\n").hexdigest()
    }


def test_command_line_comes_from_the_arguments(step_args, monkeypatch):
    """On SLURM sys.argv is the job runner's, so the saved command line is used

    Args:
        step_args: Arguments for a fake per-state step.
        monkeypatch: pytest fixture to replace sys.argv.
    """
    monkeypatch.setattr("sys.argv", ["python", "-m", "submitit.core._submit"])
    step_args.command_line = ["cft", "normalize", "--states", "tx"]
    step_args.pipeline_step_func = write_output
    run_step_with_manifest(step_args)

    manifest = read_manifest(step_args.output_directory / "tx" / RUN_MANIFEST_NAME)
    assert manifest["command_line"] == ["cft", "normalize", "--states", "tx"]
    assert "command_line" not in manifest["arguments"]


def test_bookkeeping_errors_do_not_stop_or_hide_the_step(step_args, monkeypatch):
    """A file that can't be hashed is recorded, and the step's own error wins

    Args:
        step_args: Arguments for a fake per-state step.
        monkeypatch: pytest fixture to break file hashing.
    """

    def broken_hash(path):
        raise PermissionError(f"can't read {path}")

    monkeypatch.setattr("utils.provenance.sha256_file", broken_hash)
    step_args.pipeline_step_func = fail
    with pytest.raises(ValueError, match="bad input"):
        run_step_with_manifest(step_args)

    manifest = read_manifest(step_args.output_directory / "tx" / RUN_MANIFEST_NAME)
    assert manifest["status"] == "failed"
    assert any("PermissionError" in error for error in manifest["manifest_errors"])

    step_args.pipeline_step_func = write_output
    assert run_step_with_manifest(step_args) == 0


def test_manifest_leaves_out_archives_the_download_log_and_partial_downloads(
    step_args,
):
    """Archives, the download log and partial downloads are not listed

    Args:
        step_args: Arguments for a fake per-state step.
    """
    state_input = step_args.input_directory / "tx"
    (state_input / "_archives").mkdir()
    (state_input / "_archives" / "TEC_CF_CSV.20261110T060000Z.zip").write_bytes(b"z")
    (state_input / "_downloads.jsonl").write_text("{}\n")
    (state_input / "big.zip.part").write_bytes(b"p")

    step_args.pipeline_step_func = write_output
    run_step_with_manifest(step_args)

    manifest = read_manifest(step_args.output_directory / "tx" / RUN_MANIFEST_NAME)
    assert [file["path"] for file in manifest["inputs"]] == ["Transaction.csv"]
    written = {file["path"]: file["written_by_run"] for file in manifest["outputs"]}
    assert written == {"Transaction.csv": True}


def test_outputs_from_before_the_run_are_not_written_by_it(step_args):
    """A file left from an earlier run is marked as not written by this one

    A file's times can't be set back for a test (the system sets its
    status-change time), so the run is placed after the file instead.

    Args:
        step_args: Arguments for a fake per-state step.
    """
    earlier = step_args.output_directory / "tx" / "Transaction.parquet"
    earlier.parent.mkdir(parents=True)
    earlier.write_bytes(b"old")

    outputs = describe_outputs(step_args, started_at=time.time() + 10)
    assert [(file["path"], file["written_by_run"]) for file in outputs] == [
        ("Transaction.parquet", False)
    ]


def test_outputs_with_a_restored_old_mtime_are_written_by_the_run(step_args):
    """A file the step wrote counts as written even if its mtime was set back

    Michigan's collector extracts 7z archives with py7zr, which gives each
    file the time stored in the archive, so the modification time alone
    would mark every file it extracts as left from an earlier run.

    Args:
        step_args: Arguments for a fake per-state step.
    """
    step_args.pipeline_step_func = write_output_with_an_old_mtime
    run_step_with_manifest(step_args)

    manifest = read_manifest(step_args.output_directory / "tx" / RUN_MANIFEST_NAME)
    written = {file["path"]: file["written_by_run"] for file in manifest["outputs"]}
    assert written == {"Transaction.csv": True, "extracted.csv": True}


def test_normalize_inputs_include_the_id_mapping(step_args):
    """The id mapping normalize reads from its output directory is an input

    Args:
        step_args: Arguments for a fake per-state step.
    """
    mapping = step_args.output_directory / "tx" / "id_mapping.tsv"
    mapping.parent.mkdir(parents=True)
    mapping.write_text("raw_id\tyear\treported_state\ttable_name\tuuid\n")
    step_args.pipeline_step_func = write_output
    run_step_with_manifest(step_args)

    manifest = read_manifest(step_args.output_directory / "tx" / RUN_MANIFEST_NAME)
    input_paths = [file["path"] for file in manifest["inputs"]]
    assert input_paths == ["Transaction.csv", mapping.as_posix()]


@pytest.mark.parametrize("train", [True, False])
def test_link_lists_the_model_and_the_database_by_size(tmp_path, train):
    """Link's model is an output when training and an input otherwise, and the
    database is listed by size with no hash

    Args:
        tmp_path: Temporary directory for this test (pytest fixture).
        train: Whether the link run trains the model.
    """
    (tmp_path / "cleaned" / "tx").mkdir(parents=True)
    (tmp_path / "cleaned" / "tx" / "Transactor.parquet").write_bytes(b"t")
    model_path = tmp_path / "model.json"
    database_path = tmp_path / "linked.duckdb"

    def link(args):
        if args.train:
            model_path.write_text("{}")
        database_path.write_bytes(b"d" * 10)
        return 0

    if not train:
        model_path.write_text("{}")
    args = argparse.Namespace(
        command="link",
        input_directory=tmp_path / "cleaned",
        output_directory=tmp_path / "linked",
        model_path=model_path,
        database_path=database_path,
        train=train,
        pipeline_step_func=link,
    )
    run_step_with_manifest(args)

    manifest = read_manifest(tmp_path / "linked" / RUN_MANIFEST_NAME)
    model_hash = hashlib.sha256(b"{}").hexdigest()
    inputs, outputs = hashes_by_path(manifest["inputs"]), manifest["outputs"]
    assert inputs.get(model_path.as_posix()) == (None if train else model_hash)
    assert hashes_by_path(outputs).get(model_path.as_posix()) == (
        model_hash if train else None
    )
    (database,) = (file for file in outputs if file["path"] == database_path.as_posix())
    assert database["size"] == 10
    assert database["sha256"] is None
