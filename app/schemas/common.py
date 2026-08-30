import math

from pydantic import BaseModel


class Page[ItemT](BaseModel):
    """Standard pagination envelope for every list endpoint (06-api-endpoints.md)."""

    items: list[ItemT]
    total: int
    page: int
    limit: int
    pages: int

    @classmethod
    def build(cls, *, items: list, total: int, page: int, limit: int) -> "Page":
        return cls(
            items=items,
            total=total,
            page=page,
            limit=limit,
            pages=math.ceil(total / limit),
        )
