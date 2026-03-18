# rednote-spider

极简版小红书定时采集 MVP（command-only）。

主链路：
`关键词任务 -> command 采集 -> 原始入库(note/comment) -> discover 定时轮询`

机会发现链路：
`raw_note/raw_comment -> LLM 初筛 -> LLM 匹配已有产品或新建产品 -> 产品级 LLM 多维打分`

登录链路：
`Streamlit Login Control -> unified login controller -> login-only MediaCrawler runtime -> persistent browser profile + pong()`

## Quick Start

```bash
python -m pip install -e '.[dev]' --no-build-isolation
cp .env.example .env
python scripts/init_schema.py
bash scripts/run_ui_server.sh
```

不要用 `python ui/app.py` 直接启动。开发时可用 `streamlit run ui/app.py`，部署时建议用 `bash scripts/run_ui_server.sh` 配合 supervisor。

## 配置（.env）

```bash
DATABASE_URL=sqlite:///./rednote.db
APP_ENV=dev
STREAMLIT_ACCESS_TOKEN=
STREAMLIT_SERVER_ADDRESS=127.0.0.1
STREAMLIT_SERVER_PORT=8501
STREAMLIT_SERVER_HEADLESS=true
CRAWL_BACKEND=command
CRAWL_COMMAND_TEMPLATE=python external_crawler.py --keywords "{keywords}" --max-notes {max_notes}
CRAWL_COMMAND_TIMEOUT_SECONDS=600
OPPORTUNITY_LLM_PROVIDER=openai
OPPORTUNITY_LLM_API_KEY=sk-...
OPPORTUNITY_LLM_BASE_URL=https://api.openai.com/v1
OPPORTUNITY_LLM_MODEL=gpt-4.1-mini
OPPORTUNITY_LLM_TIMEOUT_SECONDS=600
OPPORTUNITY_LLM_TEMPERATURE=0.1
SCHED_DISCOVER_LOOP_INTERVAL_SECONDS=900
LOG_LEVEL=INFO
```

## 核心边界

- backend 只支持 `command`
- discover collector 只支持 `command`
- command 输出协议（one-off crawl + discover）必须是对象：`{"notes":[...], "comments":[...]}`
  - `notes[*].note_id` 必填
  - `comments[*].comment_id` 必填
- 数据库核心表：
  - `crawl_task`
  - `raw_note`
  - `crawl_task_note`
  - `raw_comment`
- `discover_watch_keyword`
- `scheduler_runtime_config`
- `product`
- `product_assessment`
- `product_opportunity`
- `opportunity_note_failure`
- `opportunity_note_ignored`

## 核心脚本（12 个）

- `scripts/start_app.sh`
- `scripts/init_schema.py`
- `scripts/run_ui_server.sh`
- `scripts/ci_migration_gate.py`
- `scripts/run_external_crawler.py`
- `scripts/verify_live_crawl.py`
- `scripts/manage_discover_keywords.py`
- `scripts/run_discover_cycle.py`
- `scripts/run_product_opportunity_cycle.py`
- `scripts/run_scheduled_cycle.sh`
- `scripts/run_scheduled_loop.sh`
- `scripts/run_managed_scheduler.py`

## Discover（定时采集）

```bash
python scripts/manage_discover_keywords.py add "宠物美容" --interval 60
python scripts/run_discover_cycle.py \
  --cycles 1 \
  --keyword-limit 20 \
  --note-limit 20 \
  --command-template 'python external_crawler.py --keywords "{keywords}" --max-notes {max_notes}'
```

页面里的 `Discover Scheduler` 可配置单一 `discover` 调度的 `enabled`、`loop interval seconds` 与 `note limit`；该调度会在每轮 discover 完成后自动执行 opportunity 与失败重试。`Discover Watchlist` 可新增关键词并调整每个关键词的轮询分钟与启用状态。

## 登录控制

UI 的 `Login` 标签页已经切到统一登录控制面板：
- `auth_state` 只反映最新 `pong()` 探测结果
- `flow_state` 只反映当前二维码/手机号/安全校验流程
- 手机号登录与二维码登录共享同一个 MediaCrawler profile
- 安全校验会保留当前浏览器上下文，支持人工接管后再继续探测

关键环境变量：

```bash
LOGIN_RUNTIME_PYTHON=/root/MediaCrawler/.venv/bin/python
LOGIN_RUNTIME_CRAWLER_CWD=../MediaCrawler
LOGIN_CONTROLLER_POLL_SECONDS=2
```

## 产品机会评估

```bash
python scripts/run_product_opportunity_cycle.py --limit-tasks 20
# 或指定任务
python scripts/run_product_opportunity_cycle.py --task-id 123
```

说明：
- 默认使用 `OPPORTUNITY_LLM_PROVIDER=openai`。
- 本地离线调试/测试可用 `OPPORTUNITY_LLM_PROVIDER=mock`。
- 产品评分是触发式：新产品首次评分，已有关联产品仅在证据量达到上次基线 2 倍时重评；其余轮次复用缓存评分。
- crawl/discover 在“抓取+入库”完成后即标记任务 `done`；托管 discover 调度会在同一轮内继续执行机会评估与失败重试。
- `done` 任务重复执行机会评估时，默认只重试失败 note，不重复扫描已映射/已忽略 note。
- 失败重试带指数退避：`--retry-backoff-base-minutes` 与 `--retry-backoff-max-minutes`。
- `ignored` 会持久化到 `opportunity_note_ignored`，保留初筛分数与原因证据。

详细规则与提示词模板见：
- `PRODUCT_OPPORTUNITY_GUIDE.md`

## 验证

```bash
python -m compileall -q src scripts ui
PYTHONPATH=src pytest -q
PYTHONPATH=src python scripts/ci_migration_gate.py
```

## 定时调度模板

已提供三套模板：
- `cron`
- `supervisor`
- `systemd`（Linux）

详见：
- `SCHEDULER_GUIDE.md`
