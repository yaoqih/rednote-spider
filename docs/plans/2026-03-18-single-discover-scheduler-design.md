# Single Discover Scheduler Design

**Date:** 2026-03-18

## Background

当前系统存在两条调度线：

- `discover` 调度：拉 watch keyword，生成并完成 `crawl_task`
- `opportunity` 调度：扫描最近 `done` 任务，执行 note->product 映射、产品评分、失败重试

这让调度层比产品需求更复杂。手动任务已经证明 `crawl + opportunity` 串联是合理的，因此独立的 `opportunity` 常驻调度没有必要继续保留。

## Goal

统一为一个 `discover` 调度入口：

- 每轮 discover 结束后，立即执行 opportunity 处理
- 保留既有 failure backoff / retry 语义
- 移除独立 `opportunity` scheduler 的配置暴露、UI 暴露和部署暴露
- 继续保留手工补跑 `run_product_opportunity_cycle.py`

## Non-goals

- 不删除 `ProductOpportunityService.process_recent_done_tasks`
- 不删除 `run_product_opportunity_cycle.py`
- 不改动产品评分与 note 决策逻辑

## Selected Design

采用“discover orchestrates opportunity”方案：

1. `run_managed_scheduler.py --mode discover`
   - 跑 discover 一轮
   - 随后立刻跑一次 opportunity sweep
2. `SchedulerConfigService`
   - 只对外支持 `discover`
   - DB 中已有 `opportunity` 行允许残留，但不再作为 UI/脚本可配置 mode
3. Streamlit Schedules UI
   - 只展示和编辑 `discover`
   - 文案明确：discover 调度内部已包含 opportunity 阶段
4. Supervisor/runtime 配置
   - 去掉独立 opportunity 进程配置

## Why This Design

优点：

- 运维面更简单，只有一个常驻调度器
- discover 产出的 task 能在同一轮内尽快进入产品结果
- 失败重试仍然可复用 `process_recent_done_tasks(...)`，不丢恢复能力

代价：

- discover 每轮耗时会变长
- 若 LLM 阶段抖动，discover 调度日志会混入 opportunity 阶段日志

这个代价可接受，因为当前系统规模较小，简化运维比拆分调度更重要。

## Data and Runtime Impact

- 不需要 schema 变更
- `scheduler_runtime_config` 只继续使用 `discover`
- 历史 `opportunity` 配置行不强删，避免破坏现有数据库

## Acceptance Criteria

- `run_managed_scheduler.py` 不再接受 `--mode opportunity`
- `discover` 模式返回的 summary 同时包含 discover 和 opportunity 结果
- UI 的 Schedules 页不再暴露 `opportunity` 配置
- 失败 note 的 backoff 重试仍会在 discover 调度内发生
- 全量测试通过
