# Task And Schedule Management Design

**Goal:** 在现有 Streamlit 控制台内补全任务管理与两层 schedule 管理能力，形成安全版 CRUD。

## Scope
- `task`：支持列表筛选、详情查看、新增、编辑、删除、手动运行。
- `schedule-global`：仅管理固定两条 `discover` / `opportunity` 调度，支持查询与修改。
- `schedule-watchlist`：管理关键词 watchlist，支持完整增删改查。
- `done` 任务只读保留，不支持编辑和删除。

## Rules
- `pending/failed`：允许编辑、删除、运行。
- `running/done`：只读。
- `discover/opportunity` 全局调度：不支持新增删除，只支持修改 `enabled` 与 `loop_interval_seconds`。
- `watch keyword`：允许新增、编辑、删除。

## UI Layout
- `Tasks`：创建任务、筛选列表、编辑/删除/运行选中任务。
- `Schedules`：
  - `Global Scheduler` 固定两行配置
  - `Watchlist` 列表 + 新增/编辑/删除
- `Results`：保留现有后续流程结果中心。

## Service Changes
- `CrawlTaskService` 增加：`list_tasks`、`update_task`、`delete_task`。
- `DiscoverService` 增加：`delete_keyword`。
- `SchedulerConfigService` 保持两条固定配置行。

## Safety
- 不对 `done` 做物理删除。
- 任务删除仅限无只读约束状态；关联结果不做级联破坏。
- 页面动作用明确提示显示成功/失败结果。
