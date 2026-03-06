# rednote-spider

极简版小红书定时采集 MVP（command-only）。

主链路：
`关键词任务 -> command 采集 -> 原始入库(note/comment) -> discover 定时轮询`

机会发现链路：
`raw_note/raw_comment -> LLM 初筛 -> LLM 匹配已有产品或新建产品 -> 产品级 LLM 多维打分`

## Quick Start

```bash
python -m pip install -e '.[dev]' --no-build-isolation
cp .env.example .env
python scripts/init_schema.py
streamlit run ui/app.py
```

不要用 `python ui/app.py` 直接启动，Streamlit 必须通过 `streamlit run` 启动。

## 配置（.env）

```bash
DATABASE_URL=sqlite:///./rednote.db
APP_ENV=dev
STREAMLIT_ACCESS_TOKEN=
CRAWL_BACKEND=command
CRAWL_COMMAND_TEMPLATE=python external_crawler.py --keywords "{keywords}" --max-notes {max_notes}
CRAWL_COMMAND_TIMEOUT_SECONDS=600
OPPORTUNITY_LLM_PROVIDER=openai
OPPORTUNITY_LLM_API_KEY=sk-...
OPPORTUNITY_LLM_BASE_URL=https://api.openai.com/v1
OPPORTUNITY_LLM_MODEL=gpt-4.1-mini
OPPORTUNITY_LLM_TIMEOUT_SECONDS=600
OPPORTUNITY_LLM_TEMPERATURE=0.1
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
- `product`
- `product_assessment`
- `product_opportunity`
- `opportunity_note_failure`
- `opportunity_note_ignored`

## 核心脚本（10 个）

- `scripts/start_app.sh`
- `scripts/init_schema.py`
- `scripts/ci_migration_gate.py`
- `scripts/run_external_crawler.py`
- `scripts/verify_live_crawl.py`
- `scripts/manage_discover_keywords.py`
- `scripts/run_discover_cycle.py`
- `scripts/run_product_opportunity_cycle.py`
- `scripts/run_scheduled_cycle.sh`
- `scripts/run_scheduled_loop.sh`

## Discover（定时采集）

```bash
python scripts/manage_discover_keywords.py add "宠物美容" --interval 60
python scripts/run_discover_cycle.py \
  --cycles 1 \
  --keyword-limit 20 \
  --note-limit 20 \
  --command-template 'python external_crawler.py --keywords "{keywords}" --max-notes {max_notes}'
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
- crawl/discover 在“抓取+入库”完成后即标记任务 `done`；机会评估通过独立脚本异步执行。
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
