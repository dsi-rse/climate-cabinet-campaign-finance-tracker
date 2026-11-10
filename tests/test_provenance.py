"""Each pipeline step writes a run manifest describing what it read and wrote"""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import pytest
from utils.cli.core import main
from utils.provenance import (
    RUN_MANIFEST_NAME,
    git_state,
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
