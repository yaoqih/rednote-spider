# DEVELOPER GUIDE

## 1. 当前架构（极简）

只保留三层：
- task orchestration：`CrawlTaskService`, `KeywordCrawlService`, `DiscoverService`
- ingest：`RawIngestService`
- adapter：`run_external_crawler.py` + `CommandKeywordCollector`
- opportunity：`ProductOpportunityService`

不再存在：
- retry worker/scheduler
- runtime metrics
- discover seed collector

## 2. 目录

```text
src/rednote_spider/
  command_template_runner.py
  config.py
  database.py
  discover_collectors.py
  models.py
  observability.py
  ui_security.py
  services/
    crawl_task_service.py
    discover_service.py
    keyword_crawl_service.py
    product_opportunity_service.py
    raw_ingest_service.py

scripts/
  ci_migration_gate.py
  init_schema.py
  manage_discover_keywords.py
  run_discover_cycle.py
  run_product_opportunity_cycle.py
  run_scheduled_cycle.sh
  run_scheduled_loop.sh
  run_external_crawler.py
  verify_live_crawl.py
  start_app.sh
```

## 3. 数据库模型

核心表：
- `crawl_task`
- `raw_note`
- `crawl_task_note`
- `raw_comment`
- `discover_watch_keyword`
- `product`
- `product_assessment`
- `product_opportunity`
- `opportunity_note_failure`
- `opportunity_note_ignored`

初始化入口：`python scripts/init_schema.py`

## 4. 核心流程

### 4.1 单次任务
1. `CrawlTaskService.create_task`
2. `KeywordCrawlService.run_task`
3. `RawIngestService.ingest_notes`
4. `RawIngestService.ingest_comments_by_note`
5. 成功后 `CrawlTaskService.complete_task`，失败则 `CrawlTaskService.fail_task`
6. 机会评估通过独立脚本异步执行：`run_product_opportunity_cycle.py`

### 4.2 discover 周期
1. `DiscoverService._list_due_keyword_ids`
2. 每个关键词创建 crawl task
3. collector 拉取 `notes + comments_by_note`
4. `RawIngestService.ingest_notes`
5. `RawIngestService.ingest_comments_by_note`
6. `CrawlTaskService.complete_task`
7. 成功后更新 `discover_watch_keyword.last_polled_at`
8. 机会评估由异步脚本批量执行

### 4.3 产品机会评估
1. `ProductOpportunityService.process_task/process_recent_done_tasks`
2. 通过 `crawl_task_note(task_id, note_id)` 读取任务范围内 note（不要用 `raw_note.task_id` 直接筛）
3. 调用 `OpportunityLLM`（默认 `openai`）做初筛、匹配/新建、评分
4. `matched` 关联已有 `product`；`created` 自动写入新产品简述/完整描述
5. 产品评分输入使用“历史已映射 note/comment + 本批增量 note/comment”
6. 产品级评分结果落库到 `product_assessment`，`product_opportunity` 仅保存 note->product 映射及评分快照
7. note 级异常写入 `opportunity_note_failure`，用于 UI 失败专区
8. `ignored` 结果写入 `opportunity_note_ignored`（`task_id/note_id/prescreen_score/reason/threshold`）
9. `process_recent_done_tasks` 对失败任务按指数退避调度，避免高频重试

## 5. 设计约束

- 直接迁移，不做向后兼容。
- 入库失败直接抛错并标记任务失败，不做重试排队。
- 同一个 `note_id` 允许被多个任务关联（通过 `crawl_task_note` 保留任务血缘）。
- command 输出协议（严格）：
  - 顶层必须是对象：`{"notes": [...], "comments": [...]}`
  - `notes[*].note_id` 必填
  - `comments[*].comment_id` 必填

## 6. 测试

全量：

```bash
PYTHONPATH=src pytest -q
```

CI 门禁：

```bash
PYTHONPATH=src python scripts/ci_migration_gate.py
```

## 7. 开发改动建议

- 改模型后必须同步 `CORE_TABLES` 和 `init_schema.py` 验证逻辑。
- 改脚本参数后同步 `README.md` / `USAGE.md` / `SCRIPTS_GUIDE.md`。
- discover 仅支持 command collector，新增 collector 时需重新定义 MVP 边界。
- 定时调度模板统一维护在 `SCHEDULER_GUIDE.md` 与 `deploy/scheduler/`。
