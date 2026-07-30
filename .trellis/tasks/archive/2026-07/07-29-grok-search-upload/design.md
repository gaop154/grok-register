# 设计：grok_search 平台上传脚本

## 边界

- **新增** `sub2api_grok_search_upload.py`（脚本根目录，与 `sub2api_upload.py` 并列）。
- **不改动** `sub2api_upload.py`、`cpa_xai/*`、注册流程任何文件。
- 输入：`cpa_auths/xai-*.json`（共享数据契约，只读）。
- 输出：sub2api 账号 + `cpa_auths/uploaded_search/`、`cpa_auths/failed_search/` 归档。

## 与 sub2api_upload.py（platform=grok）的差异

| 维度 | grok（现有） | grok_search（本任务） |
|------|--------------|----------------------|
| 平台 | platform=grok | platform=grok_search |
| 鉴权类型 | type=oauth | type=apikey |
| 凭证来源 | refresh_token 刷新得 access_token | 直接用 sso |
| credentials | access_token/refresh_token/client_id/... | {sso_token, base_url} |
| base_url | cli-chat-proxy.grok.com/v1 | console.x.ai |
| 刷新接口 | 调 grok/oauth/refresh-token | **无** |
| extra | {password} | {grok_search_chat_completions, email, password} |
| group_ids | [5] | [7] |
| 归档目录 | uploaded/ / failed_tokens/ | uploaded_search/ / failed_search/ |

## 数据流

```
xai-*.json --(读 sso)--> 无 sso? --yes--> skip(留原地)
                              |
                              no
                              v
POST /api/v1/admin/accounts (platform=grok_search, type=apikey)
                              |
              body.code==0 ? --yes--> uploaded_search/
                              |
                              no / 网络异常
                              v
                        failed_search/
```

## 契约：POST /api/v1/admin/accounts

鉴权：`Authorization: Bearer {SUB2API_TOKEN}`

请求体：
```json
{
  "name": "<email 用户名部分>",
  "notes": "",
  "platform": "grok_search",
  "type": "apikey",
  "credentials": {"sso_token": "<json.sso>", "base_url": "https://console.x.ai"},
  "extra": {"grok_search_chat_completions": true, "email": "<json.email>", "password": "<json.password>"},
  "proxy_id": 1,
  "concurrency": 10,
  "priority": 1,
  "rate_multiplier": 1,
  "load_factor": null,
  "group_ids": [7],
  "auto_pause_on_expired": true
}
```

成功响应：`{"code": 0, "data": {"id": <account_id>, ...}}`。失败：`code != 0`。

## 模块结构（自包含，不跨文件 import）

- 配置区：常量（URL/TOKEN/代理/分组/base_url/目录）。
- `_sub2api_request(method, path, json_data)` → `(http_status, body|None)`，网络异常返回 `(-1, None)`。
- `build_request_body(xai_data)` → grok_search 账号请求体。
- `_safe_move(filepath, dest_dir)` → 重名加 `.<n>` 后缀。
- `_short(value)` → 截断 JSON 字符串用于日志。
- `upload_one(filepath)` → `"ok"|"failed"|"skipped"`。
- `main()` → 校验配置/目录、扫描、循环上传、汇总。

> 不 import `sub2api_upload`：两个脚本各自自包含、可独立分发运行（与现有 grok 上传脚本风格一致）。

## 兼容性 / 回滚

- 纯新增文件，删除即回滚，对系统零影响。
- 与 grok 上传互不干扰（独立归档目录、独立脚本入口）。

## 取舍

- **独立脚本 vs 复用同一脚本加平台参数**：用户明确选独立脚本（Q1=b），避免改动已验证的 grok 上传路径，降低回归风险。
- **不跨文件复用工具函数**：脚本自包含，便于单独部署；代价是 `_sub2api_request`/`_safe_move` 有少量重复（与现有脚本风格一致，可接受）。
