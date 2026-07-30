# Design — Grok 凭证批量上传到 sub2api

> 配套 `prd.md`。本文聚焦技术设计：边界、契约、数据流、权衡、兼容性、回滚形态。

## 1. 边界与文件组织

- **新增为主**：在项目根新建 `sub2api_upload.py`，独立可运行。前置仅改动 `cpa_xai/schema.py`、`cpa_xai/mint.py` 两处（让 json 带 password），不修改 `grok_register_ttk.py` / `account_outputs.py` / `cpa_export.py` 等其他模块。
- 不接入 GUI/CLI 主流程，不接入注册回调。用户手动执行。
- 仅依赖 `curl_cffi`（项目已通过 `from curl_cffi import requests` 使用）。

## 2. 接口契约（来自用户，2026-07-28）

### 2.1 刷新
```
POST {SUB2API_URL}/api/v1/admin/grok/oauth/refresh-token
Authorization: Bearer {SUB2API_TOKEN}
Body: {"refresh_token": "<来自 xai 文件>", "proxy_id": 1}
```
成功响应：`{"code":0,"message":"success","data":{access_token, refresh_token, token_type, expires_in, expires_at, client_id, scope, sub, team_id}}`

### 2.2 创建账号
```
POST {SUB2API_URL}/api/v1/admin/accounts
Authorization: Bearer {SUB2API_TOKEN}
Body: 见 §4
```
成功响应：`{"code":0,...,"data":{"id":<int>,...}}`。判定成功 = `code == 0`（与刷新判定一致，对齐 sub2api 统一返回码风格；参考项目用 HTTP 200/201 判定，但本项目接口以 `code==0` 为业务成功，故以此为准）。

### 2.3 前置：xAI 凭证 json 携带 password
- `cpa_xai/schema.py:build_cpa_xai_auth` 增加 `password=""` 参数，payload 增加 `"password": str(password or "")`。
- `cpa_xai/mint.py` 调用处（`mint.py:51`）传入 `password=password`。
- 该 password 即注册密码，与 `accounts_*.txt` 同源同值，无需回读文件。
- 兼容性：历史已生成的 json（无 password）继续可用；上传脚本读取时缺字段则 `extra.password=""`。

## 3. 数据流

```
扫描 cpa_auths/xai-*.json （排除 uploaded/、failed_tokens/）
        │
        ▼ (逐文件，串行)
读取 JSON → 取 refresh_token
        │
        ▼
refresh_grok_token() → POST /grok/oauth/refresh-token
        │ 失败 ───────────────────► 移动到 failed_tokens/
        ▼ 成功
build_request_body() → 拼 credentials + 外层 body
        │
        ▼
POST /api/v1/admin/accounts
        │ code != 0 / 异常 ──────► 移动到 failed_tokens/
        ▼ code == 0
移动到 uploaded/
        │
        ▼
统计 + 打印汇总
```

## 4. 请求体构造（build_request_body）

`credentials`（优先用刷新响应字段，缺失时从 `access_token` 的 JWT payload 兜底解码）：
```python
{
  "access_token": refreshed["access_token"],
  "token_type": "Bearer",                       # 写死，refresh 返回亦为 Bearer
  "expires_at": refreshed["expires_at"],        # Unix 秒，来自 refresh 响应
  "client_id": refreshed["client_id"],          # refresh 响应；缺失则 JWT.aud 兜底
  "scope": refreshed["scope"],                  # refresh 响应；缺失则 JWT.scope 兜底
  "sub": refreshed["sub"],                      # refresh 响应；缺失则 JWT.sub 兜底
  "team_id": refreshed["team_id"],              # refresh 响应；缺失则 JWT.team_id 兜底
  "base_url": xai_data.get("base_url") or DEFAULT_BASE_URL,
  "refresh_token": refreshed["refresh_token"],
}
```
> JWT 兜底：`access_token` 中段 base64url 解码后含 `aud(=client_id)/scope/sub/team_id/exp`。仅当 refresh 响应缺字段时启用，避免无谓解码。用户提供的接口示例中 refresh 响应字段齐全，兜底为防御性设计。

