# Streamlit Login QR Design

**Goal:** 在 Streamlit 中显示小红书登录二维码，支持手动刷新，并在二维码超时后自动切换到新二维码。

## Recommended Approach
采用独立 `QR worker` + 数据库存储状态的方案。UI 只负责展示和发起刷新请求，不直接运行长时登录命令。后台 worker 负责生成二维码、维护登录等待进程、处理超时和自动刷新。

## Components
- `login_qr_session`：持久化当前二维码会话状态。
- `LoginQrService`：读写状态、申请刷新、标记生成中/等待扫码/过期/失败/成功。
- `run_login_qr_worker.py`：独立 supervisor 进程，负责二维码命令生命周期。
- `ui/app.py`：新增 `Login QR` 面板，展示图片、状态、剩余时间，并提供 `Refresh QR` 按钮。

## Runtime Flow
1. 页面点击 `Refresh QR`。
2. `LoginQrService` 记录刷新请求。
3. worker 检测到请求，启动登录二维码命令。
4. worker 监听 `logs/login_qr/` 新 PNG 文件，拿到图片后更新状态为 `waiting_scan`。
5. 若命令退出成功，则标记 `success`；若超时或失败，则标记 `expired/failed`。
6. 若二维码超时且 `auto_refresh` 开启，则 worker 自动生成下一张。

## Safety
- 二维码面板只展示最近一条当前会话，不删除历史图片。
- 同时只允许一个二维码登录进程存活。
- 刷新时会终止旧进程，旧二维码立即失效。
