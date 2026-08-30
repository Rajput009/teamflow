"""Shared string-safety types for request schemas.

SafeStr: PostgreSQL's UTF-8 parser rejects NUL bytes ("invalid byte sequence
... 0x00"), but JSON strings can carry them freely — so without this guard, a
single "\u0000" in any text field sails through Pydantic validation and
detonates at flush time as an unhandled DataError -> 500.

NotBlankStr: additionally rejects whitespace-only input. min_length=1 alone
accepts " ", which services then .strip() into a legal-but-empty "" stored
value. The gate is here so every named field enforces it identically.
"""
from typing import Annotated

from pydantic import AfterValidator


def _reject_nul(value: str) -> str:
    if "\x00" in value:
        raise ValueError("must not contain NUL characters")
    return value


def _not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must contain visible characters")
    return value


SafeStr = Annotated[str, AfterValidator(_reject_nul)]

NotBlankStr = Annotated[
    str, AfterValidator(_reject_nul), AfterValidator(_not_blank)
]
