import contextlib
import sys
import types

import pytest
import utils.cli.utils
from utils.cli.core import build_complete_parser
from utils.cli.utils import route_pipeline_step


class FakeExecutor:
    """Stand-in for submitit.AutoExecutor that records submitted jobs"""

    submitted = []

    def __init__(self, folder):
        pass

    def update_parameters(self, **kwargs):
        pass

    @contextlib.contextmanager
    def batch(self):
        yield

    def submit(self, func, args):
        FakeExecutor.submitted.append(args)


@pytest.fixture
def fake_submitit(monkeypatch):
    FakeExecutor.submitted = []
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
