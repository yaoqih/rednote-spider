# SCRIPTS GUIDE

当前 `scripts/` 目录保留 11 个脚本。

## 1. 总览

| 脚本 | 作用 |
| --- | --- |
| `start_app.sh` | 安装依赖 + 初始化 schema + 启动 UI |
| `init_schema.py` | 初始化数据库并做 core table/column 校验 |
| `ci_migration_gate.py` | schema + 核心测试 gate |
| `run_external_crawler.py` | 第三方爬虫输出标准化 |
| `verify_live_crawl.py` | 单次 command 采集验收 |
| `manage_discover_keywords.py` | watch keyword 管理 |
| `run_discover_cycle.py` | discover 周期采集 |
| `run_product_opportunity_cycle.py` | LLM 产品机会评估（初筛 + 匹配/新建 + 多维评分） |
| `run_scheduled_cycle.sh` | 调度入口：单轮执行 discover/opportunity/all |
| `run_scheduled_loop.sh` | 调度入口：常驻循环执行 discover/opportunity/all |
| `send_login_expiry_alert.py` | 识别登录失效日志并发送邮件告警（loop 失败分支调用） |

## 2. start_app.sh

```bash
bash scripts/start_app.sh
bash scripts/start_app.sh --no-install
```

## 3. init_schema.py

```bash
python scripts/init_schema.py
python scripts/init_schema.py --database-url sqlite:///./rednote.db
```

升级后建议先执行一次，确保 `opportunity_note_failure` 等新表存在。

## 4. ci_migration_gate.py

```bash
PYTHONPATH=src python scripts/ci_migration_gate.py
```

## 5. run_external_crawler.py

标准输出：

```json
{"notes": [...], "comments": [...]} 
```

支持输入源：
- `json-file`
- `json-dir`
- 可选先跑 `--crawler-cmd`

## 6. verify_live_crawl.py（command-only）

```bash
python scripts/verify_live_crawl.py \
  --keywords "通勤 焦虑" \
  --max-notes 10 \
  --command-template 'python external_crawler.py --keywords "{keywords}" --max-notes {max_notes}'
```

## 7. manage_discover_keywords.py

```bash
python scripts/manage_discover_keywords.py add "宠物美容" --interval 60
python scripts/manage_discover_keywords.py list
python scripts/manage_discover_keywords.py disable 1
python scripts/manage_discover_keywords.py enable 1
```

## 8. run_discover_cycle.py（command-only）

```bash
python scripts/run_discover_cycle.py \
  --cycles 1 \
  --interval-seconds 0 \
  --keyword-limit 20 \
  --note-limit 20 \
  --command-template 'python external_crawler.py --keywords "{keywords}" --max-notes {max_notes}'
```

command 输出协议（严格）：
- 顶层必须是对象：`{"notes": [...], "comments": [...]}`。
- `notes[*].note_id` 必填。
- `comments[*].comment_id` 必填。
- 该协议同时用于 `verify_live_crawl.py` 和 `run_discover_cycle.py`。

## 9. run_product_opportunity_cycle.py

先配置：

```bash
export OPPORTUNITY_LLM_PROVIDER=openai
export OPPORTUNITY_LLM_API_KEY=sk-...
```

```bash
# 扫描最近完成任务
python scripts/run_product_opportunity_cycle.py --limit-tasks 20

# 指定 task_id
python scripts/run_product_opportunity_cycle.py --task-id 123

# 调整失败重试退避
python scripts/run_product_opportunity_cycle.py \
  --limit-tasks 20 \
  --retry-backoff-base-minutes 5 \
  --retry-backoff-max-minutes 720
```

可选参数：
- `--prescreen-threshold`（默认 3.2）
- `--match-threshold`（默认 0.26）
- `--retry-backoff-base-minutes`（默认 5）
- `--retry-backoff-max-minutes`（默认 720）

说明：
- crawl/discover 只做抓取入库并标记 task `done`。
- 产品机会评估由该脚本异步执行。

## 10. run_scheduled_cycle.sh

```bash
bash scripts/run_scheduled_cycle.sh discover
bash scripts/run_scheduled_cycle.sh opportunity
bash scripts/run_scheduled_cycle.sh all
```

## 11. run_scheduled_loop.sh

```bash
bash scripts/run_scheduled_loop.sh discover
bash scripts/run_scheduled_loop.sh opportunity
```

调度模板（cron/supervisor/systemd）见：
- `SCHEDULER_GUIDE.md`
