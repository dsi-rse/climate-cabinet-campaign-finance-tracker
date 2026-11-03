"""Package for reading and standardizing state level campaign data

The DataSource class is subclassed for each unique source of data. For
more information, see CONTRIBUTING.md for how to add additional
states
"""

from utils.standardize._core import standardize_state

__all__ = ["standardize_state"]
