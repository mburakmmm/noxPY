"""Mixed Python + native Nox package."""

from __future__ import annotations

from . import _native
from ._native import (
    add,
    boom_list,
    checked_div,
    double_map,
    echo_blobs,
    greet,
    nested_groups,
    opt_int,
    opt_str,
    scale_list,
    sum_list,
    swap_pair,
    tag_bytes,
)


def greet_many(names: list[str]) -> list[str]:
    """Pure-Python wrapper calling the compiled Nox kernel."""
    return [greet(name) for name in names]


__all__ = [
    "_native",
    "add",
    "boom_list",
    "checked_div",
    "double_map",
    "echo_blobs",
    "greet",
    "greet_many",
    "nested_groups",
    "opt_int",
    "opt_str",
    "scale_list",
    "sum_list",
    "swap_pair",
    "tag_bytes",
]
