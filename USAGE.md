# USAGE

## 1. 初始化

```bash
python -m pip install -e '.[dev]' --no-build-isolation
cp .env.example .env
python scripts/init_schema.py
```

## 2. 单次采集验收（command-only）

```bash
python scripts/verify_live_crawl.py \
  --keywords "通勤 焦虑" \
  --max-notes 10 \
  --command-template 'python external_crawler.py --keywords "{keywords}" --max-notes {max_notes}'
```

## 3. Discover Watchlist

```bash
python scripts/manage_discover_keywords.py add "宠物美容" --interval 60
python scripts/manage_discover_keywords.py list
python scripts/manage_discover_keywords.py disable 1
python scripts/manage_discover_keywords.py enable 1
```

```bash
python scripts/run_discover_cycle.py \
  --cycles 1 \
  --interval-seconds 0 \
  --keyword-limit 20 \
  --note-limit 20 \
  --command-template 'python external_crawler.py --keywords "{keywords}" --max-notes {max_notes}'
```

## 4. 外部爬虫适配脚本

`run_external_crawler.py` 用于把第三方输出统一成：

```json
{"notes": [...], "comments": [...]} 
```

示例：

```bash
python scripts/run_external_crawler.py --keywords "收纳" --max-notes 20 --source json-file --json-file ./payload.json
python scripts/run_external_crawler.py --keywords "收纳" --max-notes 20 --source json-dir --json-dir ./crawler_output/xhs/json
```

## 5. 产品机会评估

先配置 LLM（`.env`）：

```bash
OPPORTUNITY_LLM_PROVIDER=openai
OPPORTUNITY_LLM_API_KEY=sk-...
OPPORTUNITY_LLM_BASE_URL=https://api.openai.com/v1
OPPORTUNITY_LLM_MODEL=gpt-4.1-mini
```

```bash
# 扫描最近完成任务
python scripts/run_product_opportunity_cycle.py --limit-tasks 20

# 指定任务
python scripts/run_product_opportunity_cycle.py --task-id 123

# 自定义失败退避（分钟）
python scripts/run_product_opportunity_cycle.py \
  --limit-tasks 20 \
  --retry-backoff-base-minutes 5 \
  --retry-backoff-max-minutes 720
```

离线测试可用：

```bash
OPPORTUNITY_LLM_PROVIDER=mock
```

说明：
- 评分对象是产品，不是单条 note。
- 评分是触发式：新产品首次评分；已有关联产品仅在关联证据量达到上次定义基线 2 倍时重评，否则复用缓存评分。
- 触发重评时，评分证据会合并“历史已映射 note/comment + 本批增量 note/comment”。
- 托管 discover 调度会在每轮 discover 结束后自动执行机会评估与失败重试；独立脚本主要用于手工补跑。
- note 级失败会写入 `opportunity_note_failure`，可在 UI 的失败专区排查。
- ignored 证据会写入 `opportunity_note_ignored`，保留初筛分数、阈值和原因。

## 6. UI

开发态：

```bash
streamlit run ui/app.py
```

部署态（适合 supervisor）：

```bash
bash scripts/run_ui_server.sh
```

不要用：

```bash
python ui/app.py
```

页面内可直接配置：
- discover watch keyword
- 每个关键词的轮询分钟与启用状态
- discover scheduler 的全局启停、循环间隔与 note limit（同一轮内自动执行 opportunity）
- 统一登录控制面板（probe / 二维码 / 手机号 / 短信验证码 / 安全校验继续探测）

登录运行时说明：

```bash
# supervisor 常驻控制器
python scripts/run_login_qr_worker.py

# login-only runtime（由控制器拉起，一般不手工调用）
python scripts/run_mediacrawler_login_only.py --platform xhs --method probe --attempt-id 0
```

## 7. 回归验证

```bash
python -m compileall -q src scripts ui
PYTHONPATH=src pytest -q
PYTHONPATH=src python scripts/ci_migration_gate.py
```

## 8. 定时调度（单一 discover 编排）

```bash
# 单轮
bash scripts/run_scheduled_cycle.sh discover

# 常驻循环
bash scripts/run_scheduled_loop.sh discover
```

`cron/supervisor/systemd` 模板见：
- `SCHEDULER_GUIDE.md`
- `deploy/supervisor/supervisor-ui.conf`
