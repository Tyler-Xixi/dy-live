"""Structured result of one account's batch order run."""
from dataclasses import dataclass


@dataclass(frozen=True)
class BatchOutcome:
    kind: str
    confirmed_orders: int
    reason: str
