# SCHEDULER GUIDE

目标：把「discover 抓取入库」和「opportunity 评估」按两阶段定时运行。

## 1. 通用入口脚本

- 单轮执行：`bash scripts/run_scheduled_cycle.sh [discover|opportunity|all]`
- 常驻循环：`bash scripts/run_scheduled_loop.sh [discover|opportunity|all]`

脚本内部不会 `source .env`；配置由 Python 侧 `Settings` 读取 `.env`。同时加互斥锁防止重入（`/tmp/rednote-<mode>.lock`）。

## 2. 可调环境变量

discover：
- `SCHED_DISCOVER_COMMAND_TEMPLATE`（默认回退 `CRAWL_COMMAND_TEMPLATE`）
- `SCHED_DISCOVER_CYCLES`（默认 `1`）
- `SCHED_DISCOVER_INTERVAL_SECONDS`（默认 `0`）
- `SCHED_DISCOVER_KEYWORD_LIMIT`（默认 `20`）
- `SCHED_DISCOVER_NOTE_LIMIT`（默认 `20`）

opportunity：
- `SCHED_OPPORTUNITY_TASK_ID`（默认 `0`，大于 0 时只跑单任务）
- `SCHED_OPPORTUNITY_LIMIT_TASKS`（默认 `20`）
- `SCHED_OPPORTUNITY_PRESCREEN_THRESHOLD`（默认 `3.2`）
- `SCHED_OPPORTUNITY_MATCH_THRESHOLD`（默认 `0.26`）
- `SCHED_OPPORTUNITY_RETRY_BACKOFF_BASE_MINUTES`（默认 `5`）
- `SCHED_OPPORTUNITY_RETRY_BACKOFF_MAX_MINUTES`（默认 `720`）

loop：
- `SCHED_DISCOVER_LOOP_INTERVAL_SECONDS`（默认 `900`）
- `SCHED_OPPORTUNITY_LOOP_INTERVAL_SECONDS`（默认 `600`）
- `SCHED_ALL_LOOP_INTERVAL_SECONDS`（默认 `900`）
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
- `deploy/scheduler/supervisor-opportunity.conf`

步骤：
1. `mkdir -p logs`
2. 把模板里的 `__PROJECT_DIR__` 替换成你的绝对路径
3. 拷贝到 supervisor 配置目录（例如 `/etc/supervisor/conf.d/`）
4. 执行：
   - `supervisorctl reread`
   - `supervisorctl update`
   - `supervisorctl status`

## 5. systemd 模板（Linux）

模板文件：
- `deploy/scheduler/systemd/rednote-discover.service`
- `deploy/scheduler/systemd/rednote-discover.timer`
- `deploy/scheduler/systemd/rednote-opportunity.service`
- `deploy/scheduler/systemd/rednote-opportunity.timer`

步骤：
1. 把 `__PROJECT_DIR__` 和 `__RUN_USER__` 替换成真实值
2. 复制到 `/etc/systemd/system/`
3. 执行：
   - `sudo systemctl daemon-reload`
   - `sudo systemctl enable --now rednote-discover.timer`
   - `sudo systemctl enable --now rednote-opportunity.timer`
4. 查看：
   - `systemctl list-timers | rg rednote`
   - `journalctl -u rednote-discover.service -n 100 --no-pager`
   - `journalctl -u rednote-opportunity.service -n 100 --no-pager`

### 当前项目路径直接可用（步骤 2）

```bash
cd /Users/huyaoqi/Documents/rednote—spider
mkdir -p logs

for f in \
  deploy/scheduler/systemd/rednote-discover.service \
  deploy/scheduler/systemd/rednote-opportunity.service \
  deploy/scheduler/systemd/rednote-discover.timer \
  deploy/scheduler/systemd/rednote-opportunity.timer
do
  sed -e 's|__PROJECT_DIR__|/Users/huyaoqi/Documents/rednote—spider|g' \
      -e "s|__RUN_USER__|$(whoami)|g" "$f" | \
    sudo tee "/etc/systemd/system/$(basename "$f")" >/dev/null
done

sudo systemctl daemon-reload
sudo systemctl enable --now rednote-discover.timer
sudo systemctl enable --now rednote-opportunity.timer

systemctl list-timers | rg rednote
```
