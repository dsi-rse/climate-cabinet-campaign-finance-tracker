import contextlib
import sys
import types

import pytest
import utils.cli.utils
from utils.cli.core import build_complete_parser
from utils.cli.utils import route_pipeline_step
from utils.provenance import run_step_with_manifest


class FakeExecutor:
    """Stand-in for submitit.AutoExecutor that records submitted jobs"""

    submitted = []
    functions = []

    def __init__(self, folder):
        pass

    def update_parameters(self, **kwargs):
        pass

    @contextlib.contextmanager
    def batch(self):
        yield

    def submit(self, func, args):
        FakeExecutor.functions.append(func)
        FakeExecutor.submitted.append(args)


@pytest.fixture
def fake_submitit(monkeypatch):
    FakeExecutor.submitted = []
    FakeExecutor.functions = []
    monkeypatch.setitem(
        sys.modules, "submitit", types.SimpleNamespace(AutoExecutor=FakeExecutor)
    )
    return FakeExecutor


def parse(argv, calls):
    args = build_complete_parser().parse_args(argv)
    args.pipeline_step_func = calls.append
    return args


@pytest.mark.parametrize("slurm", [False, True])
def test_each_state_gets_its_own_args(tmp_path, fake_submitit, slurm):
    calls = []
    argv = ["clean", "--data-directory", str(tmp_path), "--states", "pa", "az", "mi"]
    (tmp_path / "normalized").mkdir()
    args = parse(argv + (["--slurm"] if slurm else []), calls)
    route_pipeline_step(args)
    jobs = fake_submitit.submitted if slurm else calls
    assert [job.state for job in jobs] == ["pa", "az", "mi"]


@pytest.mark.parametrize("slurm", [False, True])
def test_link_runs_once_for_all_states(tmp_path, fake_submitit, slurm):
    calls = []
    argv = ["link", "--data-directory", str(tmp_path), "--states", "pa", "az", "mi"]
    (tmp_path / "cleaned").mkdir()
    args = parse(argv + (["--slurm"] if slurm else []), calls)
    route_pipeline_step(args)
    jobs = fake_submitit.submitted if slurm else calls
    assert len(jobs) == 1


def test_collect_without_states_uses_all_collectors(tmp_path, monkeypatch):
    monkeypatch.setattr(
        utils.cli.utils, "get_state_collectors", lambda: {"pa": None, "az": None}
    )
    calls = []
    args = parse(["collect", "--data-directory", str(tmp_path)], calls)
    route_pipeline_step(args)
    assert [job.state for job in calls] == ["pa", "az"]


def test_slurm_jobs_run_through_the_run_manifest(tmp_path, fake_submitit, monkeypatch):
    """Each SLURM job runs its step through run_step_with_manifest, and carries
    the cft command line, since sys.argv inside a job is the job runner's
    """
    argv = [
        "cft",
        "clean",
        "--data-directory",
        str(tmp_path),
        "--states",
        "pa",
        "--slurm",
    ]
    monkeypatch.setattr(sys, "argv", argv)
    (tmp_path / "normalized").mkdir()
    route_pipeline_step(parse(argv[1:], []))
    assert fake_submitit.functions == [run_step_with_manifest]
    assert fake_submitit.submitted[0].command_line == argv


def test_states_come_from_state_folders_only(tmp_path):
    """Files such as link's _run_manifest.json and folders starting with _ are
    not states
    """
    linked = tmp_path / "linked"
    for name in ("pa", "tx", "_archives", ".cache"):
        (linked / name).mkdir(parents=True)
    (linked / "_run_manifest.json").write_text("{}")
    calls = []
    route_pipeline_step(parse(["classify", "--data-directory", str(tmp_path)], calls))
    assert sorted(job.state for job in calls) == ["pa", "tx"]
