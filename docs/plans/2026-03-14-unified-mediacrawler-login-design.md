# Unified MediaCrawler Login Design

**Goal:** 将当前 Streamlit 小红书登录链路重构为一个以 MediaCrawler 浏览器 profile 和 `pong()` 探测为真相源的统一登录内核，让二维码登录与手机号登录都能稳定运营，并支持自动与人工混合处理安全校验。

## Context

当前实现存在三类根问题：

1. Streamlit/数据库自己维护了一套“登录状态”，但 MediaCrawler 真正依赖的是浏览器 profile、cookie 和 `XiaoHongShuClient.pong()`。
2. 二维码登录实际跑的是完整爬虫子进程，而不是纯登录子进程，导致二维码过期、worker 回收、爬虫执行三个生命周期相互污染。
3. 手机号登录、二维码登录、人工安全校验分别由不同状态机和桥接层维护，状态真相分裂，恢复和重试逻辑脆弱。

目标场景已明确：

- 单个 MediaCrawler 实例
- 单个小红书账号
- 手机号登录必须是一等公民
- 安全校验既要支持自动恢复，也要支持人工接管

## Design Principles

- 只保留一个登录真相源：MediaCrawler 持久浏览器目录和 `pong()` 结果
- 只保留一个当前登录状态单例，不再维护 QR/phone 双套会话表
- 登录运行时必须是 login-only，不允许借用完整 crawl 进程
- 自动流程失败时不立刻销毁上下文，而是优先进入人工接管模式
- 所有 UI 展示都来自“当前状态 + 事件流”，而不是猜测子进程语义

## Target Architecture

### 1. Login Controller

新增一个常驻统一登录控制器，负责：

- 定时或手动执行登录态探测
- 启动二维码登录尝试
- 启动手机号登录尝试
- 接收验证码提交
- 接收人工接管完成后的继续探测请求
- 维护单一活动 attempt
- 在进程重启后收敛陈旧状态

控制器不负责抓取任务，不负责调度 discover/opportunity，只负责认证。

### 2. Login-Only Runtime

新增一个专用 `run_mediacrawler_login_only.py` 入口：

- 运行在 MediaCrawler cwd 和其虚拟环境中
- 仅执行浏览器启动、主页打开、登录态探测、二维码登录、手机号登录、安全校验处理和最终复探测
- 二维码或手机号登录成功的唯一标准都是最终 `pong() == True`
- 成功后退出 0，失败退出非 0，并输出稳定阶段事件

这个 runtime 取代当前用完整 `search` 命令模拟登录的方式。

### 3. Persistent Browser Profile

所有登录尝试都复用同一个 MediaCrawler 浏览器目录：

- 二维码登录
- 手机号登录
- 安全校验人工接管
- 登录态探测

只有这一份 profile 会对 MediaCrawler 正式爬虫生效，因此它是认证真相源的一部分。

### 4. Streamlit Console

Streamlit 仅作为控制台，不再承担登录真相判断职责。

它负责：

- 展示当前认证状态
- 展示当前登录流程状态
- 展示最近二维码/安全校验截图
- 展示最近事件流和错误
- 提供控制按钮和验证码输入

## Data Model

### login_runtime_state

新增统一状态表，只允许单平台一行：

- `platform`
- `auth_state`: `unknown | authenticated | unauthenticated`
- `flow_state`: `idle | probing | starting | waiting_qr_scan | waiting_phone_code | waiting_security_verification | verifying | need_human_action | failed`
- `active_method`: `qr | phone | null`
- `attempt_id`
- `action_nonce`
- `phone_number`
- `submitted_sms_code`
- `sms_code_nonce`
- `handled_sms_code_nonce`
- `qr_image_path`
- `security_image_path`
- `last_probe_ok`
- `last_probe_at`
- `last_error`
- `controller_pid`
- `child_pid`
- `profile_dir`

### login_event

新增事件表，append-only：

- `platform`
- `attempt_id`
- `event_type`
- `message`
- `payload`
- `created_at`

事件流用于 UI 实时展示和问题审计，不反向驱动真相。

## Unified State Machine

### Auth State

- `unknown`: 尚未探测
- `authenticated`: `pong()` 通过
- `unauthenticated`: `pong()` 不通过

### Flow State

