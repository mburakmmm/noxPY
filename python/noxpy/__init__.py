"""NoxPy — Native Python extensions written in Nox."""

from .config import NoxPyProjectConfig, load_pyproject
from .errors import NoxPyError
from .ir import (
    ExportedFunctionIR,
    ModuleIR,
    ParamIR,
    SourceSpan,
    parse_upstream_metadata_json,
)

__version__ = "0.1.0a1"

__all__ = [
    "__version__",
    "ExportedFunctionIR",
    "ModuleIR",
    "NoxPyError",
    "NoxPyProjectConfig",
    "ParamIR",
    "SourceSpan",
    "load_pyproject",
    "parse_upstream_metadata_json",
]
