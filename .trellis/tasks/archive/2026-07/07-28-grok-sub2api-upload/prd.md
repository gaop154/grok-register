# 新增 Grok 凭证批量上传到 sub2api 脚本

## Goal

提供一个**独立批量脚本**，把 `cpa_auths/` 目录下 CPA 导出的 xAI OIDC 凭证（`xai-*.json`）批量上传到 sub2api（`platform=grok`），使注册产出的 Grok 凭证能被 sub2api 统一纳管。借鉴参考项目 `RegistrationMachineProject/chatgpt_register/sub2api_upload.py` 的上传机制，但目标平台为 Grok。

**前置**：改造 CPA 导出链路使 `xai-*.json` 携带 `password` 字段，供上传时写入 sub2api 账号的 `extra.password`。

## Background

- grok-register 注册成功后产出两类凭证：
  - Grok SSO token（写入 grok2api 池，**本任务不涉及**）。
  - xAI OIDC 凭证（`cpa_auths/xai-*.json`，含 `access_token`/`refresh_token`/`base_url` 等，由 `cpa_xai/schema.py:build_cpa_xai_auth` 生成）。
- sub2api 已支持 Grok 平台账号，接口契约（用户提供，2026-07-28）：
  - 刷新：`POST /api/v1/admin/grok/oauth/refresh-token`，入参 `{"refresh_token": "...", "proxy_id": 1}`，返回 `data` 含 `access_token/refresh_token/token_type/expires_in/expires_at/client_id/scope/sub/team_id`。
  - 创建账号：`POST /api/v1/admin/accounts`，`platform="grok"`、`type="oauth"`，`credentials` 需 `access_token/token_type/expires_at/client_id/scope/sub/team_id/base_url/refresh_token`。
- xAI 凭证 json 当前不含密码。密码在注册流程中已知（与 `accounts_*.txt` 的 `email----password----sso` 同源同值），`mint_and_export` 已持有 `password` 参数但未写入 json（见 `cpa_xai/mint.py:51`、`cpa_xai/schema.py:build_cpa_xai_auth`）。

## Requirements

### 功能需求

- R0（前置·源头改造）使 `xai-*.json` 携带 `password` 字段：
  - R0.1 `cpa_xai/schema.py:build_cpa_xai_auth` 增加 `password` 参数并写入返回 payload。
  - R0.2 `cpa_xai/mint.py` 调用 `build_cpa_xai_auth` 时传入 `password=password`（即注册密码，与 `accounts_*.txt` 同源同值，无需回读文件）。
- R1 独立脚本 `sub2api_upload.py`，位于项目根，可 `python sub2api_upload.py` 直接运行，不依赖 GUI/CLI 主流程。
- R2 扫描 `cpa_auths/` 下所有 `xai-*.json`（不递归子目录，排除 `uploaded/`、`failed_tokens/`）。
- R3 对每个凭证文件：
  - R3.1 先调用 `POST /api/v1/admin/grok/oauth/refresh-token` 刷新（入参取自文件的 `refresh_token` + 写死的 `proxy_id`）。
  - R3.2 刷新成功后，用刷新响应 `data` 的字段构造 `credentials`（access_token / token_type=Bearer / expires_at / client_id / scope / sub / team_id / base_url / refresh_token）。
  - R3.3 调用 `POST /api/v1/admin/accounts` 上传，`platform=grok`、`type=oauth`，附带写死的 `proxy_id/concurrency/priority/rate_multiplier/group_ids/expires_at=null/auto_pause_on_expired=true`。
  - R3.4 上传请求体 `extra` 包含 `password`（取自 json 的 `password` 字段；历史无该字段的文件留空字符串）。
- R4 上传结果处理：
  - R4.1 上传成功（响应 `code==0`）：把源文件移动到 `cpa_auths/uploaded/`。
  - R4.2 刷新失败或上传失败：把源文件移动到 `cpa_auths/failed_tokens/`，并在日志记录原因。
- R5 单个文件失败不得中断整批，继续处理后续文件。
- R6 上传**不**调用 `/test` 验证（grok `/test` 契约未提供，避免误删有效账号）。

### 配置需求（全部写死，脚本顶部常量）

- `SUB2API_URL`：占位 `http://localhost:8080`，由用户自行修改。
- `SUB2API_TOKEN`：占位空串，由用户自行填入。
- `GROK_PROXY_ID = 1`
- `GROK_GROUP_IDS = [5]`
- `GROK_CONCURRENCY = 10`
- `GROK_PRIORITY = 1`
- `GROK_RATE_MULTIPLIER = 1`
- `DEFAULT_BASE_URL = "https://cli-chat-proxy.grok.com/v1"`
- `AUTH_DIR = "./cpa_auths"`、`UPLOADED_DIR`、`FAILED_DIR` 派生路径。

### 非功能需求

- N1 仅依赖 `curl_cffi`（项目已有），不引入新依赖。
- N2 除前置 R0 外不修改现有模块：仅改动 `cpa_xai/schema.py`、`cpa_xai/mint.py`；不修改 `grok_register_ttk.py` / `account_outputs.py` / `cpa_export.py`；上传功能为纯新增脚本。
- N3 脚本对网络异常、非预期 JSON 健壮处理，异常文件归入 `failed_tokens/` 而非崩溃。
- N4 凭据 `SUB2API_TOKEN` 以常量占位形式提供，默认不把真实 token 写入代码（避免随 git 泄露）。

## Acceptance Criteria

- [ ] AC0（前置）`cpa_xai/schema.py`、`cpa_xai/mint.py` 改造后，新生成的 `xai-*.json` 含非空 `password` 字段，且与 `accounts_*.txt` 中同 email 的密码一致。
- [ ] AC1 项目根存在 `sub2api_upload.py`，`python sub2api_upload.py` 可独立运行（无 GUI/Tk 依赖）。
- [ ] AC2 运行时扫描 `cpa_auths/xai-*.json`，对每个文件先 refresh 再 POST 创建账号。
- [ ] AC3 构造的 `credentials` 包含 refresh 响应中的 `client_id/scope/sub/team_id/expires_at` 与 `base_url/refresh_token/access_token`。
- [ ] AC3.1 上传请求体 `extra.password` 取自 json：json 有则带、无则空字符串。
- [ ] AC4 上传成功的文件被移动到 `cpa_auths/uploaded/`；失败的移动到 `cpa_auths/failed_tokens/`。
- [ ] AC5 单文件失败不中断批次，结束时输出成功/失败计数。
- [ ] AC6 不调用 `/test`，不影响 grok-register 主流程。
- [ ] AC7 `SUB2API_URL/SUB2API_TOKEN` 为常量占位，其余 sub2api 参数写死，与用户提供的接口入参一致。
- [ ] AC8（手动）用户填入真实 `SUB2API_TOKEN` 并放入一个有效 `xai-*.json` 后运行，账号在 sub2api 成功创建（`code==0` 且返回 `id`）。

## Out of Scope

- 实时上传（注册成功后即时入 sub2api）——本次不做，仅批量脚本。
- 上传后 `/test` 验证与 401 自动删除。
- OpenAI/codex 平台支持。
- GUI/CLI 配置入口（配置写死，不暴露到界面）。
- sub2api 账号巡检/刷新服务（参考项目的 cron、check_and_clean 等）。
