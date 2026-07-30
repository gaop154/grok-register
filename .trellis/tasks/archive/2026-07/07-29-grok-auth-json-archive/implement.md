# Implement — 注册成功即生成凭证 JSON

> 依据 `design.md` / `prd.md`。有序清单 + 验证 + gate + rollback。

## 前置 Review Gate 0

- [ ] `prd.md` / `design.md` 用户确认。

## 实现步骤

- [ ] S1 `cpa_xai/schema.py`：
  - S1.1 `build_cpa_xai_auth` 增加 `sso=""` 参数；payload 在 `password` 后加 `"sso": str(sso or "")`。
  - S1.2 新增 `build_basic_auth(email, password, sso) -> dict`（基础 payload，见 design §3）。
- [ ] S2 `cpa_xai/mint.py`：`mint_and_export` 增加 `sso=""` 参数；`build_cpa_xai_auth(...)` 调用增加 `sso=sso`。
- [ ] S3 `cpa_export.py`：`mint_and_export(...)` 调用（`export_cpa_xai_for_account` 内）增加 `sso=sso`（该函数已接收 `sso` 参数）。
- [ ] S4 `registration_flow.py`：
  - S4.1 `RegistrationOperations` 增加 `write_auth_json: Callable[[str,str,str], Dict[str,Any]]` 字段。
  - S4.2 `persist_account_result` 在 `persist_account_line` 块之后、`add_tokens` 块之前，独立 try/except 调用 `ops.write_auth_json(result.email, result.password, result.sso)`；异常仅 `callbacks.log` 记录。
- [ ] S5 `grok_register_ttk.py`：
  - S5.1 新增 `_write_auth_json(email, password, sso, log_callback=None)`：解析 `auth_dir`（相对项目根）、`build_basic_auth` + `write_cpa_xai_auth` 写入；返回 `{ok,path/error}`，异常记日志。
  - S5.2 `run_registration_common` 的 `RegistrationOperations(...)` 绑定 `write_auth_json=lambda e,p,s: _write_auth_json(e,p,s,log_callback=log_callback)`。
- [ ] S6 `sub2api_upload.py`：
  - S6.1 `upload_one` 读 json 后、刷新前：`refresh_token` 为空 → 打印跳过，`return "skipped"`（不移动）。
  - S6.2 `main` 增加 `skipped_count`；汇总行输出 `成功 X / 失败 Y / 跳过 Z`。

## 验证命令

- [ ] V0 语法+导入：
  ```
  python -c "import ast; [ast.parse(open(f,encoding='utf-8').read()) for f in ['cpa_xai/schema.py','cpa_xai/mint.py','cpa_export.py','registration_flow.py','grok_register_ttk.py','sub2api_upload.py']]; print('syntax ok')"
  python -c "import registration_flow, sub2api_upload; from cpa_xai import schema, mint; import cpa_export; print('import ok')"
  ```
- [ ] V1 字段：
  ```
  python -c "from cpa_xai.schema import build_basic_auth, build_cpa_xai_auth; print(build_basic_auth('a@b.c','pw','sso1').get('sso')); print(build_cpa_xai_auth(email='a@b.c',password='pw',sso='sso1',access_token='h.p.s',refresh_token='r').get('sso'))"
  ```
- [ ] V2 覆盖一致性：写基础 json 到临时目录 → 用 `build_cpa_xai_auth`+`write_cpa_xai_auth` 覆盖 → 读取确认 `sso` 保留且含 `access_token`。
- [ ] V3 跳过：在 `cpa_auths/` 放一个无 `refresh_token` 的 json，运行 `sub2api_upload.py`（配 token），确认打印"跳过"、文件留原地。
- [ ] V4（手动端到端）跑一次注册（CPA 关闭），确认 `cpa_auths/` 出现含 sso 的基础 json。

## Review Gates

- Gate1（实现前）：`prd`/`design` 确认 → `task.py start`。
- Gate2（自测）：V0–V3 通过。
- Gate3（交付）：V4 用户验收。

## Rollback Points

- S1–S6 任一步：`git checkout -- cpa_xai/schema.py cpa_xai/mint.py cpa_export.py registration_flow.py grok_register_ttk.py sub2api_upload.py`。
- 测试产生的临时 json 手工删除。
- 无外部数据破坏性操作。
