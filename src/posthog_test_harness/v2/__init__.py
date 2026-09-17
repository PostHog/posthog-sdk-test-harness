"""Versioned execution boundary; independent of the legacy YAML runner."""

from .contracts import BoundaryError, Contracts

__all__ = ["BoundaryError", "Contracts"]
