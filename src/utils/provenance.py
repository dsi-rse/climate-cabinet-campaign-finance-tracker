"""Record what each pipeline step ran on, with what code, and what it wrote

Each `cft` step writes a `_run_manifest.json` to its output directory (per
state for steps that run per state). It lists the git commit, the arguments,
the Python and package versions, and the size and sha256 of every input and
output file. A later step's input hashes can be matched to an earlier step's
output hashes to show which run produced the data it read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import traceback
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

RUN_MANIFEST_NAME = "_run_manifest.json"
# Packages whose versions can change the pipeline's output
TRACKED_PACKAGES = (
    "climate-cabinet-campaign-finance",
    "pandas",
    "numpy",
    "pyarrow",
    "duckdb",
    "splink",
    "requests",
    "usaddress",
    "nameparser",
)
# Arguments that hold functions rather than settings
_UNRECORDED_ARGUMENTS = ("func", "pipeline_step_func")
_HASH_CHUNK_BYTES = 1024 * 1024


def utc_now() -> str:
    """Get the current time for a manifest

    Returns:
        The current UTC time in ISO 8601 format, to the second.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    """Hash a file without reading it all into memory

    Args:
        path: File to hash.

    Returns:
        The file's sha256 as a hex string.
    """
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        while chunk := file.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def describe_file(path: Path, relative_to: Path | None = None) -> dict:
    """Describe a file by its path, size and sha256

    Args:
        path: File to describe.
        relative_to: Directory to write the path relative to. If None, or if
            the file is not inside it, the full path is written.

    Returns:
        {"path": ..., "size": ..., "sha256": ...}
    """
    path = Path(path)
    label = path.as_posix()
    if relative_to is not None:
        try:
            label = path.relative_to(relative_to).as_posix()
        except ValueError:
            pass
    return {"path": label, "size": path.stat().st_size, "sha256": sha256_file(path)}


def describe_directory(
    directory: Path | None, exclude: Iterable[str] = (RUN_MANIFEST_NAME,)
) -> list[dict]:
    """Describe every file in a directory and its subdirectories

    Args:
        directory: Directory to describe. A missing directory has no files.
        exclude: File names to leave out, such as the manifest itself.

    Returns:
        One describe_file entry per file, with paths relative to directory,
        sorted by path.
    """
    if directory is None or not Path(directory).is_dir():
        return []
    directory = Path(directory)
    excluded = set(exclude)
    files = sorted(
        path
        for path in directory.rglob("*")
        if path.is_file() and path.name not in excluded
    )
    return [describe_file(path, relative_to=directory) for path in files]


def git_state(directory: Path | None = None) -> dict:
    """Find the git commit of the code that is running

    Args:
        directory: Directory inside the repository. Defaults to this module's
            directory.

    Returns:
        {"commit": sha or None, "dirty": bool or None}. Both are None when the
        code is not running from a git checkout, such as an installed package.
        "dirty" is True when tracked files have uncommitted changes, so the
        commit alone doesn't describe the code.
    """
    directory = Path(directory or Path(__file__).parent)
    git = shutil.which("git")
    if git is None:
        return {"commit": None, "dirty": None}

    def run_git(*arguments: str) -> str:
        # fixed git commands with no user input
        return subprocess.run(
            [git, *arguments],  # noqa: S603
            cwd=directory,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    try:
        commit = run_git("rev-parse", "HEAD")
        changes = run_git("status", "--porcelain", "--untracked-files=no")
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "dirty": None}
    return {"commit": commit, "dirty": bool(changes)}


def package_versions(packages: Iterable[str] = TRACKED_PACKAGES) -> dict:
    """Look up installed package versions

    Args:
        packages: Distribution names to look up.

    Returns:
        Mapping of each package to its version, or None if not installed.
    """
    versions = {}
    for package in packages:
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = None
    return versions


def recordable_arguments(args: argparse.Namespace) -> dict:
    """Turn parsed arguments into values that can be written as JSON

    Args:
        args: Parsed command line arguments for one step.

    Returns:
        Each argument except the step functions, with paths and other
        objects written as text.
    """
    recorded = {
        name: value
        for name, value in sorted(vars(args).items())
        if name not in _UNRECORDED_ARGUMENTS
    }
    return json.loads(json.dumps(recorded, default=str))