外层 body：
```python
{
  "name": <email 前缀；缺失则 sub；再缺失则文件名去后缀>,
  "notes": "",
  "platform": "grok",
  "type": "oauth",
  "credentials": <上>,
  "extra": {"password": xai_data.get("password", "")},   # 取自 json；历史无该字段的文件留空
  "proxy_id": GROK_PROXY_ID,            # 1
  "concurrency": GROK_CONCURRENCY,      # 10
  "priority": GROK_PRIORITY,            # 1
  "rate_multiplier": GROK_RATE_MULTIPLIER,  # 1
  "group_ids": GROK_GROUP_IDS,          # [5]
  "expires_at": None,
  "auto_pause_on_expired": True,
}
```
该结构逐字段对齐用户提供的真实入参（见 prd 背景链接的抓包）。

## 5. 关键函数设计

| 函数 | 职责 | 返回 |
|---|---|---|
| `_sub2api_request(method, path, json_data=None)` | curl_cffi.Session 通用请求，`Authorization: Bearer`、`verify=False`、`timeout=30` | `(http_status:int, body_json:dict\|None)`，网络异常 `(-1, None)` |
| `_decode_jwt_payload(token)` | base64url 解 JWT 中段（兜底用） | `dict`（失败返回 `{}`） |
| `refresh_grok_token(refresh_token)` | 调 `/grok/oauth/refresh-token`，校验 `code==0`，取 `data` | `data:dict` 或 `None` |
| `build_request_body(xai_data, refreshed)` | 拼 §4 的 body | `dict` |
| `_safe_move(filepath, dest_dir)` | mkdir + shutil.move，目标重名追加 `.<n>` 后缀避免覆盖 | 目标路径 |
| `upload_one(filepath)` | 编排：读→refresh→build→POST→归档；异常归 failed | `"ok"` / `"failed"` |
| `main()` | 扫描目录、循环、汇总打印 | 退出码 0/非 0 |

## 6. 错误处理与健壮性

- **单文件隔离**：`upload_one` 全程 try/except，任何异常都归入 `failed_tokens/` 并继续下一个，不抛出。
- **refresh 失败**：HTTP 非 2xx、`code != 0`、网络异常、缺 `access_token` → 视为失败。
- **上传失败**：同上判定；`code != 0` 时打印响应便于排查。
- **目录排除**：扫描时跳过 `uploaded`、`failed_tokens` 目录及非 `xai-*.json` 文件。
- **重名归档**：`_safe_move` 处理目标已存在情况，不覆盖。
- **凭据未填**：启动时检查 `SUB2API_TOKEN` 为空则打印提示并退出（不发起请求）。

## 7. 权衡

| 决策 | 选择 | 理由 |
|---|---|---|
| 串行 vs 并发 | **串行** | 批量通常几十个文件；串行简单可靠、对 sub2api 友好；并发可后续扩展 |
| 成功判定 | `code == 0` | sub2api 统一返回码风格；HTTP 层 curl_cffi 200 即可，但业务以 code 为准 |
| refresh 兜底 | JWT 解码 | 防御性；refresh 响应正常时不触发 |
| name 取值 | email 前缀优先 | 与 sub2api 既有命名一致，便于检索 |
| 配置形态 | 脚本顶部常量 | 用户明确要求写死；token 占位避免泄露 |
| 是否 test | 不做 | grok /test 契约未知；避免误删 |

## 8. 兼容性

- 对现有 grok_register 运行时零影响：独立脚本，不被主入口 import。
- 对 `cpa_auths/` 只做"读取 + 移动"，不修改文件内容；失败文件完整保留到 `failed_tokens/`，可重试。
- 路径相对脚本所在目录解析，与 CWD 无关。

## 9. 验证策略

- **静态**：`python -c "import ast; ast.parse(open('sub2api_upload.py',encoding='utf-8').read())"` 确认语法。
- **空跑**：`SUB2API_TOKEN` 留空时脚本应提示并退出（不报错）。
- **手动端到端**（AC8）：填入真实 token + 一个有效 `xai-*.json` → 运行 → 确认 sub2api 出现新账号、文件进入 `uploaded/`。
- 暂不写自动化单测（脚本为 I/O 密集的外部集成，人工验证更实际；如需可后续 mock _sub2api_request 补测）。

## 10. 回滚形态

- 单文件新增，回滚 = 删除 `sub2api_upload.py`。
- 已移动的文件：`uploaded/`、`failed_tokens/` 中的 `xai-*.json` 可手工移回 `cpa_auths/` 根。
- 不涉及数据库/grok2api 池/sub2api 既有数据的破坏性操作（仅向 sub2api 新增账号；如需清理，在 sub2api 后台删除对应账号即可）。
