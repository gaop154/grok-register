# 执行计划：grok_search 平台上传脚本

## 步骤

1. **新建** `sub2api_grok_search_upload.py`，参照 `sub2api_upload.py` 骨架（自包含）。
2. **配置区**写死：
   - `SUB2API_URL="http://localhost:8080"`、`SUB2API_TOKEN=""`
   - `GROK_SEARCH_PROXY_ID=1`、`GROK_SEARCH_GROUP_IDS=[7]`、`GROK_SEARCH_CONCURRENCY=10`
   - `GROK_SEARCH_PRIORITY=1`、`GROK_SEARCH_RATE_MULTIPLIER=1`
   - `GROK_SEARCH_BASE_URL="https://console.x.ai"`
   - `AUTH_DIR=cpa_auths`、`UPLOADED_DIR=cpa_auths/uploaded_search`、`FAILED_DIR=cpa_auths/failed_search`
3. **通用工具**：`_sub2api_request`、`_safe_move`、`_short`（复制现有实现）。
4. **build_request_body(xai_data)**：按 design 契约拼 grok_search 请求体；`name` = email 用户名部分，空则回退文件名。
5. **upload_one(filepath)**：读 json → 无 sso 返回 `"skipped"`（留原地）→ POST accounts → `code==0` 移 uploaded_search 否则 failed_search；任何异常归 failed。
6. **main()**：TOKEN 空则 exit 1；目录不存在 exit 1；扫描 xai-*.json；循环 upload_one；汇总 `成功/失败/跳过`。

## 验证

- `python sub2api_grok_search_upload.py`（TOKEN 空）→ 打印未配置、exit 1。
- 逻辑核对要点：
  - 无 sso json → `skipped`、文件留原地、不建 failed_search。
  - 有 sso + `code==0` → uploaded_search。
  - 有 sso + `code!=0` / 网络异常 → failed_search。
- 端到端：填真实 TOKEN，对一个已含 sso 的 xai-*.json 实跑，确认 sub2api 建出 platform=grok_search 账号（待用户 V4 环境验证）。

## 审查门

- 用户确认三份文档后 `task.py start`，再进入实现。

## 回滚点

- 实现阶段纯新增单文件；不满意直接删除该文件即可，对系统零影响。
