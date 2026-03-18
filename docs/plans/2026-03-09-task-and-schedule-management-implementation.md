# Task And Schedule Management Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 给现有 UI 和服务层补齐 task 安全版 CRUD，以及两层 schedule 管理。

**Architecture:** 以现有 SQLAlchemy service 为核心扩展最小能力；Streamlit 页面按 `Tasks / Schedules / Results` 整理。保持后台 discover/opportunity 调度模型不变，仅补管理界面与服务方法。

**Tech Stack:** Python 3.11, SQLAlchemy 2.x, Streamlit, pytest

---

### Task 1: Add service-layer failing tests
- `tests/test_crawl_task_service.py`
- `tests/test_discover_service.py`
- 覆盖 task list/update/delete 与 watch keyword delete 的红绿流程。

### Task 2: Implement service-layer CRUD
- `src/rednote_spider/services/crawl_task_service.py`
- `src/rednote_spider/services/discover_service.py`
- 仅为允许状态开放编辑删除。

### Task 3: Add UI management flows
- `ui/app.py`
- 重组为 `Tasks`、`Schedules`、`Results`。
- 任务区支持筛选、新增、编辑、删除、Run Now。
- schedule 区支持 global scheduler 修改和 watchlist CRUD。

### Task 4: Validate and restart deployed UI
- 运行 targeted tests、compileall。
- 重启 `rednote-ui` 并验证页面可访问。
