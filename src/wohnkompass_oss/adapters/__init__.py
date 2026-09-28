"""Portal adapters. Adding a portal = one module + an entry here (see docs/adding-a-portal.md)."""

from __future__ import annotations

from .base import Adapter
from .derstandard import DerStandardAdapter
from .immowelt import ImmoweltAdapter

ADAPTERS: dict[str, Adapter] = {
    "immowelt": ImmoweltAdapter(),
    "derstandard": DerStandardAdapter(),
}


def get_adapter(name: str) -> Adapter:
    if name not in ADAPTERS:
        raise KeyError(f"unknown portal: {name}")
    return ADAPTERS[name]


__all__ = ["ADAPTERS", "Adapter", "get_adapter"]
