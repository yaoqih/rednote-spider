"""Core package for rednote-spider crawl MVP."""

from __future__ import annotations

from typing import Any

__all__ = [
    "CrawlTask",
    "TaskStatus",
    "CrawlTaskService",
    "DiscoverService",
    "KeywordCrawlService",
    "ProductOpportunityService",
    "RawIngestService",
]


def __getattr__(name: str) -> Any:
    if name in {"CrawlTask", "TaskStatus"}:
        from .models import CrawlTask, TaskStatus

        return {"CrawlTask": CrawlTask, "TaskStatus": TaskStatus}[name]
    if name == "CrawlTaskService":
        from .services.crawl_task_service import CrawlTaskService

        return CrawlTaskService
    if name == "DiscoverService":
        from .services.discover_service import DiscoverService

        return DiscoverService
    if name == "KeywordCrawlService":
        from .services.keyword_crawl_service import KeywordCrawlService

        return KeywordCrawlService
    if name == "ProductOpportunityService":
        from .services.product_opportunity_service import ProductOpportunityService

        return ProductOpportunityService
    if name == "RawIngestService":
        from .services.raw_ingest_service import RawIngestService

        return RawIngestService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
