# SCHEDULER GUIDE

目标：只保留一个 discover 调度入口，由它在每轮内串联「discover 抓取入库」和「opportunity 评估/失败重试」，并支持通过 UI 动态调整 discover 的启停与循环间隔。

## 1. 通用入口脚本

- 单轮执行：`bash scripts/run_scheduled_cycle.sh [discover|all]`
- 常驻循环：`bash scripts/run_scheduled_loop.sh [discover|all]`

脚本内部不会 `source .env`；配置由 Python 侧 `Settings` 读取 `.env`。discover 常驻循环每轮会读取数据库里的 `scheduler_runtime_config`，所以页面改动会在下一轮生效；同时加互斥锁防止重入（`/tmp/rednote-discover.lock`）。

## 2. 可调环境变量

discover：
- `SCHED_DISCOVER_COMMAND_TEMPLATE`（默认回退 `CRAWL_COMMAND_TEMPLATE`）
- `SCHED_DISCOVER_CYCLES`（默认 `1`）
- `SCHED_DISCOVER_INTERVAL_SECONDS`（默认 `0`）
- `SCHED_DISCOVER_KEYWORD_LIMIT`（默认 `20`）
- `SCHED_DISCOVER_NOTE_LIMIT`（默认 `20`）

discover 内嵌 opportunity sweep：
- `SCHED_OPPORTUNITY_TASK_ID`（默认 `0`，大于 0 时只跑单任务）
- `SCHED_OPPORTUNITY_LIMIT_TASKS`（默认 `20`）
- `SCHED_OPPORTUNITY_PRESCREEN_THRESHOLD`（默认 `3.2`）
- `SCHED_OPPORTUNITY_MATCH_THRESHOLD`（默认 `0.26`）
- `SCHED_OPPORTUNITY_RETRY_BACKOFF_BASE_MINUTES`（默认 `5`）
- `SCHED_OPPORTUNITY_RETRY_BACKOFF_MAX_MINUTES`（默认 `720`）

loop：
- `SCHED_DISCOVER_LOOP_INTERVAL_SECONDS`（默认 `900`，仅首次建默认配置时使用，后续可在 UI 覆盖）
- `SCHED_LOGIN_ALERT_ENABLED`（默认 `true`）
- `SCHED_LOGIN_ALERT_FROM_EMAIL`（QQ 发件邮箱）
- `SCHED_LOGIN_ALERT_TO_EMAIL`（收件邮箱，默认同发件）
- `SCHED_LOGIN_ALERT_PASSWORD`（QQ SMTP 授权码）
- `SCHED_LOGIN_ALERT_SMTP_HOST`（默认 `smtp.qq.com`）
- `SCHED_LOGIN_ALERT_SMTP_PORT`（默认 `465`）
- `SCHED_LOGIN_ALERT_COOLDOWN_SECONDS`（默认 `21600`，避免重复轰炸）

## 3. cron 模板

模板文件：`deploy/scheduler/cron.example`

步骤：
1. `mkdir -p logs`
2. 把 `__PROJECT_DIR__` 替换成你的绝对路径
3. 安装：`crontab deploy/scheduler/cron.example`
4. 查看：`crontab -l`

### 当前项目路径直接可用（步骤 1）

```bash
cd /Users/huyaoqi/Documents/rednote—spider
mkdir -p logs

sed 's|__PROJECT_DIR__|/Users/huyaoqi/Documents/rednote—spider|g' \
  deploy/scheduler/cron.example > /tmp/rednote-cron.installed

crontab /tmp/rednote-cron.installed
crontab -l
```

## 4. Supervisor 模板

模板文件：
- `deploy/scheduler/supervisor-discover.conf`
- `deploy/supervisor/supervisor-ui.conf`

步骤：
1. `mkdir -p logs`
2. 把模板里的 `__PROJECT_DIR__` 替换成你的绝对路径
3. 拷贝到 supervisor 配置目录（例如 `/etc/supervisor/conf.d/`）
4. 执行：
   - `supervisorctl reread`
   - `supervisorctl update`
   - `supervisorctl status`

## 5. UI Server（Supervisor）

推荐把 UI 也交给 supervisor 托管：

- 模板文件：`deploy/supervisor/supervisor-ui.conf`
- 启动脚本：`scripts/run_ui_server.sh`
- 默认监听：`127.0.0.1:8501`

建议：
1. 先配置 `STREAMLIT_ACCESS_TOKEN`
2. 绑定到 `127.0.0.1`
3. 外层再挂 Nginx/Caddy 做鉴权与 HTTPS
4. 页面里的 `Discover Scheduler` 控 discover 的启停、循环间隔和 note limit；同一轮内自动执行 opportunity

## 6. systemd 模板（Linux）

模板文件：
- `deploy/scheduler/systemd/rednote-discover.service`
- `deploy/scheduler/systemd/rednote-discover.timer`

步骤：
1. 把 `__PROJECT_DIR__` 和 `__RUN_USER__` 替换成真实值
2. 复制到 `/etc/systemd/system/`
3. 执行：
   - `sudo systemctl daemon-reload`
   - `sudo systemctl enable --now rednote-discover.timer`
4. 查看：
   - `systemctl list-timers | rg rednote`
   - `journalctl -u rednote-discover.service -n 100 --no-pager`

### 当前项目路径直接可用（步骤 2）

```bash
cd /Users/huyaoqi/Documents/rednote—spider
mkdir -p logs

for f in \
  deploy/scheduler/systemd/rednote-discover.service \
  deploy/scheduler/systemd/rednote-discover.timer
do
  sed -e 's|__PROJECT_DIR__|/Users/huyaoqi/Documents/rednote—spider|g' \
      -e "s|__RUN_USER__|$(whoami)|g" "$f" | \
    sudo tee "/etc/systemd/system/$(basename "$f")" >/dev/null
done

sudo systemctl daemon-reload
sudo systemctl enable --now rednote-discover.timer

systemctl list-timers | rg rednote
```
