# 凭证管线：注册归档与 sub2api 上传

> 项目特定的可执行契约。记录 Grok 注册产出的 `cpa_auths` 凭证 json 的格式/生成时机，以及 sub2api（platform=grok）上传契约。

## 概述

Grok 注册成功后产出多类凭证，按下游分派：

- **SSO token** → `accounts_*.txt` + grok2api 池（Grok Web 模拟）
- **xAI OIDC 凭证** → `cpa_auths/xai-<email>.json`（API 网关：sub2api / cli-chat-proxy）

`cpa_auths/xai-*.json` 是多方的共享数据契约：注册流程写、`cpa_export` 覆盖、`sub2api_upload.py` 读、人工排查。改其字段格式属跨层变更，须同步所有读写方。

---

## Scenario 1: cpa_auths 凭证 json 归档

### 1. Scope / Trigger

注册成功（拿到 sso）即归档；CPA 成功补全 OIDC。触发：json 格式跨层契约 + 外部上传消费。

### 2. Signatures

- `cpa_xai.schema.build_basic_auth(email, password, sso) -> dict`：注册成功时写的基础 payload
- `cpa_xai.schema.build_cpa_xai_auth(email, access_token, refresh_token, password="", sso="", id_token=None, expires_in=None, ...) -> dict`：CPA 成功时的完整 OIDC payload
- `cpa_xai.writer.write_cpa_xai_auth(auth_dir, payload, filename=None) -> Path`：原子写入（`tempfile.mkstemp` + `os.fsync` + `os.replace`，权限 `0o600`）
- `cpa_xai.schema.credential_file_name(email="", sub="") -> str`：返回 `xai-<email>.json`（email 为空回退 sub / 时间戳）
- 注册流程 ops：`write_auth_json(email, password, sso) -> {"ok": bool, "path"|"error": str}`

### 3. Contracts

基础 json（注册成功即写，`build_basic_auth`）：
```json
{"type":"xai","email":"...","password":"...","sso":"...","last_refresh":"<UTC ISO>"}
```

完整 json（CPA 成功后 `build_cpa_xai_auth` 覆盖），在基础字段之上补：
```json
{"type":"xai","access_token":"<jwt>","refresh_token":"...","token_type":"Bearer",
 "expires_in":21600,"expired":"<UTC ISO>","last_refresh":"<UTC ISO>","email":"...",
 "password":"...","sso":"...","sub":"<uuid>","base_url":"https://cli-chat-proxy.grok.com/v1",
 "redirect_uri":"http://127.0.0.1:56121/callback","token_endpoint":"https://auth.x.ai/oauth2/token",
 "auth_kind":"oauth","id_token":"<jwt>"}
```

**关键不变量**：
- 基础与完整 json **同名** `xai-<email>.json`；CPA 写入用 `os.replace` 原子覆盖基础。
- `password` 与 `sso` 在两条路径**都**写入（基础来自 `build_basic_auth`，完整来自 `build_cpa_xai_auth` 的 `password`/`sso` 参数）。
- `sso`/`password` 由 `mint_and_export(sso=...)` / `export_cpa_xai_for_account(sso=...)` 透传；注册主流程的 `write_auth_json` 直接用注册时已知的 email/password/sso。

### 4. Validation & Error Matrix

- `write_auth_json` 异常 → 记日志、返回 `{ok:False}`，**不阻断注册**（账号仍入 `accounts_*.txt` 与 grok2api 池）。
- `email` 为空 → `credential_file_name` 回退 `sub`，再回退时间戳。
- `build_cpa_xai_auth` 缺 `access_token`/`refresh_token` → `ValueError`（仅 CPA 成功路径调用；基础路径走 `build_basic_auth`，无此校验）。

### 5. Good / Base / Bad Cases

