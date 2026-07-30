# Implement — Grok 凭证批量上传到 sub2api

> 执行计划：有序清单、验证命令、review gate、rollback point。设计依据见 `design.md`，需求见 `prd.md`。

## 前置确认（Review Gate 0）

- [ ] `prd.md` / `design.md` 经用户 review 通过。
- [ ] 用户已知：`SUB2API_URL/SUB2API_TOKEN` 需自行填入脚本顶部常量。

## 实现步骤

- [ ] S0（前置·源头改造）使 `xai-*.json` 带 `password`：
  - S0.1 `cpa_xai/schema.py:build_cpa_xai_auth` 增加 `password=""` 参数；payload 增加 `"password": str(password or "")`。
  - S0.2 `cpa_xai/mint.py` 的 `build_cpa_xai_auth(...)` 调用（`mint.py:51`）增加 `password=password`。
- [ ] S1 新建 `sub2api_upload.py`（项目根）：模块 docstring（用途/用法）、`from curl_cffi import requests as curl_requests`、`import os/json/shutil/base64/sys`、常量配置区（`SUB2API_URL/SUB2API_TOKEN/GROK_PROXY_ID=1/GROK_GROUP_IDS=[5]/GROK_CONCURRENCY=10/GROK_PRIORITY=1/GROK_RATE_MULTIPLIER=1/DEFAULT_BASE_URL/AUTH_DIR/UPLOADED_DIR/FAILED_DIR`）。路径相对 `os.path.dirname(os.path.abspath(__file__))` 解析。
- [ ] S2 实现 `_decode_jwt_payload(token) -> dict`：split(".") 取中段，base64url 补齐填充，json 解析，异常返回 `{}`。
- [ ] S3 实现 `_sub2api_request(method, path, json_data=None) -> (int, dict|None)`：`curl_requests.Session()`，headers `Authorization: Bearer {SUB2API_TOKEN}`、`Content-Type/Accept: application/json`，`verify=False, timeout=30`；异常 `(-1, None)`；返回 `(resp.status_code, resp.json() if text else None)`。
- [ ] S4 实现 `refresh_grok_token(refresh_token) -> dict|None`：调 `_sub2api_request("POST", "/api/v1/admin/grok/oauth/refresh-token", {"refresh_token":..., "proxy_id": GROK_PROXY_ID})`；校验 `body` 非空且 `body.get("code")==0`，取 `body["data"]`；data 缺 `access_token` 返回 None；否则返回 data。
- [ ] S5 实现 `build_request_body(xai_data, refreshed) -> dict`：按 design §4 拼 credentials（refreshed 字段优先，缺失走 `_decode_jwt_payload(refreshed["access_token"])` 兜底；`base_url` 取 `xai_data.get("base_url") or DEFAULT_BASE_URL`）与外层 body；`name` = email 前缀 → sub → 文件名（由调用方传入兜底名）；`extra = {"password": xai_data.get("password", "")}`（json 无该字段则空串）。
- [ ] S6 实现 `_safe_move(filepath, dest_dir) -> str`：`os.makedirs(dest_dir, exist_ok=True)`；目标存在则追加 `.<n>`；`shutil.move`；返回目标路径。
- [ ] S7 实现 `upload_one(filepath) -> "ok"|"failed"`：读 JSON（异常→failed）；取 `refresh_token`（空→failed）；`refresh_grok_token`（None→failed）；`build_request_body`；`_sub2api_request("POST","/api/v1/admin/accounts", body)`；`code==0`→`_safe_move(uploaded)` 返回 ok，否则打印响应 + `_safe_move(failed)` 返回 failed；顶层 try/except 兜底 → failed。
- [ ] S8 实现 `main()`：启动检查 `SUB2API_TOKEN` 空 → 打印提示 `sys.exit(1)`；扫描 `AUTH_DIR` 下 `xai-*.json`（排除 `uploaded/failed_tokens` 子目录）；逐个 `upload_one` 计数；打印 `成功 X / 失败 Y` 汇总。`if __name__=="__main__": main()`。

## 验证命令

- [ ] V0（前置）语法检查 `python -c "import ast; ast.parse(open('cpa_xai/schema.py',encoding='utf-8').read()); ast.parse(open('cpa_xai/mint.py',encoding='utf-8').read())"`；并手动调用 `build_cpa_xai_auth(email='a@b.c', password='x', access_token='<jwt>', refresh_token='r')` 确认返回含非空 `password` 字段。
- [ ] V1 语法检查：`python -c "import ast; ast.parse(open('sub2api_upload.py',encoding='utf-8').read())"`
- [ ] V2 import 检查：`python -c "import sub2api_upload"`（确认无运行时导入错误，main 不自动执行）
- [ ] V3 空凭据早退：保持 `SUB2API_TOKEN=""`，`python sub2api_upload.py` 应打印"未配置 token"并退出码 1，不发起网络请求。
- [ ] V4 端到端（用户手动，AC8）：填入真实 `SUB2API_URL/SUB2API_TOKEN`，在 `cpa_auths/` 放一个有效 `xai-*.json`，运行 `python sub2api_upload.py`；确认：
  - 终端打印 refresh 成功 + 账号创建成功；
  - sub2api 后台出现 `platform=grok` 新账号；
  - 源文件进入 `cpa_auths/uploaded/`。

## Review Gates

- **Gate 1（实现前）**：`prd.md` + `design.md` 用户确认 → 方可 `task.py start`。
- **Gate 2（自测）**：V0–V3 通过。
- **Gate 3（交付）**：V4 用户手动验收通过。

## Rollback Points

- S0 改动回滚：`git checkout -- cpa_xai/schema.py cpa_xai/mint.py`（这两处是仅有的现有文件改动）。
- S1–S8 任一步出错：脚本尚未接入主流程，直接整体删除/还原 `sub2api_upload.py` 即可。
- V4 若误传账号到 sub2api：在 sub2api 后台删除对应账号；`uploaded/` 中文件可手工移回 `cpa_auths/` 重跑。

## 备注

- 暂不新增自动化测试（外部集成 I/O 型，人工验证更实际）；如后续需要，可 mock `_sub2api_request` 补单测。
- 与参考项目 `RegistrationMachineProject/.../sub2api_upload.py` 的差异已记录于 design §2/§7（platform/refresh 路径/成功判定均按 Grok 契约调整）。
