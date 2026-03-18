# Streamlit Phone Login Design

**Goal:** 在现有 Login QR 面板旁增加真正可用的小红书手机号验证码登录入口，让用户可以在 Streamlit 页面里输入手机号、启动登录、提交验证码，并让后台进程完成登录。

**Why This Shape:** MediaCrawler 原生手机号登录依赖进程内 `memory` cache，Streamlit 和爬虫进程分离时验证码无法共享；同时 `XiaoHongShuCrawler` 还把 `login_phone` 写死为空字符串。直接在 UI 里加一个验证码输入框不会生效。

## Chosen Approach

采用 `rednote-spider` 包装层方案，不直接修改 `/root/MediaCrawler` 仓库源码：

- 新增 `login_phone_session` 持久化表，保存手机号登录状态、当前手机号、验证码 nonce、错误信息和时间戳。
- 新增 `LoginPhoneService` 处理“开始登录 / 等待验证码 / 提交验证码 / 成功 / 失败”状态流。
- 在现有 supervisor 运行的登录 worker 进程里增加 phone worker 轮询逻辑，负责启动手机号登录子进程、读取子进程阶段日志并更新数据库状态。
- 扩展现有 `run_mediacrawler_with_terminal_qr.py`，为 MediaCrawler 注入运行时补丁：
  - 从环境变量读取手机号；
  - 替换小红书 `login_by_mobile()`，改为从 `rednote-spider` 数据库读取用户在 Streamlit 中提交的验证码；
  - 输出稳定阶段标记，供 worker 判断是否进入“等待验证码 / 正在提交 / 成功 / 失败”。

## Data Flow

1. 用户在 Streamlit 输入手机号并点击“开始手机号登录”。
2. `LoginPhoneService.start_login()` 生成新的 attempt nonce，状态置为 `pending`。
3. 登录 worker 发现 `pending` 状态，启动 `LOGIN_PHONE_COMMAND` 子进程，并通过环境变量把手机号传给包装脚本。
4. 包装脚本给 MediaCrawler 注入手机号登录补丁，浏览器发起发码流程。
5. 补丁在进入“等待验证码”阶段时输出标记；worker 读到后将数据库状态置为 `waiting_code`。
6. 用户在 Streamlit 输入短信验证码并点击“提交验证码”。
7. `LoginPhoneService.submit_code()` 持久化验证码并递增 code nonce。
8. MediaCrawler 补丁轮询数据库读到新验证码后自动填入并提交，成功后进程退出 0，worker 标记为 `success`；失败则带错误信息退出并标记为 `failed`。

## Constraints

- 不与二维码登录会话共享表，避免把现有稳定流程改成大重构。
- 同一平台只维护一个手机号登录会话；新的开始请求会覆盖旧会话并让 worker 重启子进程。
- 验证码仅在当前 attempt 下有效，防止旧验证码串到新登录流程。
- Streamlit 只负责发起动作和展示状态，不直接持有子进程。

## Error Handling

- 子进程启动失败：状态 `failed`，记录 `last_error`。
- 长时间未进入等待验证码阶段：worker 根据日志尾和退出状态标记失败。
- 用户提交验证码过早或流程未启动：服务层拒绝提交并提示当前状态。
- 验证码超时或页面出现验证失败：补丁抛错退出，由 worker 记录日志尾。

## Testing

- 服务层：状态流、attempt/code nonce、非法状态提交。
- Worker：启动 phone 子进程、阶段标记驱动状态流、提交验证码后成功收敛、失败日志回写。
- UI helper：空状态提示、状态文本。
- 配置：新增 `LOGIN_PHONE_*` 环境变量加载。