- **Good**：CPA 开启且成功 → 完整 json（OIDC + password + sso）。
- **Base**：CPA 关闭/失败 → 基础 json（email/password/sso），账号仍归档不丢。
- **Bad**：`write_auth_json` 抛异常 → 必须**不**中断后续 `add_tokens`/`export_cpa`（独立 try/except）。

### 6. Tests Required

- `build_basic_auth(...)` 输出含非空 `sso`/`password`。
- `build_cpa_xai_auth(..., sso='x')` 输出含 `"sso"`。
- 覆盖一致性：基础 → 完整覆盖后 `sso` 保留且 `access_token` 补全、文件名不变。
- 异常路径：`persist_account_result` 中 `write_auth_json` 失败时仍执行 `add_tokens`/`export_cpa`。

### 7. Wrong vs Correct

**Wrong**：仅在 `export_cpa` 成功才生成 json → CPA 失败/关闭时账号无 json 归档，sso 丢失。
**Correct**：注册成功即写基础 json（含 sso），CPA 成功后覆盖补全 OIDC 字段。

---

## Scenario 2: sub2api (platform=grok) 批量上传

### 1. Scope / Trigger

外部 API 集成契约（sub2api 管理 API）。脚本 `sub2api_upload.py`。

### 2. Signatures / Endpoints

- 刷新：`POST {SUB2API_URL}/api/v1/admin/grok/oauth/refresh-token`
- 创建账号：`POST {SUB2API_URL}/api/v1/admin/accounts`
- 鉴权：`Authorization: Bearer {SUB2API_TOKEN}`
- 写死常量：`GROK_PROXY_ID=1`、`GROK_GROUP_IDS=[5]`、`GROK_CONCURRENCY=10`、`GROK_PRIORITY=1`、`GROK_RATE_MULTIPLIER=1`、`DEFAULT_BASE_URL=https://cli-chat-proxy.grok.com/v1`；`SUB2API_URL/SUB2API_TOKEN` 为占位常量（用户自填）。

### 3. Contracts

- 刷新入参 `{"refresh_token": "...", "proxy_id": 1}`，成功响应 `{"code":0,"data":{access_token, refresh_token, token_type, expires_in, expires_at, client_id, scope, sub, team_id}}`。
- 创建账号 body：`platform="grok"`、`type="oauth"`，`credentials` = `{access_token, token_type:"Bearer", expires_at, client_id, scope, sub, team_id, base_url, refresh_token}`，`extra={"password": <取自 json>}`。
- **成功判定**：`body.code == 0`（非 HTTP 200）。
- credentials 字段优先用刷新响应；缺失时从 `access_token` 的 JWT payload 兜底解码（`aud→client_id`、`scope`、`sub`、`team_id`、`exp→expires_at`）。

### 4. Validation & Error Matrix

- **无 `refresh_token`**（基础 json 尚未被 CPA 补全）→ `return "skipped"`，**不移动**，留原地等待补全。
- 刷新失败 / 上传 `code!=0` / 网络异常 → 移 `cpa_auths/failed_tokens/`。
- 上传成功（`code==0`）→ 移 `cpa_auths/uploaded/`。
- `SUB2API_TOKEN` 为空 → 启动即退出（exit 1），不发起请求。

### 5. Good / Base / Bad Cases

- **Good**：有 rt → refresh → 上传成功 → `uploaded/`。
- **Base**：无 rt（基础 json 未补全）→ 跳过，留原地（下一轮 CPA 补全后再传）。
- **Bad**：refresh 失败 → `failed_tokens/`。

### 6. Tests Required

- 无 rt json → `upload_one` 返回 `"skipped"`，文件留原地，不创建 `failed_tokens/`。
- `main` 汇总含 `跳过 N` 计数。
- 空配置早退（exit 1）。

### 7. Wrong vs Correct

**Wrong**：无 rt 当作失败移到 `failed_tokens/` → 把"尚未补全"的有效账号误判为失败。
**Correct**：无 rt 跳过、留原地，等 CPA 补全 rt 后下一轮上传。

