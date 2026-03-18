"""Streamlit console for the simplified crawl/discover MVP."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from inspect import signature
from pathlib import Path
from typing import Any

import streamlit as st
from sqlalchemy import func, select
from sqlalchemy.exc import ProgrammingError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from rednote_spider.config import settings
from rednote_spider.database import make_engine
from rednote_spider.discover_collectors import CommandKeywordCollector
from rednote_spider.models import (
    CrawlTask,
    CrawlTaskNote,
    DiscoverWatchKeyword,
    LoginEvent,
    OpportunityNoteFailure,
    OpportunityNoteIgnored,
    Product,
    ProductAssessment,
    ProductOpportunity,
    RawNote,
    TaskStatus,
)
from rednote_spider.observability import configure_logging, get_logger, log_database_target
from rednote_spider.services.crawl_task_service import CrawlTaskService
from rednote_spider.services.discover_service import DiscoverService
from rednote_spider.services.login_controller_service import LoginControllerService
from rednote_spider.services.manual_task_pipeline_service import ManualTaskPipelineService
from rednote_spider.services.scheduler_config_service import SchedulerConfigService
from rednote_spider.ui_security import validate_access_token

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


def _fetch_tasks(
    factory: sessionmaker[Session],
    *,
    limit: int = 100,
    statuses: list[str] | None = None,
    platform: str | None = None,
    keywords_query: str | None = None,
) -> list[dict[str, Any]]:
    task_statuses = [TaskStatus(item) for item in statuses] if statuses else None
    with factory() as session:
        rows = CrawlTaskService(session).list_tasks(
            statuses=task_statuses,
            platform=platform,
            keywords_query=keywords_query,
            limit=limit,
        )
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


def _fetch_scheduler_configs(factory: sessionmaker[Session]) -> list[dict[str, Any]]:
    rows = SchedulerConfigService(factory).list_configs()
    return [
        {
            "mode": row.mode,
            "enabled": row.enabled,
            "loop_interval_seconds": row.loop_interval_seconds,
            "note_limit": getattr(row, "note_limit", None),
            "created_at": _dt(row.created_at),
            "updated_at": _dt(row.updated_at),
        }
        for row in rows
    ]


def _scheduler_service_supports_note_limit(service_cls: type[Any]) -> bool:
    try:
        return "note_limit" in signature(service_cls.set_config).parameters
    except (AttributeError, TypeError, ValueError):
        return False


def _safe_dict(payload: Any) -> dict[str, Any]:
    return payload if isinstance(payload, dict) else {}


def _task_label(task: dict[str, Any]) -> str:
    return f"#{task['id']} | {task['status']} | {task['keywords']} | {task['created_at'] or '-'}"


def _login_auth_state_message(auth_state: str) -> str:
    if auth_state == "authenticated":
        return "当前 MediaCrawler profile 已登录，并且通过最新探测确认可用。"
    if auth_state == "unauthenticated":
        return "当前 profile 尚未通过最新探测，MediaCrawler 还不能视为已登录。"
    return "当前还没有有效探测结果，请先执行一次登录状态探测。"


def _login_flow_state_message(flow_state: str) -> str:
    if flow_state == "idle":
        return "当前没有活动登录尝试，可以发起探测、二维码登录或手机号登录。"
    if flow_state == "probing":
        return "正在执行登录态探测，请等待最新 probe 结果。"
    if flow_state == "starting":
        return "登录运行时正在启动浏览器与上下文。"
    if flow_state == "waiting_qr_scan":
        return "二维码已生成，请使用小红书 App 扫码，成功后系统会自动复探测。"
    if flow_state == "waiting_phone_code":
        return "当前正在等待短信验证码，请输入 6 位验证码后提交。"
    if flow_state == "waiting_security_verification":
        return "检测到安全校验，请使用已登录小红书 App 扫码处理，完成后再继续探测。"
    if flow_state == "verifying":
        return "系统正在等待扫码或验证码提交后的最终登录结果。"
    if flow_state == "need_human_action":
        return "自动流程无法继续，但浏览器上下文仍保留，可人工接管后再继续探测。"
    if flow_state == "failed":
        return "最近一次登录尝试失败，可查看事件和错误后重新发起。"
    return "当前登录流程状态未知。"


def _login_action_enabled(flow_state: str, action: str) -> bool:
    start_allowed = flow_state in {"idle", "failed"}
    if action in {"start_qr", "start_phone", "request_probe"}:
        return start_allowed
    if action == "submit_sms_code":
        return flow_state == "waiting_phone_code"
    if action == "cancel":
        return flow_state in {
            "starting",
            "probing",
            "waiting_qr_scan",
            "waiting_phone_code",
            "waiting_security_verification",
            "verifying",
            "need_human_action",
        }
    if action == "continue_probe":
        return flow_state in {"waiting_security_verification", "need_human_action", "failed", "idle"}
    return False


def _format_login_event_row(row: LoginEvent) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "attempt_id": int(row.attempt_id),
        "event_type": row.event_type,
        "message": row.message,
        "payload": _safe_dict(row.payload),
        "created_at": _dt(row.created_at),
    }


def _login_qr_empty_state_message(status: str) -> str:
    if status in {"pending", "generating"}:
        return "正在生成二维码。当前环境首次启动浏览器较慢，通常需要 30-90 秒，请稍候等待面板自动刷新。"
    if status == "failed":
        return "二维码生成失败。可点击右侧按钮重试，并查看 Last Error。"
    if status == "expired":
        return "二维码已过期。可点击右侧按钮重新生成。"
    if status == "success":
        return "当前二维码流程已完成。"
    return "当前没有可展示的二维码。可点击右侧按钮生成/刷新。"


def _mask_phone_number(phone_number: str | None) -> str:
    if not phone_number:
        return "-"
    if len(phone_number) < 7:
        return phone_number
    return f"{phone_number[:3]}****{phone_number[-4:]}"


def _login_phone_status_message(status: str) -> str:
    if status == "idle":
        return "输入手机号后点击“开始手机号登录”，后台会自动打开浏览器并请求短信验证码。"
    if status in {"pending", "starting"}:
        return "手机号登录正在启动，请等待浏览器进入验证码页。"
    if status == "need_verify":
        return "检测到安全校验，请先使用已登录小红书 App 扫码完成安全校验，完成后系统会继续进入短信验证码页。"
    if status == "waiting_code":
        return "验证码已发送，请输入 6 位短信验证码并点击提交。"
    if status == "verifying":
        return "验证码已提交，正在等待登录结果。"
    if status == "success":
        return "手机号登录已完成。"
    if status == "failed":
        return "手机号登录失败，可重新开始并查看 Last Error。"
    return "当前手机号登录状态未知。"


def _login_schema_init_hint() -> str:
    return (
        "登录控制表尚未初始化。请先执行 "
        "`DATABASE_URL=... .venv/bin/python scripts/init_schema.py --database-url \"$DATABASE_URL\"` "
        "为当前数据库创建 `login_runtime_state` / `login_event`。"
    )


def _load_login_panel_state(factory: sessionmaker[Session]) -> dict[str, Any]:
    service = LoginControllerService(factory)
    try:
        row = service.get_state()
        events = [_format_login_event_row(item) for item in service.list_events(limit=20)]
        return {"ok": True, "row": row, "events": events, "service": service}
    except ProgrammingError as exc:
        message = str(exc)
        if "login_runtime_state" in message or "login_event" in message:
            return {
                "ok": False,
                "error": message,
                "hint": _login_schema_init_hint(),
            }
        raise


def _fetch_product_overview(factory: sessionmaker[Session]) -> dict[str, Any]:
    with factory() as session:
        rows = session.execute(
            select(Product, ProductAssessment, ProductOpportunity)
            .outerjoin(ProductAssessment, ProductAssessment.product_id == Product.id)
            .outerjoin(ProductOpportunity, ProductOpportunity.product_id == Product.id)
            .order_by(Product.id.asc(), ProductOpportunity.created_at.desc(), ProductOpportunity.id.desc())
        ).all()

        product_map: dict[int, dict[str, Any]] = {}
        for product, assessment, opportunity in rows:
            item = product_map.get(product.id)
            if item is None:
                assessment_evidence = _safe_dict(assessment.evidence) if assessment is not None else {}
                lifecycle = _safe_dict(assessment_evidence.get("product_lifecycle"))
                item = {
                    "product_id": int(product.id),
                    "name": product.name,
                    "status": product.status.value,
                    "source_keyword": product.source_keyword or "",
                    "short_description": product.short_description,
                    "full_description": product.full_description,
                    "total_score": float(assessment.total_score) if assessment is not None else None,
                    "assessment_updated_at": _dt(assessment.updated_at) if assessment is not None else None,
                    "assessment_evidence": assessment_evidence,
                    "linked_notes": 0,
                    "matched_notes": 0,
                    "created_notes": 0,
                    "last_opportunity_at": None,
                    "generation_note_count": int(lifecycle.get("generation_note_count") or 0),
                    "next_regenerate_at_linked_notes": int(
                        lifecycle.get("next_regenerate_at_linked_notes") or 0
                    ),
                    "regenerated_this_round": bool(lifecycle.get("regenerated_this_round", False)),
                    "_seen_note_ids": set(),
                }
                product_map[product.id] = item

            if opportunity is None:
                continue

            note_id = str(opportunity.note_id or "").strip()
            if note_id and note_id not in item["_seen_note_ids"]:
                item["_seen_note_ids"].add(note_id)
                item["linked_notes"] += 1
            if opportunity.decision.value == "matched":
                item["matched_notes"] += 1
            if opportunity.decision.value == "created":
                item["created_notes"] += 1
            opportunity_created_at = _dt(opportunity.created_at)
            if opportunity_created_at and (
                item["last_opportunity_at"] is None or opportunity_created_at > item["last_opportunity_at"]
            ):
                item["last_opportunity_at"] = opportunity_created_at

        product_rows = list(product_map.values())
        for row in product_rows:
            row.pop("_seen_note_ids", None)

        product_rows.sort(
            key=lambda row: (
                row["total_score"] is not None,
                row["total_score"] if row["total_score"] is not None else -1.0,
                row["linked_notes"],
                row["last_opportunity_at"] or "",
            ),
            reverse=True,
        )

        summary = {
            "total_products": len(product_rows),
            "active_products": sum(1 for row in product_rows if row["status"] == "active"),
            "assessed_products": sum(1 for row in product_rows if row["total_score"] is not None),
            "total_linked_notes": sum(int(row["linked_notes"]) for row in product_rows),
            "matched_notes": sum(int(row["matched_notes"]) for row in product_rows),
            "created_notes": sum(int(row["created_notes"]) for row in product_rows),
        }
        return {
            "summary": summary,
            "product_rows": product_rows,
        }


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
                    "source_keyword": product.source_keyword or "",
                    "short_description": product.short_description,
                    "total_score": product_total_score,
                    "task_linked_notes": 0,
                    "task_matched_notes": 0,
                    "task_created_notes": 0,
                    "global_linked_notes": int(lifecycle.get("linked_note_count") or 0),
                    "assessment_updated_at": _dt(assessment.updated_at) if assessment is not None else None,
                    "assessment_evidence": assessment_evidence,
                    "generation_note_count": int(lifecycle.get("generation_note_count") or 0),
                    "next_regenerate_at_linked_notes": int(
                        lifecycle.get("next_regenerate_at_linked_notes") or 0
                    ),
                    "regenerated_this_round": bool(lifecycle.get("regenerated_this_round", False)),
                }
                product_map[product.id] = item
            item["task_linked_notes"] += 1
            if opp.decision.value == "matched":
                item["task_matched_notes"] += 1
            if opp.decision.value == "created":
                item["task_created_notes"] += 1
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
                row["task_linked_notes"],
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


def _draw_global_product_results(factory: sessionmaker[Session]) -> None:
    st.caption("产品总览展示当前全局产品池状态，适合看长期沉淀结果和最新评分。")
    payload = _fetch_product_overview(factory)
    summary = payload["summary"]
    product_rows = payload["product_rows"]

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("产品总数", int(summary.get("total_products", 0)))
    m2.metric("活跃产品", int(summary.get("active_products", 0)))
    m3.metric("已评分产品", int(summary.get("assessed_products", 0)))
    m4.metric("累计关联 Note", int(summary.get("total_linked_notes", 0)))
    m5.metric("Matched 决策", int(summary.get("matched_notes", 0)))
    m6.metric("Created 决策", int(summary.get("created_notes", 0)))

    if not product_rows:
        st.info("当前还没有产品结果。先完成 crawl/discover 流程，系统会在同一轮内自动执行机会评估。")
        return

    latest_assessment_at = max((row["assessment_updated_at"] or "" for row in product_rows), default="") or None
    if latest_assessment_at:
        st.caption(f"最近一次产品评估更新时间：{latest_assessment_at}")

    f1, f2, f3 = st.columns([2, 2, 1])
    status_options = sorted({row["status"] for row in product_rows})
    with f1:
        status_filter = st.multiselect(
            "状态过滤",
            options=status_options,
            default=status_options,
            key="global_product_status_filter",
        )
    with f2:
        keyword_filter = st.text_input(
            "关键词过滤(name/source)",
            value="",
            key="global_product_keyword_filter",
        ).strip()
    with f3:
        min_score = st.number_input(
            "最低总分",
            min_value=0.0,
            max_value=100.0,
            value=0.0,
            step=1.0,
            key="global_product_min_score",
        )

    filtered_rows: list[dict[str, Any]] = []
    for row in product_rows:
        if status_filter and row["status"] not in status_filter:
            continue
        if row["total_score"] is not None and float(row["total_score"]) < float(min_score):
            continue
        if keyword_filter:
            haystack = f"{row['name']} {row['source_keyword']} {row['product_id']}".lower()
            if keyword_filter.lower() not in haystack:
                continue
        filtered_rows.append(row)

    if not filtered_rows:
        st.info("当前筛选条件下没有产品。")
        return

    top_n = _resolve_result_products_top_n(len(filtered_rows))
    if len(filtered_rows) > 1:
        top_n = st.slider(
            "展示前 N 个产品",
            min_value=1,
            max_value=len(filtered_rows),
            value=top_n,
            step=1,
            key="global_product_top_n",
        )
    display_rows = filtered_rows[: int(top_n)]
    st.dataframe(
        [
            {
                "product_id": row["product_id"],
                "name": row["name"],
                "status": row["status"],
                "total_score": row["total_score"],
                "linked_notes": row["linked_notes"],
                "matched_notes": row["matched_notes"],
                "created_notes": row["created_notes"],
                "assessment_updated_at": row["assessment_updated_at"],
                "source_keyword": row["source_keyword"],
            }
            for row in display_rows
        ],
        width="stretch",
        hide_index=True,
    )

    product_index = {int(row["product_id"]): row for row in display_rows}
    selected_product_id = int(
        st.selectbox(
            "查看产品详情",
            options=[int(row["product_id"]) for row in display_rows],
            format_func=lambda product_id: (
                f"#{product_id} | {product_index[int(product_id)]['name']} | "
                f"score={product_index[int(product_id)]['total_score']}"
            ),
            key="global_product_detail_selector",
        )
    )
    selected_product = product_index[selected_product_id]
    st.markdown(f"**{selected_product['name']}**")
    if selected_product["source_keyword"]:
        st.caption(f"source_keyword: {selected_product['source_keyword']}")
    st.write(selected_product["short_description"])

    d1, d2, d3, d4 = st.columns(4)
    d1.metric("当前总分", selected_product["total_score"] if selected_product["total_score"] is not None else "-")
    d2.metric("全局关联 Note", int(selected_product["linked_notes"]))
    d3.metric("下次重生成阈值", int(selected_product["next_regenerate_at_linked_notes"]))
    d4.metric("最近评分更新", selected_product["assessment_updated_at"] or "-")
    st.json(selected_product["assessment_evidence"])


def _draw_task_result_view(factory: sessionmaker[Session]) -> None:
    st.caption("任务结果用于排查单次任务流水。这里展示的产品分数是当前产品态，不是任务执行时快照。")
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

    tab1, tab2, tab3, tab4 = st.tabs(["流程漏斗", "任务涉及产品", "Note 决策明细", "失败 Note 专区"])

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
            st.caption("该列表按当前产品状态展示，只回答“本任务影响到了哪些产品”，不代表历史快照。")
            top_n = _resolve_result_products_top_n(len(product_rows))
            if len(product_rows) > 1:
                top_n = st.slider(
                    "展示前 N 个产品",
                    min_value=1,
                    max_value=len(product_rows),
                    value=top_n,
                    step=1,
                    key="task_result_products_top_n",
                )
            else:
                st.caption("当前仅有 1 个产品结果，已展示全部。")
            st.dataframe(
                [
                    {
                        "product_id": row["product_id"],
                        "name": row["name"],
                        "status": row["status"],
                        "current_total_score": row["total_score"],
                        "task_linked_notes": row["task_linked_notes"],
                        "global_linked_notes": row["global_linked_notes"],
                        "matched_notes_in_task": row["task_matched_notes"],
                        "created_notes_in_task": row["task_created_notes"],
                        "next_regenerate_at": row["next_regenerate_at_linked_notes"],
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
                    key="task_result_product_detail_selector",
                )
            )
            selected_product = product_index[selected_product_id]
            st.markdown(f"**{selected_product['name']}**")
            d1, d2, d3, d4 = st.columns(4)
            d1.metric("当前总分", selected_product["total_score"] if selected_product["total_score"] is not None else "-")
            d2.metric("本任务关联 Note", int(selected_product["task_linked_notes"]))
            d3.metric("全局关联 Note", int(selected_product["global_linked_notes"]))
            d4.metric("最近评分更新", selected_product["assessment_updated_at"] or "-")
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


def _draw_pipeline_results(factory: sessionmaker[Session]) -> None:
    st.subheader("结果中心")
    st.caption("把产品总览和任务排障分开看，避免全局产品态与 task 流水相互污染。")
    tab1, tab2 = st.tabs(["产品总览", "任务结果"])
    with tab1:
        _draw_global_product_results(factory)
    with tab2:
        _draw_task_result_view(factory)


def _task_is_mutable(task: dict[str, Any]) -> bool:
    return task["status"] in {"pending", "failed"}


def _format_remaining_seconds(expires_at: str | None) -> str:
    if not expires_at:
        return "-"
    try:
        target = datetime.fromisoformat(expires_at)
    except ValueError:
        return "-"
    remaining = int((target - datetime.now()).total_seconds())
    return str(max(0, remaining))


def _resolve_result_products_top_n(total_items: int, *, desired: int = 20) -> int:
    return min(max(int(total_items), 1), max(int(desired), 1))


def _run_manual_task_pipeline(
    factory: sessionmaker[Session],
    *,
    task_id: int,
    max_notes: int,
    command_template: str,
) -> dict[str, Any]:
    result = ManualTaskPipelineService(factory).run(
        task_id=task_id,
        max_notes=max_notes,
        backend="command",
        command_template=command_template,
    )
    return asdict(result)


@st.fragment(run_every=5)
def _draw_login_qr_panel(factory: sessionmaker[Session]) -> None:
    st.subheader("Login Control")
    panel_state = _load_login_panel_state(factory)
    if not panel_state["ok"]:
        st.error("登录控制表缺失，当前数据库还没有完成统一登录 schema 初始化。")
        st.code(str(panel_state["error"]), language="text")
        st.info(str(panel_state["hint"]))
        return

    service = panel_state["service"]
    row = panel_state["row"]
    events = panel_state["events"]

    payload = {
        "platform": row.platform,
        "auth_state": row.auth_state.value,
        "flow_state": row.flow_state.value,
        "active_method": row.active_method,
        "attempt_id": int(row.attempt_id),
        "action_nonce": int(row.action_nonce),
        "handled_action_nonce": int(row.handled_action_nonce),
        "phone_number": _mask_phone_number(row.phone_number),
        "last_probe_ok": row.last_probe_ok,
        "last_probe_at": _dt(row.last_probe_at),
        "profile_dir": row.profile_dir,
        "controller_pid": row.controller_pid,
        "child_pid": row.child_pid,
        "updated_at": _dt(row.updated_at),
    }

    st.info(_login_auth_state_message(row.auth_state.value))
    st.caption(_login_flow_state_message(row.flow_state.value))

    action_cols = st.columns(5)
    with action_cols[0]:
        if st.button(
            "探测登录状态",
            key="login_probe_button",
            disabled=not _login_action_enabled(row.flow_state.value, "request_probe"),
        ):
            try:
                service.request_probe()
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))
    with action_cols[1]:
        if st.button(
            "开始二维码登录",
            type="primary",
            key="login_start_qr_button",
            disabled=not _login_action_enabled(row.flow_state.value, "start_qr"),
        ):
            try:
                service.start_qr_login()
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))
    with action_cols[2]:
        phone_number_value = st.text_input(
            "手机号",
            value=row.phone_number or "",
            key="login_phone_number_input",
        )
    with action_cols[3]:
        if st.button(
            "开始手机号登录",
            key="login_start_phone_button",
            disabled=not _login_action_enabled(row.flow_state.value, "start_phone"),
        ):
            try:
                service.start_phone_login(phone_number_value)
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))
    with action_cols[4]:
        if st.button(
            "取消当前尝试",
            key="login_cancel_button",
            disabled=not _login_action_enabled(row.flow_state.value, "cancel"),
        ):
            try:
                service.cancel_current_attempt()
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))

    sms_cols = st.columns([3, 1, 1])
    with sms_cols[0]:
        sms_code_value = st.text_input(
            "短信验证码",
            value="",
            max_chars=6,
            key="login_phone_sms_code_input",
        )
    with sms_cols[1]:
        if st.button(
            "提交验证码",
            key="submit_login_phone_code_button",
            disabled=not _login_action_enabled(row.flow_state.value, "submit_sms_code"),
        ):
            try:
                service.submit_phone_code(sms_code_value)
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))
    with sms_cols[2]:
        if st.button(
            "继续探测",
            key="login_continue_probe_button",
            disabled=not _login_action_enabled(row.flow_state.value, "continue_probe"),
        ):
            try:
                service.request_probe()
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))

    left, right = st.columns([3, 2])
    with left:
        qr_image_path = Path(row.qr_image_path).expanduser() if row.qr_image_path else None
        security_image_path = Path(row.security_image_path).expanduser() if row.security_image_path else None
        if qr_image_path is not None and qr_image_path.exists():
            st.image(str(qr_image_path), caption="Login QR", use_container_width=True)
            st.caption(str(qr_image_path))
        elif row.flow_state.value == "waiting_qr_scan":
            st.info("二维码已生成，等待扫码。")
        if security_image_path is not None and security_image_path.exists():
            st.image(str(security_image_path), caption="Security Verification", use_container_width=True)
            st.caption(str(security_image_path))
    with right:
        st.json(payload)
        if row.last_error:
            st.caption("Last Error")
            st.code(row.last_error, language="text")

    st.divider()
    st.caption("Recent Login Events")
    st.dataframe(events, use_container_width=True, hide_index=True)


def _draw_task_management(factory: sessionmaker[Session]) -> None:
    st.subheader("Tasks")
    with st.form("task_create_form"):
        create_keywords = st.text_input("Keywords", value="")
        create_platform = st.text_input("Platform", value="xhs")
        run_immediately = st.checkbox("Run Immediately", value=False)
        create_max_notes = st.number_input("Max Notes", min_value=1, value=20, step=1)
        create_command_template = st.text_input("Command Template", value=settings.crawl_command_template)
        create_task = st.form_submit_button("Create Task")
    if create_task:
        try:
            with factory() as session:
                task = CrawlTaskService(session).create_task(create_keywords, platform=create_platform)
                if run_immediately:
                    if not create_command_template.strip():
                        raise ValueError("command_template is required when Run Immediately is enabled")
                    payload = _run_manual_task_pipeline(
                        factory,
                        task_id=task.id,
                        max_notes=int(create_max_notes),
                        command_template=create_command_template,
                    )
                    st.success(f"task_created_and_run_with_opportunity id={task.id}")
                    st.json(payload)
                else:
                    st.success(f"task_created id={task.id} status={task.status.value}")
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))

    st.divider()
    st.caption("Task Filters")
    f1, f2, f3 = st.columns([2, 1, 2])
    with f1:
        status_filter = st.multiselect(
            "Statuses",
            options=["pending", "running", "failed", "done"],
            default=[],
            key="task_status_filter",
        )
    with f2:
        platform_filter = st.text_input("Platform Filter", value="", key="task_platform_filter")
    with f3:
        keyword_filter = st.text_input("Keyword Filter", value="", key="task_keyword_filter")

    task_rows = _fetch_tasks(
        factory,
        limit=200,
        statuses=status_filter or None,
        platform=platform_filter.strip() or None,
        keywords_query=keyword_filter.strip() or None,
    )
    st.dataframe(task_rows, width="stretch", hide_index=True)

    if not task_rows:
        st.info("暂无符合条件的任务。")
        return

    task_by_id = {int(row["id"]): row for row in task_rows}
    selected_task_id = int(
        st.selectbox(
            "Select Task",
            options=list(task_by_id.keys()),
            format_func=lambda task_id: _task_label(task_by_id[int(task_id)]),
            key="task_management_selector",
        )
    )
    selected_task = task_by_id[selected_task_id]
    st.caption("Selected Task")
    st.json(selected_task)

    if selected_task["error_message"]:
        st.caption("Latest Error")
        st.code(selected_task["error_message"], language="text")

    c1, c2 = st.columns(2)
    if _task_is_mutable(selected_task):
        with c1:
            with st.form("edit_task_form"):
                edit_keywords = st.text_input("Edit Keywords", value=selected_task["keywords"])
                edit_platform = st.text_input("Edit Platform", value=selected_task["platform"])
                save_task = st.form_submit_button("Save Task")
            if save_task:
                try:
                    with factory() as session:
                        row = CrawlTaskService(session).update_task(
                            selected_task_id,
                            keywords=edit_keywords,
                            platform=edit_platform,
                        )
                    st.success(f"task_updated id={row.id} status={row.status.value}")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(str(exc))

            with st.form("delete_task_form"):
                confirm_delete_task = st.checkbox("Confirm delete task", value=False)
                delete_task = st.form_submit_button("Delete Task")
            if delete_task:
                try:
                    if not confirm_delete_task:
                        raise ValueError("confirm delete task first")
                    with factory() as session:
                        CrawlTaskService(session).delete_task(selected_task_id)
                    st.success(f"task_deleted id={selected_task_id}")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(str(exc))

        with c2:
            with st.form("run_existing_task_form"):
                run_max_notes = st.number_input("Run Max Notes", min_value=1, value=20, step=1)
                run_command_template = st.text_input(
                    "Run Command Template",
                    value=settings.crawl_command_template,
                )
                run_selected_task = st.form_submit_button("Run Selected Task")
            if run_selected_task:
                try:
                    if not run_command_template.strip():
                        raise ValueError("command_template is required")
                    payload = _run_manual_task_pipeline(
                        factory,
                        task_id=selected_task_id,
                        max_notes=int(run_max_notes),
                        command_template=run_command_template,
                    )
                    st.success(f"task_run_completed_with_opportunity id={selected_task_id}")
                    st.json(payload)
                except Exception as exc:  # noqa: BLE001
                    st.error(str(exc))
    else:
        with c1:
            st.info("running / done 任务只读保留，不支持编辑或删除。")
        with c2:
            if selected_task["status"] == "done":
                st.info("done 任务请在 Results 查看后续结果。")
            else:
                st.info("running 任务执行中，请稍后刷新。")


def _draw_schedule_management(factory: sessionmaker[Session]) -> None:
    st.subheader("Schedules")
    scheduler_rows = _fetch_scheduler_configs(factory)
    st.caption("Discover scheduler is the single runtime entrypoint and now runs opportunity processing in the same loop.")
    st.dataframe(scheduler_rows, width="stretch", hide_index=True)

    if not scheduler_rows:
        st.info("当前还没有 discover 调度配置。")
        return

    scheduler_col1, scheduler_col2 = st.columns([2, 3])
    with scheduler_col1:
        supports_note_limit = _scheduler_service_supports_note_limit(SchedulerConfigService)
        selected_scheduler = scheduler_rows[0]
        with st.form("scheduler_config_form"):
            scheduler_enabled = st.checkbox("Enabled", value=bool(selected_scheduler["enabled"]))
            loop_interval_seconds = st.number_input(
                "Loop Interval Seconds",
                min_value=1,
                value=int(selected_scheduler["loop_interval_seconds"]),
                step=1,
            )
            discover_note_limit = st.number_input(
                "Discover Note Limit",
                min_value=1,
                value=int(selected_scheduler["note_limit"] or settings.sched_discover_note_limit),
                step=1,
                disabled=not supports_note_limit,
            )
            save_scheduler = st.form_submit_button("Save Scheduler Config")
        if save_scheduler:
            try:
                if not supports_note_limit:
                    raise ValueError(
                        "Current Streamlit process loaded legacy scheduler code. Restart the UI server, then save Discover Note Limit again."
                    )
                row = SchedulerConfigService(factory).set_config(
                    "discover",
                    enabled=bool(scheduler_enabled),
                    loop_interval_seconds=int(loop_interval_seconds),
                    note_limit=int(discover_note_limit),
                )
                message = (
                    f"scheduler_updated mode={row.mode} enabled={row.enabled} interval={row.loop_interval_seconds}s"
                )
                message += f" note_limit={row.note_limit}"
                st.success(message)
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))
    with scheduler_col2:
        st.info("独立 opportunity 调度已取消；discover 每轮结束后会自动执行 opportunity 和失败重试。")
        if not supports_note_limit:
            st.warning("当前进程仍在使用旧版 scheduler 模块。重启 Streamlit 后即可使用 Discover Note Limit。")

    st.divider()
    st.caption("Watchlist")
    watch_col1, watch_col2 = st.columns(2)
    with watch_col1:
        with st.form("add_watch_keyword_form"):
            watch_keyword = st.text_input("Keyword", value="")
            watch_platform = st.text_input("Platform", value="xhs")
            interval = st.number_input("Poll Interval Minutes", min_value=1, value=60, step=1)
            enabled = st.checkbox("Enabled", value=True)
            add_keyword = st.form_submit_button("Add Watch Keyword")
        if add_keyword:
            try:
                row = DiscoverService(factory, collector=None).upsert_keyword(
                    keyword=watch_keyword,
                    platform=watch_platform,
                    poll_interval_minutes=int(interval),
                    enabled=bool(enabled),
                )
                st.success(f"keyword_upserted id={row.id} keyword={row.keyword}")
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))

    with watch_col2:
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

    keyword_rows = _fetch_keywords(factory, limit=300)
    st.dataframe(keyword_rows, width="stretch", hide_index=True)
    if not keyword_rows:
        st.info("暂无 watch keyword，可先新增。")
        return

    keyword_index = {f"#{row['id']} | {row['keyword']}": row for row in keyword_rows}
    selected_keyword_label = st.selectbox(
        "Select Watch Keyword",
        options=list(keyword_index.keys()),
        key="watch_keyword_selector",
    )
    selected_keyword = keyword_index[selected_keyword_label]
    w1, w2 = st.columns(2)
    with w1:
        with st.form("edit_watch_keyword_form"):
            edit_keyword = st.text_input("Edit Keyword", value=selected_keyword["keyword"])
            edit_platform = st.text_input("Edit Platform", value=selected_keyword["platform"])
            edit_interval = st.number_input(
                "Edit Poll Interval Minutes",
                min_value=1,
                value=int(selected_keyword["poll_interval_minutes"]),
                step=1,
            )
            edit_enabled = st.checkbox("Edit Enabled", value=bool(selected_keyword["enabled"]))
            save_keyword = st.form_submit_button("Save Watch Keyword")
        if save_keyword:
            try:
                row = DiscoverService(factory, collector=None).update_keyword(
                    int(selected_keyword["id"]),
                    keyword=edit_keyword,
                    platform=edit_platform,
                    poll_interval_minutes=int(edit_interval),
                    enabled=bool(edit_enabled),
                )
                st.success(f"keyword_updated id={row.id} keyword={row.keyword}")
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))
    with w2:
        with st.form("delete_watch_keyword_form"):
            confirm_delete_keyword = st.checkbox("Confirm delete watch keyword", value=False)
            delete_keyword = st.form_submit_button("Delete Watch Keyword")
        if delete_keyword:
            try:
                if not confirm_delete_keyword:
                    raise ValueError("confirm delete watch keyword first")
                DiscoverService(factory, collector=None).delete_keyword(int(selected_keyword["id"]))
                st.success(f"keyword_deleted id={selected_keyword['id']}")
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))


def _draw_main(factory: sessionmaker[Session]) -> None:
    tasks_tab, schedules_tab, login_tab, results_tab = st.tabs(["Tasks", "Schedules", "Login", "Results"])
    with tasks_tab:
        _draw_task_management(factory)
    with schedules_tab:
        _draw_schedule_management(factory)
    with login_tab:
        _draw_login_qr_panel(factory)
    with results_tab:
        _draw_pipeline_results(factory)

def main() -> None:
    configure_logging(settings.log_level)
    _require_streamlit_run()

    st.set_page_config(page_title="rednote-spider (MVP)", layout="wide")
    log_database_target(logger, database_url=settings.database_url, source="streamlit_ui")
    st.title("rednote-spider MVP Console")

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
