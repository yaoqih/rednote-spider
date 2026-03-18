"""Manual task runner that chains crawl and opportunity processing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from .keyword_crawl_service import CrawlRunResult, KeywordCrawlService
from .product_opportunity_service import OpportunityRunSummary, ProductOpportunityService


@dataclass(slots=True)
class ManualTaskPipelineResult:
    crawl: CrawlRunResult
    opportunity: OpportunityRunSummary


class ManualTaskPipelineService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        llm: Any | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.llm = llm

    def run(
        self,
        *,
        task_id: int,
        max_notes: int = 20,
        backend: str = "command",
        command_template: str | None = None,
        prescreen_threshold: float = 3.2,
        match_threshold: float = 0.26,
    ) -> ManualTaskPipelineResult:
        with self.session_factory() as session:
            crawl = KeywordCrawlService(session).run_task(
                task_id=task_id,
                max_notes=max_notes,
                backend=backend,
                command_template=command_template,
            )

        with self.session_factory() as session:
            opportunity = ProductOpportunityService(session, llm=self.llm).process_task(
                task_id,
                prescreen_threshold=prescreen_threshold,
                match_threshold=match_threshold,
            )

        return ManualTaskPipelineResult(crawl=crawl, opportunity=opportunity)
