"""Core database models for the simplified crawl MVP."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    JSON,
    Float,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TaskStatus(str, Enum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"


class ProductStatus(str, Enum):
    active = "active"
    archived = "archived"


class OpportunityDecision(str, Enum):
    ignored = "ignored"
    matched = "matched"
    created = "created"


class CrawlTask(Base):
    __tablename__ = "crawl_task"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    keywords: Mapped[str] = mapped_column(String(255), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False, default="xhs")
    status: Mapped[TaskStatus] = mapped_column(
        SAEnum(TaskStatus, native_enum=False), nullable=False, default=TaskStatus.pending
    )
    note_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class RawNote(Base):
    __tablename__ = "raw_note"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("crawl_task.id"), nullable=False, index=True)
    note_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    author: Mapped[str | None] = mapped_column(String(128), nullable=True)
    likes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    comments_cnt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    collected_cnt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    share_cnt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    note_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    crawled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now()
    )


class CrawlTaskNote(Base):
    __tablename__ = "crawl_task_note"
    __table_args__ = (
        UniqueConstraint("task_id", "note_id", name="uq_crawl_task_note_task_note"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("crawl_task.id"), nullable=False, index=True)
    note_id: Mapped[str] = mapped_column(ForeignKey("raw_note.note_id"), nullable=False, index=True)
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now()
    )


class RawComment(Base):
    __tablename__ = "raw_comment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    note_id: Mapped[str] = mapped_column(ForeignKey("raw_note.note_id"), nullable=False, index=True)
    comment_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    author: Mapped[str | None] = mapped_column(String(128), nullable=True)
    likes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parent_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    crawled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now()
    )


class DiscoverWatchKeyword(Base):
    __tablename__ = "discover_watch_keyword"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    keyword: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False, default="xhs")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    poll_interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Product(Base):
    __tablename__ = "product"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    short_description: Mapped[str] = mapped_column(Text, nullable=False)
    full_description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ProductStatus] = mapped_column(
        SAEnum(ProductStatus, native_enum=False), nullable=False, default=ProductStatus.active
    )
    source_keyword: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ProductAssessment(Base):
    __tablename__ = "product_assessment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("product.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    personal_fit_score: Mapped[float] = mapped_column(Float, nullable=False)
    value_score: Mapped[float] = mapped_column(Float, nullable=False)
    competition_opportunity_score: Mapped[float] = mapped_column(Float, nullable=False)
    self_control_score: Mapped[float] = mapped_column(Float, nullable=False)
    total_score: Mapped[float] = mapped_column(Float, nullable=False)
    scores: Mapped[dict] = mapped_column(JSON, nullable=False)
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ProductOpportunity(Base):
    __tablename__ = "product_opportunity"
    __table_args__ = (
        UniqueConstraint("task_id", "note_id", name="uq_product_opportunity_task_note"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("crawl_task.id"), nullable=False, index=True)
    note_id: Mapped[str] = mapped_column(ForeignKey("raw_note.note_id"), nullable=False, index=True)
    decision: Mapped[OpportunityDecision] = mapped_column(
        SAEnum(OpportunityDecision, native_enum=False), nullable=False
    )
    product_id: Mapped[int | None] = mapped_column(ForeignKey("product.id"), nullable=True, index=True)
    prescreen_score: Mapped[float] = mapped_column(Float, nullable=False)
    value_score: Mapped[float] = mapped_column(Float, nullable=False)
    competition_opportunity_score: Mapped[float] = mapped_column(Float, nullable=False)
    self_control_score: Mapped[float] = mapped_column(Float, nullable=False)
    total_score: Mapped[float] = mapped_column(Float, nullable=False)
    scores: Mapped[dict] = mapped_column(JSON, nullable=False)
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now()
    )


class OpportunityNoteFailure(Base):
    __tablename__ = "opportunity_note_failure"
    __table_args__ = (
        UniqueConstraint("task_id", "note_id", name="uq_opportunity_note_failure_task_note"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("crawl_task.id"), nullable=False, index=True)
    note_id: Mapped[str] = mapped_column(ForeignKey("raw_note.note_id"), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(64), nullable=False, default="note_pipeline")
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class OpportunityNoteIgnored(Base):
    __tablename__ = "opportunity_note_ignored"
    __table_args__ = (
        UniqueConstraint("task_id", "note_id", name="uq_opportunity_note_ignored_task_note"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("crawl_task.id"), nullable=False, index=True)
    note_id: Mapped[str] = mapped_column(ForeignKey("raw_note.note_id"), nullable=False, index=True)
    prescreen_score: Mapped[float] = mapped_column(Float, nullable=False)
    prescreen_threshold: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now(), onupdate=func.now()
    )


CORE_TABLES: tuple[str, ...] = (
    "crawl_task",
    "raw_note",
    "crawl_task_note",
    "raw_comment",
    "discover_watch_keyword",
    "product",
    "product_assessment",
    "product_opportunity",
    "opportunity_note_failure",
    "opportunity_note_ignored",
)
