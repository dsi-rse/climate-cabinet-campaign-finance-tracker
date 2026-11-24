"""Special (Python) pipelines are found by state code and replace config-only ones"""

import importlib
import sys
from pathlib import Path

import pytest
import yaml
from utils.standardize import source_standardization_registry as registry
from utils.standardize.source import DataSourceStandardizationPipeline
from utils.standardize.source_standardization_registry import (
    register_special_data_source,
)

FORM = {
    "table_name": "Transaction",
    "path_pattern": ".*",
    "state_code_columns": ["reported_state"],
    "column_details": [
        {"raw_name": "ID", "type": "str", "standard_name": "donor_id"},
        {"raw_name": "AMT", "type": "Float64", "standard_name": "amount"},
    ],
}


@pytest.fixture
def state_zz(tmp_path, monkeypatch):
    """A state zz with a config of two forms and a special module for one

    The special package is a temporary directory on sys.path; the config
    directory is temporary too, so the real states are left alone.

    Returns:
        The special package's directory.
    """
    configs = tmp_path / "config"
    configs.mkdir()
    with (configs / "zz.yaml").open("w") as f:
        yaml.safe_dump({"special": FORM, "plain": FORM}, f)
    package = tmp_path / "special_states"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "zz.py").write_text(
        "from utils.standardize.source import DataSourceStandardizationPipeline\n"
        "from utils.standardize.source_standardization_registry import"
        " register_special_data_source\n"
        f"\n\n@register_special_data_source('zz', form_code='special',"
        f" init_kwargs={{'config_file': {str(configs / 'zz.yaml')!r}}})\n"
        "class ZZSpecial(DataSourceStandardizationPipeline):\n"
        "    pass\n"
    )
    # not a state module: importing it would fail the test
    (package / "_shared.py").write_text("raise AssertionError('imported _shared')\n")
    # each test writes its own package, so an import from an earlier test
    # must not be reused
    for name in [n for n in sys.modules if n.split(".")[0] == "special_states"]:
        monkeypatch.delitem(sys.modules, name)
    importlib.invalidate_caches()
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(registry, "SPECIAL_PIPELINE_PACKAGE", "special_states")
    monkeypatch.setattr(registry, "STATE_FINANCE_CONFIG_DIRECTORY", configs)
    monkeypatch.setattr(registry, "_FINANCE_SOURCE_REGISTRY", {})
    return package


def pipelines_by_form(state: str) -> dict[str, str]:
    return {
        pipeline.form_code: type(pipeline).__name__
        for pipeline in registry.get_registered_sources()[state]
    }


def test_special_pipeline_replaces_the_config_only_one(state_zz):
    registry.register_all_data_source_pipelines("ZZ")
    assert pipelines_by_form("zz") == {
        "special": "ZZSpecial",
        "plain": "DataSourceStandardizationPipeline",
    }


@pytest.mark.parametrize("states", [["zz"], ["zz", "yy"], None])
def test_states_may_be_a_list_or_none(state_zz, states):
    registry.register_all_data_source_pipelines(states)
    assert set(registry.get_registered_sources()) == {"zz"}
    assert pipelines_by_form("zz")["special"] == "ZZSpecial"


def test_other_states_are_not_registered(state_zz):
    registry.register_all_data_source_pipelines(["yy"])
    assert registry.get_registered_sources() == {}


def test_decorator_takes_form_code_from_the_class(state_zz, monkeypatch):
    config_file = Path(registry.STATE_FINANCE_CONFIG_DIRECTORY) / "zz.yaml"

    @register_special_data_source("zz", init_kwargs={"config_file": config_file})
    class FromAttribute(DataSourceStandardizationPipeline):
        form_code = "plain"

    assert pipelines_by_form("zz") == {"plain": "FromAttribute"}


def test_decorator_needs_a_form_code():
    with pytest.raises(ValueError, match="form_code must be given"):

        @register_special_data_source("zz")
        class NoForm(DataSourceStandardizationPipeline):
            pass


def test_decorator_needs_a_pipeline_subclass():
    with pytest.raises(TypeError, match="must subclass"):

        @register_special_data_source("zz", form_code="special")
        class NotAPipeline:
            pass
