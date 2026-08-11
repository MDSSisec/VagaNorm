from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Issue:
    id: str
    kind: str
    key: str
    title: str
    message: str
    rows: list[int] = field(default_factory=list)
    row_indices: list[int] = field(default_factory=list, repr=False)
    original: Any = None
    context: dict[str, Any] = field(default_factory=dict)
    suggestions: list[dict[str, str]] = field(default_factory=list)
    blocking: bool = True

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("row_indices", None)
        return data


@dataclass
class PipelineResult:
    dataframe: Any
    issues: list[Issue]
    warnings: list[str] = field(default_factory=list)
    changes: dict[str, int] = field(default_factory=dict)
    excluded_query_indices: set[int] = field(default_factory=set)


@dataclass
class ValidationMessage:
    level: str
    code: str
    message: str
    rows: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
