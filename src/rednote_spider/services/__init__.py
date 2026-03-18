"""Service layer package."""

from __future__ import annotations

__all__ = [
    "ProductOpportunityService",
]


def __getattr__(name: str):
    if name == "ProductOpportunityService":
        from .product_opportunity_service import ProductOpportunityService

        return ProductOpportunityService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