def _state_directory(directory: Path | None, args: argparse.Namespace) -> Path | None:
    """Find a step's directory for the state it is running on

    Args:
        directory: The step's input or output directory.
        args: Parsed arguments. Steps that run per state have a `state`.

    Returns:
        directory/<state> for per-state steps, directory for steps that run
        once, or None if directory is None.
    """
    if directory is None:
        return None
    state = getattr(args, "state", None)
    return Path(directory) / state if state else Path(directory)


def run_manifest_path(args: argparse.Namespace) -> Path | None:
    """Find where a step's manifest goes

    Args:
        args: Parsed arguments for one step.

    Returns:
        The manifest path in the step's output directory, or None if the step
        has no output directory.
    """
    output_directory = _state_directory(getattr(args, "output_directory", None), args)
    if output_directory is None:
        return None
    return output_directory / RUN_MANIFEST_NAME


def _describe_optional_file(path: Path | None) -> list[dict]:
    """Describe a file if it is given and exists

    Args:
        path: File to describe, or None.

    Returns:
        A list with the file's description, or an empty list.
    """
    if path is None or not Path(path).is_file():
        return []
    return [describe_file(Path(path))]


def describe_inputs(args: argparse.Namespace) -> list[dict]:
    """Describe the files a step will read, before it runs

    Args:
        args: Parsed arguments for one step.

    Returns:
        The files in the step's input directory (per state if the step runs
        per state), and for link, the model it loads when not training.
    """
    inputs = describe_directory(
        _state_directory(getattr(args, "input_directory", None), args)
    )
    if not getattr(args, "train", False):
        inputs += _describe_optional_file(getattr(args, "model_path", None))
    return inputs


def describe_outputs(args: argparse.Namespace) -> list[dict]:
    """Describe the files a step wrote, after it runs

    The DuckDB database is listed by path and size only: it can be very large,
    and its bytes change whenever it is opened, so a hash would not identify
    its contents.

    Args:
        args: Parsed arguments for one step.

    Returns:
        The files in the step's output directory (per state if the step runs
        per state), and for link, the model it trained and the database.
    """
    outputs = describe_directory(
        _state_directory(getattr(args, "output_directory", None), args)
    )
    if getattr(args, "train", False):
        outputs += _describe_optional_file(getattr(args, "model_path", None))
    database_path = getattr(args, "database_path", None)
    if database_path is not None and Path(database_path).is_file():
        outputs.append(
            {
                "path": Path(database_path).as_posix(),
                "size": Path(database_path).stat().st_size,
                "sha256": None,
            }
        )
    return outputs


def write_run_manifest(path: Path, manifest: dict) -> None:
    """Write a manifest as indented JSON

    Args:
        path: File to write. Its directory is created if needed.
        manifest: Contents to write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2) + "\n")


def run_step_with_manifest(args: argparse.Namespace) -> int | None:
    """Run one pipeline step and write its run manifest

    The manifest is written even if the step fails, with "status": "failed"
    and the error, and then the error is raised again.

    Args:
        args: Parsed arguments for one step, with `pipeline_step_func` set to
            the function that runs it.

    Returns:
        Whatever the step function returns.
    """
    step_function: Callable[[argparse.Namespace], int | None] = args.pipeline_step_func
    manifest = {
        "step": getattr(args, "command", step_function.__name__),
        "state": getattr(args, "state", None),
        "status": "running",
        "started_at": utc_now(),
        "finished_at": None,
        "git": git_state(),
        "command_line": sys.argv,
        "arguments": recordable_arguments(args),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": package_versions(),
        "inputs": describe_inputs(args),
        "outputs": [],
        "error": None,
    }
    manifest_path = run_manifest_path(args)
    try:
        result = step_function(args)
    except BaseException as error:
        manifest["status"] = "failed"
        manifest["error"] = "".join(
            traceback.format_exception_only(type(error), error)
        ).strip()
        raise
    else:
        manifest["status"] = "succeeded"
        return result
    finally:
        manifest["finished_at"] = utc_now()
        manifest["outputs"] = describe_outputs(args)
        if manifest_path is not None:
            write_run_manifest(manifest_path, manifest)
