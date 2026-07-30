# 新增 grok_search 平台上传脚本（SSO→sub2api）

## 背景

sub2api 新增 `grok_search` 平台：通过 SSO token 走 `console.x.ai` 直接对话，绕过 multi-agent 的 402。grok-register 产出的 `cpa_auths/xai-*.json` 现已含 `sso` 字段（Task 2 变更后），可作为该平台的上传来源。

本任务新增一个**独立批量脚本**，把 xai-*.json 里的 sso 上传到 sub2api(platform=grok_search)，**不改动**现有 `sub2api_upload.py`（platform=grok）。

## 目标

独立脚本 `sub2api_grok_search_upload.py`：扫描 `cpa_auths/xai-*.json` → 读 `sso` → POST `/api/v1/admin/accounts`(platform=grok_search) → 成功/失败归档到独立目录。

## 需求

### 功能
- 扫描 `cpa_auths/xai-*.json`（与现有 grok 上传同一来源）。
- 从 json 读 `sso`；为空则**跳过**（文件留原地，不归失败，等 sso 补全后下一轮再传）。
- 无刷新调用（SSO 直传，不像 grok 平台需要 refresh-token）。
- POST 请求体：
  - `platform="grok_search"`、`type="apikey"`
  - `credentials={"sso_token": <sso>, "base_url": "https://console.x.ai"}`
  - `extra={"grok_search_chat_completions": true, "email": <json.email>, "password": <json.password>}`
  - `group_ids=[7]`、`proxy_id=1`、`concurrency=10`、`priority=1`、`rate_multiplier=1`、`load_factor=null`、`auto_pause_on_expired=true`
  - `name` 取 email 用户名部分（与现有脚本一致），`notes=""`
- 成功判定：响应 `body.code == 0`（非 HTTP 200）。
- 成功归档 `cpa_auths/uploaded_search/`，失败归档 `cpa_auths/failed_search/`（**独立于** grok 的 uploaded/failed_tokens，因不同时上传两平台）。

### 配置
- 写死常量：`GROK_SEARCH_PROXY_ID=1`、`GROK_SEARCH_GROUP_IDS=[7]`、`GROK_SEARCH_CONCURRENCY=10`、`GROK_SEARCH_PRIORITY=1`、`GROK_SEARCH_RATE_MULTIPLIER=1`、`GROK_SEARCH_BASE_URL="https://console.x.ai"`。
- `SUB2API_URL="http://localhost:8080"`、`SUB2API_TOKEN=""`（占位，用户自填）。
- `SUB2API_TOKEN` 为空 → 启动即退出（exit 1）。

### 约束
- **独立脚本，不动** `sub2api_upload.py`（platform=grok）。
- 复用 `_sub2api_request`、`_safe_move`、`_short` 等通用工具的实现风格（各自一份，不跨文件 import，保持脚本自包含可独立运行）。
- 依赖 `curl_cffi`（项目已有）。
- 日志中文，风格与现有脚本一致。

## 验收标准

- [ ] 新文件 `sub2api_grok_search_upload.py` 存在且 `python sub2api_grok_search_upload.py` 可运行（SUB2API_TOKEN 为空时 exit 1）。
- [ ] 无 sso 的 json → `upload_one` 返回 `"skipped"`，文件留原地，不创建 `failed_search/`。
- [ ] 有 sso → POST grok_search 账号契约，`code==0` 移 `uploaded_search/`，否则移 `failed_search/`。
- [ ] `main` 汇总输出 `成功 / 失败 / 跳过` 三计数。
- [ ] 不修改 `sub2api_upload.py`。

## 备注

- 来源 json 的 `sso` 字段由 Task 2（grok-auth-json-archive）保证：注册成功即写基础 json 含 sso。
- `extra.grok_search_chat_completions` 恒为 `true`（按用户指定）。