- `idle`: 当前无活动 attempt
- `probing`: 正在执行登录态探测
- `starting`: 已创建登录 attempt，准备进入方法分支
- `waiting_qr_scan`: 已生成二维码，等待用户扫描
- `waiting_phone_code`: 已请求验证码，等待用户输入短信验证码
- `waiting_security_verification`: 识别到安全校验页，等待自动或人工完成
- `verifying`: 已提交验证码或已完成扫码，正在等待复探测结果
- `need_human_action`: 自动流程无法继续，但浏览器上下文保留，允许人工接管
- `failed`: 当前 attempt 结束失败

`authenticated/unauthenticated` 与 `flow_state` 解耦。前者只反映 MediaCrawler 可用性，后者只反映当前控制器动作。

## Runtime Behavior

### Probe

控制器执行 `probe_login_state`：

1. 复用同一 profile 启动浏览器
2. 创建 MediaCrawler client
3. 调用 `pong()`
4. 根据结果更新 `auth_state`
5. 写入事件流

### QR Login

1. 如果 probe 已登录，直接结束
2. 未登录时进入二维码登录
3. 捕获二维码图片并保存到 `qr_image_path`
4. 更新 `flow_state=waiting_qr_scan`
5. 扫码完成后不直接视为成功，而是再次 `pong()`
6. 只有复探测通过才将 `auth_state=authenticated`

### Phone Login

1. 如果 probe 已登录，直接结束
2. 未登录时进入手机号登录流程
3. 若直接到验证码页，进入 `waiting_phone_code`
4. 若命中安全校验页，进入 `waiting_security_verification`
5. 用户提交验证码后，runtime 消费验证码，进入 `verifying`
6. 若验证码错误或过期，返回 `waiting_phone_code`
7. 只有复探测通过才将 `auth_state=authenticated`

### Security Verification

若自动流程检测到风控页或未知交互：

- 不直接销毁浏览器
- 保留当前 attempt
- 保存截图
- 更新到 `waiting_security_verification` 或 `need_human_action`
- 允许用户在同一浏览器上下文中人工处理
- 人工完成后，控制器只触发重新探测，不重新创建新 attempt

## Failure Handling

### Controller Restart

控制器启动时执行 reconcile：

- 若状态为 `starting/waiting_*/verifying`
- 但 `child_pid` 不存在
- 收敛为 `idle`
- 保留 `auth_state`
- 写一条 `controller_recovered_stale_attempt`

### Attempt Failure

以下情况标记 attempt 失败：

- runtime 启动失败
- 浏览器初始化失败
- 二维码/手机号流程确定不可恢复
- 手动取消 attempt

但“需要安全校验”和“验证码错误”不应立即落到终态失败。

## UI Design

统一替换现有 QR/Phone 双面板，改为一个 `Login Control` 面板：

- 当前认证状态
- 当前流程状态
- 当前方法
- 最近一次 probe 结果与时间
- 当前二维码
- 当前安全校验截图
- 当前手机号
- 验证码输入框
- 最近事件流
- 最近错误

操作：

- `探测登录状态`
- `开始二维码登录`
- `开始手机号登录`
- `提交验证码`
- `我已处理完成，继续探测`
- `取消当前登录尝试`

## Migration Strategy

### Replace

- `login_qr_session`
- `login_phone_session`
- `LoginQrService`
- `LoginPhoneService`
- `login_qr_worker.py`
- `login_phone_worker.py`
- `login_phone_bridge.py`

### Reuse

- `mediacrawler_qr.py`
- `mediacrawler_phone.py`
- `mediacrawler_runtime.py`

但这些模块改为服务于 login-only runtime 和统一控制器。

## Testing Strategy

### Unit

- 统一状态机迁移
- stale attempt reconcile
- action nonce / attempt id 变化
- SMS code 消费与重试
- probe 结果映射

### Integration

- controller + runtime 事件流
- QR 成功后经 `pong()` 复探测收敛
- phone 成功后经 `pong()` 复探测收敛
- security verification 进入人工接管再恢复
- controller 重启后 stale state 自动收敛

### UI

- 单一面板状态文案
- 事件流展示
- 按钮启用禁用条件

## Acceptance Criteria

- 二维码登录成功不依赖爬虫任务执行完成
- 手机号登录错误验证码可重试，不重建 attempt
- 安全校验支持人工接管并复用同一浏览器上下文
- 控制器重启后不会留下假活着状态
- UI 上“已登录”必须来自最新 probe
- 正式 crawl 前可调用统一 probe 判断是否可用
