"""Structured diagnostic errors for NoxPy (`AGENTS.md` §11)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DiagnosticLocation:
    file: str
    line: int | None = None
    column: int | None = None

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = {"file": self.file}
        if self.line is not None:
            out["line"] = self.line
        if self.column is not None:
            out["column"] = self.column
        return out


class NoxPyError(Exception):
    """Base error with stable `NPY-E-*` diagnostic code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        location: DiagnosticLocation | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.location = location
        self.hint = hint

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = {"code": self.code, "message": self.message}
        if self.location is not None:
            out["location"] = self.location.to_dict()
        if self.hint is not None:
            out["hint"] = self.hint
        return out
