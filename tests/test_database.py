from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from rednote_spider.database import make_engine
from rednote_spider.models import (
    Base,
    CrawlTask,
    OpportunityDecision,
    ProductOpportunity,
    RawNote,
    TaskStatus,
)


def test_make_engine_json_serializer_keeps_unicode(tmp_path: Path):
    db_path = tmp_path / "unicode_json.db"
    engine = make_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        task = CrawlTask(keywords="测试", platform="xhs", status=TaskStatus.done, note_count=1)
        session.add(task)
        session.flush()

        note = RawNote(
            task_id=task.id,
            note_id="note-1",
            title="中文标题",
            content="中文内容",
            likes=1,
            comments_cnt=0,
            collected_cnt=0,
            share_cnt=0,
        )
        session.add(note)
        session.flush()

        opp = ProductOpportunity(
            task_id=task.id,
            note_id=note.note_id,
            decision=OpportunityDecision.ignored,
            product_id=None,
            prescreen_score=1.0,
            value_score=1.0,
            competition_opportunity_score=1.0,
            self_control_score=1.0,
            total_score=10.0,
            scores={"label": "中文分数"},
            evidence={"note_title": "中文标题", "note_excerpt": "中文内容"},
        )
        session.add(opp)
        session.commit()

    with engine.connect() as conn:
        evidence_text = conn.execute(text("select evidence from product_opportunity limit 1")).scalar_one()

    assert "中文标题" in evidence_text
    assert "\\u4e2d\\u6587" not in evidence_text
