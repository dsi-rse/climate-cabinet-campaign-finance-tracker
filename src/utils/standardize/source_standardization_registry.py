"""Stores registry of all data source pipelines by state

For standard states (ones that only require yaml configuration),
they will be registered by the `register_all_standard_data_sources` function.

For special states (ones that need Python as well as a config), a module
named after the state's two-letter code in SPECIAL_PIPELINE_PACKAGE
(utils/standardize/finance/il.py for Illinois) decorates its pipeline classes
with `register_special_data_source`; `register_all_special_data_sources`
imports those modules. A special pipeline is registered before the config-only
one for the same state and form, so it replaces it.
"""

import importlib
from collections.abc import Callable
from pathlib import Path

import yaml

from utils.constants import STATE_FINANCE_CONFIG_DIRECTORY
from utils.standardize.source import DataSourceStandardizationPipeline

# package whose <state code>.py modules hold special pipelines
SPECIAL_PIPELINE_PACKAGE = "utils.standardize.finance"

_FINANCE_SOURCE_REGISTRY: dict[str, list[DataSourceStandardizationPipeline]] = {}


def register_data_source(
    state: str, data_source: DataSourceStandardizationPipeline
) -> None:
    """Add data source pipeline to registry

    If a data source pipeline with this form_code is already registered,
    it will be skipped.

    Args:
        state: two letter state abbreviation
        data_source: pipeline defining how to standardize data source
    """
    if state not in _FINANCE_SOURCE_REGISTRY:
        _FINANCE_SOURCE_REGISTRY[state] = []
    if data_source.form_code not in [
        pipeline.form_code for pipeline in _FINANCE_SOURCE_REGISTRY[state]
    ]:
        _FINANCE_SOURCE_REGISTRY[state].append(data_source)


def get_registered_sources() -> dict[str, list[DataSourceStandardizationPipeline]]:
    """Retrieve dictionary mapping state abbreviations to their data source pipelines"""
    return _FINANCE_SOURCE_REGISTRY


def _requested_states(states: list[str] | str | None) -> set[str] | None:
    """Lowercase state codes to register, or None for every state

    Args:
        states: A state code, a list of them, or None.
    """
    if states is None:
        return None
    if isinstance(states, str):
        states = [states]
    return {state.lower() for state in states}


def register_all_data_source_pipelines(
    states: list[str] | str | None = None,
) -> None:
    """Register every pipeline for the requested states

    Special (Python) pipelines are registered first, so one replaces the
    config-only pipeline of the same form.

    Args:
        states: A state code, a list of them, or None for every state.
    """
    register_all_special_data_sources(states)
    register_all_standard_data_sources(states)


def register_all_special_data_sources(
    states: list[str] | str | None = None,
) -> None:
    """Import the modules of states whose pipelines need Python

    A module is SPECIAL_PIPELINE_PACKAGE/<state code>.py; importing it runs
    its `register_special_data_source` decorators. Modules whose names start
    with an underscore are not state modules.

    Args:
        states: A state code, a list of them, or None for every state.
    """
    wanted = _requested_states(states)
    package = importlib.import_module(SPECIAL_PIPELINE_PACKAGE)
    for module_path in sorted(Path(package.__file__).parent.glob("*.py")):
        state = module_path.stem
        if state.startswith("_") or (wanted is not None and state not in wanted):
            continue
        importlib.import_module(f"{SPECIAL_PIPELINE_PACKAGE}.{state}")


def register_special_data_source(
    state: str,
    *,
    form_code: str | None = None,
    init_kwargs: dict | None = None,
) -> Callable[[DataSourceStandardizationPipeline], DataSourceStandardizationPipeline]:
    """Decorator factory for special (code) pipelines.

    Usage, in utils/standardize/finance/ca.py:
        @register_special_data_source("ca", form_code="990")
        class CA990(DataSourceStandardizationPipeline):
            ...

    - Creates an instance of the class with:
        state_code = <state>
        form_code  = <form_code> (or the class's form_code attribute)
        **init_kwargs (optional)
    - Calls register_data_source(state, instance).
    - Returns the class unchanged.
    The form's configuration still comes from the state's yaml file, under
    the form_code key.
    """

    def _data_source_pipeline_decorator(
        cls: DataSourceStandardizationPipeline,
    ) -> DataSourceStandardizationPipeline:
        """Decorator for data source pipelines"""
        if not issubclass(cls, DataSourceStandardizationPipeline):
            raise TypeError(
                "Decorator target must subclass DataSourceStandardizationPipeline"
            )

        code = form_code if form_code is not None else getattr(cls, "form_code", None)
        if code is None:
            raise ValueError(
                f"form_code must be given to the decorator or as a class attribute"
                f" of {cls.__name__}"
            )

        kwargs = dict(init_kwargs or {})
        # Defaults only if not provided by init_kwargs
        kwargs.setdefault("state_code", state)
        kwargs.setdefault("form_code", code)

        # create initialized pipeline instance
        instance = cls(**kwargs)
        register_data_source(state, instance)

        # return the class unchanged
        return cls

    return _data_source_pipeline_decorator


def register_all_standard_data_sources(
    states: list[str] | str | None = None,
) -> None:
    """Register a config-only pipeline for every non-meta form of each state

    Args:
        states: A state code, a list of them, or None for every state.
    """
    wanted = _requested_states(states)
    for state_configuration in sorted(STATE_FINANCE_CONFIG_DIRECTORY.glob("*.yaml")):
        state = state_configuration.stem
        if wanted is not None and state not in wanted:
            continue
        with state_configuration.open() as f:
            config = yaml.safe_load(f)
            for form_code in config.keys():
                if config[form_code].get("meta", False):
                    continue
                pipeline = DataSourceStandardizationPipeline(
                    state_code=state,
                    form_code=form_code,
                    config_file=state_configuration,
                )
                register_data_source(state, pipeline)
