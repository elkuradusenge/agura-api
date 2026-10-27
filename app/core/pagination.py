"""Pagination, sorting and the shared list envelope."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Annotated, Any, Generic, TypeVar

from fastapi import Depends, Query
from pydantic import BaseModel

T = TypeVar("T")


@dataclass(frozen=True)
class PageParams:
    page: int
    page_size: int
    sort: str | None
    order: str

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def pagination_params(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    sort: Annotated[str | None, Query(max_length=60)] = None,
    order: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
) -> PageParams:
    return PageParams(page=page, page_size=page_size, sort=sort, order=order)


PageParamsDep = Annotated[PageParams, Depends(pagination_params)]


class PaginationMeta(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int


class Page(BaseModel, Generic[T]):
    items: list[T]
    pagination: PaginationMeta


def build_page(items: list[Any], total: int, params: PageParams) -> dict[str, Any]:
    total_pages = math.ceil(total / params.page_size) if params.page_size else 0
    return {
        "items": items,
        "pagination": {
            "page": params.page,
            "page_size": params.page_size,
            "total": total,
            "total_pages": total_pages,
        },
    }
