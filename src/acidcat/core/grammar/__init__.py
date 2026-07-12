"""Declarative grammar engine: format descriptors (data) + one interpreter
that emits the hand-written walkers' exact field model.

Opt-in and test-only for now: nothing on the ``import acidcat`` path imports
this package, walk_file dispatch is unchanged, and the walkers remain the
oracle and the default.
"""

from acidcat.core.grammar.interp import interpret
from acidcat.core.grammar.model import Field, Format, Region
from acidcat.core.grammar.types import Enum, Int

__all__ = ["interpret", "Format", "Region", "Field", "Int", "Enum"]
