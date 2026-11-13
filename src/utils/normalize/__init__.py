"""Normalization module"""

from utils.normalize._core import (
    Normalizer,
    combine_duplicates_across_chunks,
    combine_rows_sharing_an_id,
)

__all__ = [
    "Normalizer",
    "combine_rows_sharing_an_id",
    "combine_duplicates_across_chunks",
]
