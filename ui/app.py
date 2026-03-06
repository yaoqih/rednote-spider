"""Streamlit console for the simplified crawl/discover MVP."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

import streamlit as st
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from rednote_spider.config import settings
from rednote_spider.database import make_engine
from rednote_spider.discover_collectors import CommandKeywordCollector
from rednote_spider.models import (
    CrawlTask,
    CrawlTaskNote,
    DiscoverWatchKeyword,
    OpportunityNoteFailure,
    OpportunityNoteIgnored,
    Product,
    ProductAssessment,
    ProductOpportunity,
    RawNote,
)
from rednote_spider.observability import configure_logging, get_logger, log_database_target
from rednote_spider.services.crawl_task_service import CrawlTaskService
from rednote_spider.services.discover_service import DiscoverService
from rednote_spider.services.keyword_crawl_service import KeywordCrawlService
from rednote_spider.ui_security import mask_database_url, validate_access_token

logger = get_logger(__name__)


def _dt(value: datetime | None) -> str | None:
    return value.isoformat(sep=" ", timespec="seconds") if value else None


def _require_streamlit_run() -> None:
    from streamlit import runtime

    if not runtime.exists():
        raise SystemExit("Run with: streamlit run ui/app.py")


def _build_runtime(database_url: str) -> tuple[Any, sessionmaker[Session]]:
    engine = make_engine(database_url)
    factory = sessionmaker(bind=engine, class_=Session, autoflush=False, autocommit=False)
    return engine, factory


def _fetch_tasks(factory: sessionmaker[Session], *, limit: int = 100) -> list[dict[str, Any]]:
    with factory() as session:
        rows = session.execute(
            select(CrawlTask).order_by(CrawlTask.created_at.desc(), CrawlTask.id.desc()).limit(limit)
        ).scalars()
        return [
            {
                "id": row.id,
                "status": row.status.value,
                "platform": row.platform,
                "keywords": row.keywords,
                "note_count": row.note_count,
                "error_message": row.error_message,
                "created_at": _dt(row.created_at),
                "updated_at": _dt(row.updated_at),
            }
            for row in rows
        ]


def _fetch_keywords(factory: sessionmaker[Session], *, limit: int = 300) -> list[dict[str, Any]]:
    with factory() as session:
        rows = session.execute(
            select(DiscoverWatchKeyword)
            .order_by(DiscoverWatchKeyword.id.asc())
            .limit(limit)
        ).scalars()
        return [
            {
                "id": row.id,
                "keyword": row.keyword,
                "platform": row.platform,
                "enabled": row.enabled,
                "poll_interval_minutes": row.poll_interval_minutes,
                "last_polled_at": _dt(row.last_polled_at),
                "created_at": _dt(row.created_at),
                "updated_at": _dt(row.updated_at),
            }
            for row in rows
        ]


def _safe_dict(payload: Any) -> dict[str, Any]:
    return payload if isinstance(payload, dict) else {}


def _task_label(task: dict[str, Any]) -> str:
    return f"#{task['id']} | {task['status']} | {task['keywords']} | {task['created_at'] or '-'}"


def _fetch_pipeline_snapshot(factory: sessionmaker[Session], *, task_id: int) -> dict[str, Any]:
    with factory() as session:
        task = session.get(CrawlTask, task_id)
        if task is None:
            return {
                "task": None,
                "summary": {},
                "product_rows": [],
                "note_rows": [],
            }

        total_notes = int(
            session.execute(
                select(func.count()).select_from(CrawlTaskNote).where(CrawlTaskNote.task_id == task_id)
            ).scalar_one()
        )

        rows = session.execute(
            select(ProductOpportunity, RawNote, Product, ProductAssessment)
            .join(RawNote, RawNote.note_id == ProductOpportunity.note_id)
            .outerjoin(Product, Product.id == ProductOpportunity.product_id)
            .outerjoin(ProductAssessment, ProductAssessment.product_id == Product.id)
            .where(ProductOpportunity.task_id == task_id)
            .order_by(ProductOpportunity.created_at.desc(), ProductOpportunity.id.desc())
        ).all()

        mapped_note_rows: list[dict[str, Any]] = []
        product_map: dict[int, dict[str, Any]] = {}
        for opp, note, product, assessment in rows:
            evidence = _safe_dict(opp.evidence)
            decision_trace = _safe_dict(evidence.get("decision_trace"))
            product_evidence = _safe_dict(evidence.get("product_evidence"))
            product_total_score = float(assessment.total_score) if assessment is not None else None

            mapped_note_rows.append(
                {
                    "opportunity_id": opp.id,
                    "note_id": opp.note_id,
                    "note_title": note.title or "",
                    "note_author": note.author or "",
                    "note_url": note.note_url or "",
                    "decision": opp.decision.value,
                    "product_id": opp.product_id,
                    "product_name": product.name if product is not None else "",
                    "prescreen_score": float(opp.prescreen_score),
                    "snapshot_total_score": float(opp.total_score),
                    "product_total_score": product_total_score,
                    "created_at": _dt(opp.created_at),
                    "decision_trace": decision_trace,
                    "product_evidence": product_evidence,
                }
            )

            if product is None:
                continue
            item = product_map.get(product.id)
            if item is None:
                assessment_evidence = _safe_dict(assessment.evidence) if assessment is not None else {}
                lifecycle = _safe_dict(assessment_evidence.get("product_lifecycle"))
                item = {
                    "product_id": product.id,
                    "name": product.name,
                    "status": product.status.value,
                    "short_description": product.short_description,
                    "total_score": product_total_score,
                    "linked_notes": 0,
                    "matched_notes": 0,
                    "created_notes": 0,
                    "assessment_updated_at": _dt(assessment.updated_at) if assessment is not None else None,
                    "assessment_evidence": assessment_evidence,
                    "generation_note_count": int(lifecycle.get("generation_note_count") or 0),
                    "next_regenerate_at_linked_notes": int(
                        lifecycle.get("next_regenerate_at_linked_notes") or 0
                    ),
                    "regenerated_this_round": bool(lifecycle.get("regenerated_this_round", False)),
                }
                product_map[product.id] = item
            item["linked_notes"] += 1
            if opp.decision.value == "matched":
                item["matched_notes"] += 1
            if opp.decision.value == "created":
                item["created_notes"] += 1
            if item["total_score"] is None and product_total_score is not None:
                item["total_score"] = product_total_score

        ignored_records = session.execute(
            select(OpportunityNoteIgnored, RawNote)
            .outerjoin(RawNote, RawNote.note_id == OpportunityNoteIgnored.note_id)
            .where(OpportunityNoteIgnored.task_id == task_id)
            .order_by(OpportunityNoteIgnored.updated_at.desc(), OpportunityNoteIgnored.id.desc())
        ).all()
        ignored_note_rows = [
            {
                "opportunity_id": f"ignored-{ignored.id}",
                "note_id": ignored.note_id,
                "note_title": note.title if note is not None and note.title else "",
                "note_author": note.author if note is not None and note.author else "",
                "note_url": note.note_url if note is not None and note.note_url else "",
                "decision": "ignored",
                "product_id": None,
                "product_name": "",
                "prescreen_score": float(ignored.prescreen_score),
                "snapshot_total_score": None,
                "product_total_score": None,
                "created_at": _dt(ignored.updated_at),
                "decision_trace": {
                    "task_id": int(ignored.task_id),
                    "note_id": ignored.note_id,
                    "prescreen_reason": ignored.reason,
                    "prescreen_threshold": float(ignored.prescreen_threshold),
                },
                "product_evidence": {},
            }
            for ignored, note in ignored_records
        ]
        note_rows = mapped_note_rows + ignored_note_rows

        failure_rows: list[dict[str, Any]] = []
        try:
            failed_records = session.execute(
                select(OpportunityNoteFailure, RawNote)
                .outerjoin(RawNote, RawNote.note_id == OpportunityNoteFailure.note_id)
                .where(OpportunityNoteFailure.task_id == task_id)
                .order_by(OpportunityNoteFailure.updated_at.desc(), OpportunityNoteFailure.id.desc())
            ).all()
            for failure, note in failed_records:
                failure_rows.append(
                    {
                        "failure_id": failure.id,
                        "note_id": failure.note_id,
                        "note_title": note.title if note is not None and note.title else "",
                        "note_url": note.note_url if note is not None and note.note_url else "",
                        "stage": failure.stage,
                        "retry_count": int(failure.retry_count),
                        "error_message": failure.error_message,
                        "updated_at": _dt(failure.updated_at),
                    }
                )
        except SQLAlchemyError:
            failure_rows = []

        product_rows = list(product_map.values())
        product_rows.sort(
            key=lambda row: (
                row["total_score"] is not None,
                row["total_score"] if row["total_score"] is not None else -1.0,
                row["linked_notes"],
            ),
            reverse=True,
        )

        matched_notes = sum(1 for row in note_rows if row["decision"] == "matched")
        created_notes = sum(1 for row in note_rows if row["decision"] == "created")
        ignored_notes = sum(1 for row in note_rows if row["decision"] == "ignored")
        mapped_notes = len(mapped_note_rows)
        processed_notes = len(note_rows)
        failed_notes = len(failure_rows)
        pending_notes = max(total_notes - processed_notes - failed_notes, 0)
        assessed_products = sum(1 for row in product_rows if row["total_score"] is not None)

        summary = {
            "task_id": task.id,
            "task_status": task.status.value,
            "task_keywords": task.keywords,
            "task_note_count": task.note_count,
            "total_notes": total_notes,
            "mapped_notes": mapped_notes,
            "ignored_notes": ignored_notes,
            "failed_notes": failed_notes,
            "pending_notes": pending_notes,
            "matched_notes": matched_notes,
            "created_notes": created_notes,
            "products_involved": len(product_rows),
            "assessed_products": assessed_products,
        }
        return {
            "task": {
                "id": task.id,
                "status": task.status.value,
                "keywords": task.keywords,
                "note_count": task.note_count,
                "created_at": _dt(task.created_at),
                "updated_at": _dt(task.updated_at),
            },
            "summary": summary,
            "product_rows": product_rows,
            "note_rows": note_rows,
            "failure_rows": failure_rows,
        }


def _draw_pipeline_results(factory: sessionmaker[Session]) -> None:
    st.subheader("后续流程结果中心")
    done_tasks = [row for row in _fetch_tasks(factory, limit=200) if row["status"] == "done"]
    if not done_tasks:
        st.info("暂无已完成任务可查看结果。先运行 crawl/discover。")
        return

    task_by_id = {int(row["id"]): row for row in done_tasks}
    options = [int(row["id"]) for row in done_tasks]
    options.sort(reverse=True)

    col_left, col_right = st.columns([4, 1])
    with col_left:
        selected_task_id = int(
            st.selectbox(
                "选择任务",
                options=options,
                format_func=lambda task_id: _task_label(task_by_id[int(task_id)]),
                key="results_task_selector",
            )
        )
    with col_right:
        st.write("")
        st.write("")
        if st.button("刷新结果", key="results_refresh_button"):
            st.rerun()

    snapshot = _fetch_pipeline_snapshot(factory, task_id=selected_task_id)
    summary = snapshot["summary"]
    product_rows = snapshot["product_rows"]
    note_rows = snapshot["note_rows"]
    failure_rows = snapshot["failure_rows"]

    m1, m2, m3, m4, m5, m6, m7 = st.columns(7)
    m1.metric("抓取 Note", int(summary.get("total_notes", 0)))
    m2.metric("进入映射", int(summary.get("mapped_notes", 0)))
    m3.metric("Ignored", int(summary.get("ignored_notes", 0)))
    m4.metric("Matched", int(summary.get("matched_notes", 0)))
    m5.metric("Created", int(summary.get("created_notes", 0)))
    m6.metric("评分产品数", int(summary.get("assessed_products", 0)))
    m7.metric("失败 Note", int(summary.get("failed_notes", 0)))

    tab1, tab2, tab3, tab4 = st.tabs(["流程漏斗", "产品评分榜", "Note 决策明细", "失败 Note 专区"])

    with tab1:
        st.dataframe(
            [
                {"阶段": "抓取入库 Note", "数量": int(summary.get("total_notes", 0))},
                {"阶段": "进入 note->product 映射", "数量": int(summary.get("mapped_notes", 0))},
                {"阶段": "初筛忽略(ignored)", "数量": int(summary.get("ignored_notes", 0))},
                {"阶段": "失败 note", "数量": int(summary.get("failed_notes", 0))},
                {"阶段": "待重试/待处理 note", "数量": int(summary.get("pending_notes", 0))},
                {"阶段": "匹配已有产品", "数量": int(summary.get("matched_notes", 0))},
                {"阶段": "新建产品", "数量": int(summary.get("created_notes", 0))},
                {"阶段": "完成产品评分", "数量": int(summary.get("assessed_products", 0))},
            ],
            width="stretch",
            hide_index=True,
        )
        st.caption("ignored 会保留初筛证据；失败 note 会显示在“失败 Note 专区”。")

    with tab2:
        if not product_rows:
            st.info("当前任务还没有产品结果。")
        else:
            top_n = st.slider(
                "展示前 N 个产品",
                min_value=1,
                max_value=max(len(product_rows), 1),
                value=min(len(product_rows), 20),
                step=1,
                key="result_products_top_n",
            )
            st.dataframe(
                [
                    {
                        "product_id": row["product_id"],
                        "name": row["name"],
                        "status": row["status"],
                        "total_score": row["total_score"],
                        "linked_notes": row["linked_notes"],
                        "generation_note_count": row["generation_note_count"],
                        "next_regenerate_at": row["next_regenerate_at_linked_notes"],
                        "matched_notes": row["matched_notes"],
                        "created_notes": row["created_notes"],
                    }
                    for row in product_rows[: int(top_n)]
                ],
                width="stretch",
                hide_index=True,
            )

            product_index = {
                int(row["product_id"]): row for row in product_rows
            }
            selected_product_id = int(
                st.selectbox(
                    "查看产品评分证据",
                    options=[int(row["product_id"]) for row in product_rows],
                    format_func=lambda product_id: (
                        f"#{product_id} | {product_index[int(product_id)]['name']} | "
                        f"score={product_index[int(product_id)]['total_score']}"
                    ),
                    key="result_product_detail_selector",
                )
            )
            selected_product = product_index[selected_product_id]
            st.markdown(f"**{selected_product['name']}**")
            st.write(selected_product["short_description"])
            lifecycle = _safe_dict(selected_product["assessment_evidence"].get("product_lifecycle"))
            if lifecycle:
                st.caption(
                    "产品生成基线："
                    f"{int(lifecycle.get('generation_note_count') or 0)} | "
                    "当前指向："
                    f"{int(lifecycle.get('linked_note_count') or 0)} | "
                    "下次重生成阈值："
                    f"{int(lifecycle.get('next_regenerate_at_linked_notes') or 0)}"
                )
            st.json(selected_product["assessment_evidence"])

    with tab3:
        if not note_rows:
            st.info("当前任务还没有 note 决策结果。")
        else:
            decision_options = sorted({row["decision"] for row in note_rows})
            f1, f2, f3 = st.columns([2, 2, 1])
            with f1:
                decision_filter = st.multiselect(
                    "Decision 过滤",
                    options=decision_options,
                    default=decision_options,
                    key="result_note_decision_filter",
                )
            with f2:
                keyword_filter = st.text_input(
                    "关键词过滤(note/product)",
                    value="",
                    key="result_note_keyword_filter",
                ).strip()
            with f3:
                only_with_product = st.checkbox(
                    "仅看有产品",
                    value=False,
                    key="result_note_only_with_product",
                )

            filtered_rows: list[dict[str, Any]] = []
            for row in note_rows:
                if decision_filter and row["decision"] not in decision_filter:
                    continue
                if only_with_product and not row["product_id"]:
                    continue
                if keyword_filter:
                    haystack = f"{row['note_title']} {row['product_name']} {row['note_id']}".lower()
                    if keyword_filter.lower() not in haystack:
                        continue
                filtered_rows.append(row)

            st.dataframe(
                [
                    {
                        "note_id": row["note_id"],
                        "note_title": row["note_title"],
                        "decision": row["decision"],
                        "product_id": row["product_id"],
                        "product_name": row["product_name"],
                        "prescreen_score": row["prescreen_score"],
                        "product_total_score": row["product_total_score"],
                        "created_at": row["created_at"],
                    }
                    for row in filtered_rows
                ],
                width="stretch",
                hide_index=True,
            )

            if filtered_rows:
                note_by_id = {row["note_id"]: row for row in filtered_rows}
                selected_note_id = st.selectbox(
                    "查看单条决策详情",
                    options=[row["note_id"] for row in filtered_rows],
                    format_func=lambda note_id: (
                        f"{note_by_id[note_id]['decision']} | "
                        f"{note_by_id[note_id]['note_title'][:24]} | {note_id}"
                    ),
                    key="result_note_detail_selector",
                )
                selected_row = note_by_id[selected_note_id]
                st.markdown(f"**{selected_row['note_title'] or selected_row['note_id']}**")
                if selected_row["note_url"]:
                    st.markdown(f"[打开原始笔记]({selected_row['note_url']})")
                st.caption("decision_trace")
                st.json(selected_row["decision_trace"])
                st.caption("product_evidence")
                st.json(selected_row["product_evidence"])

    with tab4:
        if not failure_rows:
            st.success("当前任务没有失败 note。")
        else:
            stage_options = sorted({row["stage"] for row in failure_rows})
            c1, c2 = st.columns([2, 2])
            with c1:
                stage_filter = st.multiselect(
                    "失败阶段过滤",
                    options=stage_options,
                    default=stage_options,
                    key="result_failure_stage_filter",
                )
            with c2:
                failure_keyword_filter = st.text_input(
                    "关键词过滤(note/error)",
                    value="",
                    key="result_failure_keyword_filter",
                ).strip()

            filtered_failures: list[dict[str, Any]] = []
            for row in failure_rows:
                if stage_filter and row["stage"] not in stage_filter:
                    continue
                if failure_keyword_filter:
                    haystack = f"{row['note_title']} {row['note_id']} {row['error_message']}".lower()
                    if failure_keyword_filter.lower() not in haystack:
                        continue
                filtered_failures.append(row)

            st.dataframe(
                [
                    {
                        "note_id": row["note_id"],
                        "note_title": row["note_title"],
                        "stage": row["stage"],
                        "retry_count": row["retry_count"],
                        "updated_at": row["updated_at"],
                    }
                    for row in filtered_failures
                ],
                width="stretch",
                hide_index=True,
            )

            if filtered_failures:
                failure_index = {row["note_id"]: row for row in filtered_failures}
                selected_failed_note_id = st.selectbox(
                    "查看失败详情",
                    options=[row["note_id"] for row in filtered_failures],
                    format_func=lambda note_id: (
                        f"{failure_index[note_id]['stage']} | "
                        f"retry={failure_index[note_id]['retry_count']} | {note_id}"
                    ),
                    key="result_failure_detail_selector",
                )
                selected_failure = failure_index[selected_failed_note_id]
                if selected_failure["note_title"]:
                    st.markdown(f"**{selected_failure['note_title']}**")
                if selected_failure["note_url"]:
                    st.markdown(f"[打开原始笔记]({selected_failure['note_url']})")
                st.caption("失败原因")
                st.code(selected_failure["error_message"], language="text")


def _draw_main(factory: sessionmaker[Session]) -> None:
    st.subheader("One-off Crawl")
    with st.form("run_once_form"):
        keywords = st.text_input("Keywords", value="通勤 焦虑")
        platform = st.text_input("Platform", value="xhs")
        max_notes = st.number_input("Max Notes", min_value=1, value=20, step=1)
        st.caption("Backend: command (only)")
        command_template = st.text_input("Command Template", value=settings.crawl_command_template)
        run_once = st.form_submit_button("Create Task And Run")

    if run_once:
        try:
            if not command_template.strip():
                raise ValueError("command_template is required")
            with factory() as session:
                task = CrawlTaskService(session).create_task(keywords=keywords, platform=platform)
                result = KeywordCrawlService(session).run_task(
                    task_id=task.id,
                    max_notes=int(max_notes),
                    backend="command",
                    command_template=command_template,
                )
            st.success("Crawl completed")
            st.json(asdict(result))
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))

    st.divider()
    st.subheader("Discover Watchlist")
    col1, col2 = st.columns(2)

    with col1:
        with st.form("add_watch_keyword_form"):
            watch_keyword = st.text_input("Keyword", value="")
            watch_platform = st.text_input("Platform", value="xhs")
            interval = st.number_input("Poll Interval Minutes", min_value=1, value=60, step=1)
            disabled = st.checkbox("Disabled", value=False)
            add_keyword = st.form_submit_button("Add/Upsert Keyword")
        if add_keyword:
            try:
                service = DiscoverService(factory, collector=None)
                row = service.upsert_keyword(
                    keyword=watch_keyword,
                    platform=watch_platform,
                    poll_interval_minutes=int(interval),
                    enabled=not disabled,
                )
                st.success(f"keyword_upserted id={row.id} keyword={row.keyword}")
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))

        with st.form("toggle_keyword_form"):
            keyword_id = st.number_input("Keyword ID", min_value=1, value=1, step=1)
            toggle = st.selectbox("Set Enabled", options=[True, False], index=0)
            do_toggle = st.form_submit_button("Update Keyword Status")
        if do_toggle:
            try:
                service = DiscoverService(factory, collector=None)
                row = service.set_keyword_enabled(int(keyword_id), bool(toggle))
                st.success(f"keyword_updated id={row.id} enabled={row.enabled}")
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))

    with col2:
        with st.form("run_discover_cycle_form"):
            cycles = st.number_input("Cycles", min_value=1, value=1, step=1)
            keyword_limit = st.number_input("Keyword Limit", min_value=1, value=20, step=1)
            note_limit = st.number_input("Note Limit", min_value=1, value=20, step=1)
            interval_seconds = st.number_input("Interval Seconds", min_value=0.0, value=0.0, step=1.0)
            command_template = st.text_input("Discover Command Template", value=settings.crawl_command_template)
            run_cycle = st.form_submit_button("Run Discover Cycle")
        if run_cycle:
            try:
                if not command_template.strip():
                    raise ValueError("command_template is required")

                collector = CommandKeywordCollector(command_template)
                service = DiscoverService(factory, collector)
                payload: list[dict[str, Any]] = []
                for idx in range(int(cycles)):
                    summary = service.run_once(
                        keyword_limit=int(keyword_limit),
                        note_limit=int(note_limit),
                    )
                    payload.append(asdict(summary))
                    if idx < int(cycles) - 1 and interval_seconds > 0:
                        import time

                        time.sleep(float(interval_seconds))
                st.success("discover cycle completed")
                st.json(payload)
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))

    st.caption("Watch Keywords")
    st.dataframe(_fetch_keywords(factory, limit=300), width="stretch", hide_index=True)

    st.divider()
    st.caption("Recent Tasks")
    st.dataframe(_fetch_tasks(factory, limit=100), width="stretch", hide_index=True)

    st.divider()
    _draw_pipeline_results(factory)


def main() -> None:
    configure_logging(settings.log_level)
    _require_streamlit_run()

    st.set_page_config(page_title="rednote-spider (MVP)", layout="wide")
    log_database_target(logger, database_url=settings.database_url, source="streamlit_ui")
    st.title("rednote-spider MVP Console")

    st.sidebar.caption("Environment")
    st.sidebar.code(
        "\n".join(
            [
                f"APP_ENV={settings.app_env}",
                f"DATABASE_URL={mask_database_url(settings.database_url)}",
                "CRAWL_BACKEND=command",
            ]
        )
    )

    allowed, message = validate_access_token(
        expected_token=settings.streamlit_access_token,
        provided_token=st.sidebar.text_input("Access Token", type="password"),
        app_env=settings.app_env,
    )
    if not allowed:
        st.error(message)
        st.stop()

    _, factory = _build_runtime(settings.database_url)
    _draw_main(factory)


if __name__ == "__main__":
    main()
