"""Registry of all state collection functions

Collectors register themselves with register_special_state_collector when their
module is imported; utils.collect imports every module under collect/finance
and collect/election, so a new state's module is found by being there.
"""

import inspect
from collections.abc import Callable

# Maps of state abbreviation to tuple of function and list of arguments to pass to function
_STATE_COLLECTOR_REGISTRY: dict[str, tuple[Callable[[], None]], list[str]] = {}


def register_state_collector(
    state: str, collection_func: Callable[[], None], args: list[str]
) -> None:
    """Register a state collection function

    Args:
        state: State abbreviation
        collection_func: Function to register
        args: List of arguments to pass to function
    """
    _STATE_COLLECTOR_REGISTRY[state] = (collection_func, args)


def get_state_collectors() -> dict[str, tuple[Callable[[], None], list[str]]]:
    """Get a state collection function

    Returns:
        dict[str, tuple[Callable[[], None], list[str]]]: Mapping of state abbreviation
           to tuple of function and list of arguments to pass to function
    """
    return _STATE_COLLECTOR_REGISTRY


def register_special_state_collector(
    state: str,
) -> Callable[[Callable[[], None]], Callable[[], None]]:
    """Decorator factory for special state collectors

    Usage:
        @register_special_state_collector("ca")
        def ca_collector():
            ...
    """

    def _state_collector_decorator(
        collection_func: Callable[[], None],
    ) -> Callable[[], None]:
        """Decorator for state collectors"""
        # get collection function arguments
        collection_func_args = list(
            inspect.signature(collection_func).parameters.keys()
        )
        register_state_collector(state, collection_func, collection_func_args)
        return collection_func

    return _state_collector_decorator