---

## Scenario 3: sub2api (platform=grok_search) 批量上传

### 1. Scope / Trigger

外部 API 集成契约（sub2api grok_search 平台）。脚本 `sub2api_grok_search_upload.py`（与 Scenario 2 的 grok 上传**并列、独立**）。SSO 直传走 `console.x.ai`，绕过 multi-agent 402。

### 2. Signatures / Endpoints

- 创建账号：`POST {SUB2API_URL}/api/v1/admin/accounts`
- 鉴权：`Authorization: Bearer {SUB2API_TOKEN}`
- **无刷新接口**（SSO 直传，与 Scenario 2 不同）。
- 写死常量：`GROK_SEARCH_PROXY_ID=1`、`GROK_SEARCH_GROUP_IDS=[7]`、`GROK_SEARCH_CONCURRENCY=10`、`GROK_SEARCH_PRIORITY=1`、`GROK_SEARCH_RATE_MULTIPLIER=1`、`GROK_SEARCH_BASE_URL="https://console.x.ai"`；`SUB2API_URL/SUB2API_TOKEN` 为占位常量（用户自填）。
- **独立归档**：`cpa_auths/uploaded_search/`、`cpa_auths/failed_search/`（与 grok 的 `uploaded/failed_tokens` 并列、互不干扰）。

### 3. Contracts

- 创建账号 body：`platform="grok_search"`、`type="apikey"`，`credentials={"sso_token": <json.sso>, "base_url": "https://console.x.ai"}`，`extra={"grok_search_chat_completions": true, "email": <json.email>, "password": <json.password>}`，`group_ids=[7]`、`proxy_id=1`、`concurrency=10`、`priority=1`、`rate_multiplier=1`、`load_factor=null`、`auto_pause_on_expired=true`；`name`=email 用户名部分（空回退文件名）、`notes=""`。
- **成功判定**：`body.code == 0`（非 HTTP 200）。

### 4. Validation & Error Matrix

- **无 `sso`**（基础 json 尚未被 CPA 补全）→ `return "skipped"`，**不移动**，留原地等待补全（与 Scenario 2 的"无 refresh_token 跳过"对称）。
- 上传 `code!=0` / 网络异常 → 移 `cpa_auths/failed_search/`。
- 上传成功（`code==0`）→ 移 `cpa_auths/uploaded_search/`。
- `SUB2API_TOKEN` 为空 → 启动即退出（exit 1）。

### 5. Good / Base / Bad Cases

- **Good**：有 sso → 上传成功 → `uploaded_search/`。
- **Base**：无 sso（基础 json 未补全）→ 跳过、留原地（下一轮补全后再传）。
- **Bad**：上传 `code!=0` / 网络异常 → `failed_search/`。

### 6. Tests Required

- 无 sso json → `upload_one` 返回 `"skipped"`，文件留原地，不创建 `failed_search/`。
- `build_request_body` 输出 `credentials={sso_token, base_url:console.x.ai}`、`extra.grok_search_chat_completions===true`、`group_ids=[7]`、`load_factor===null`。
- `main` 汇总含 `跳过 N` 计数。
- 空配置早退（exit 1）。

### 7. Wrong vs Correct

**Wrong**：与 grok 共用同一脚本/归档目录 → 两平台上传互相覆盖归档、回归风险。
**Correct**：独立脚本 + 独立归档目录（`uploaded_search/failed_search`），与 grok 路径零耦合。

---

## 关联

- 任务文档：`.trellis/tasks/07-28-grok-sub2api-upload/`、`.trellis/tasks/07-29-grok-auth-json-archive/`、`.trellis/tasks/07-29-grok-search-upload/`
- 代码：`cpa_xai/schema.py`、`cpa_xai/mint.py`、`cpa_export.py`、`registration_flow.py`、`grok_register_ttk.py`、`sub2api_upload.py`、`sub2api_grok_search_upload.py`
