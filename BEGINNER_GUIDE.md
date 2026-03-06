# BEGINNER GUIDE

## 1. 安装与初始化

```bash
python -m pip install -e '.[dev]' --no-build-isolation
cp .env.example .env
python scripts/init_schema.py
```

## 2. 启动 UI

```bash
streamlit run ui/app.py
```

不要用 `python ui/app.py`，否则会出现 `missing ScriptRunContext` 告警。

## 3. 跑一条最小采集链路（command-only）

```bash
python scripts/verify_live_crawl.py \
  --keywords "通勤 焦虑" \
  --max-notes 3 \
  --command-template 'python external_crawler.py --keywords "{keywords}" --max-notes {max_notes}'
```

## 4. 开启关键词轮询（discover）

```bash
python scripts/manage_discover_keywords.py add "宠物美容" --interval 60
python scripts/run_discover_cycle.py --cycles 1 --interval-seconds 0 --keyword-limit 20 --note-limit 20 --command-template 'python external_crawler.py --keywords "{keywords}" --max-notes {max_notes}'
```

## 5. 跑产品机会评估（可选）

先在 `.env` 配置：

```bash
OPPORTUNITY_LLM_PROVIDER=openai
OPPORTUNITY_LLM_API_KEY=sk-...
```

```bash
python scripts/run_product_opportunity_cycle.py --limit-tasks 20
```

说明：抓取入库与机会评估是两阶段，机会评估通过该脚本异步执行。

## 6. 自检

```bash
python -m compileall -q src scripts ui
PYTHONPATH=src pytest -q
```

## 7. 定时运行（可选）

```bash
bash scripts/run_scheduled_cycle.sh discover
bash scripts/run_scheduled_cycle.sh opportunity
```

`cron/supervisor/systemd` 模板见 `SCHEDULER_GUIDE.md`。
