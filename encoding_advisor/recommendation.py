from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class Decision:
    name: str
    value: Any
    reason: str
    basis: str
    rule_id: str
    confidence: str = "medium"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def decision(
    name: str,
    value: Any,
    reason: str,
    basis: str,
    rule_id: str,
    confidence: str = "medium",
) -> dict[str, Any]:
    return Decision(
        name=name,
        value=value,
        reason=reason,
        basis=basis,
        rule_id=rule_id,
        confidence=confidence,
    ).to_dict()
