# 注册成功即生成凭证 JSON（sso + CPA 补全 OIDC）

## Goal

把账号凭证 JSON 的生成时机从"CPA 拿到 refresh_token"提前到"注册成功（拿到 sso）"：注册成功即在 `cpa_auths/` 写入含 `email/password/sso` 的基础 json；CPA 成功后将完整 OIDC 字段补全到**同一文件**并保留 `sso`；CPA 关闭/失败时基础 json 仍保留。同时让 sub2api 上传脚本跳过尚未补全（无 refresh_token）的 json，避免误判失败。

## Background

- 当前注册成功后流程（`registration_flow.persist_account_result`）：`persist_account_line`(写 accounts) → `add_tokens`(入池) → `export_cpa`（仅 CPA 成功才在 `cpa_auths/` 生成 `xai-*.json`）。
- 因此 CPA 关闭/失败时账号虽注册成功却无 json 归档；且现有 `xai-*.json` 不含 `sso`。
- `sso` 在注册时由 `wait_for_sso_cookie` 获得，注册成功时即已知；CPA 导出链路（`cpa_export.export_cpa_xai_for_account`）也已持有 `sso` 参数但未写入 json。

## Requirements

### 功能需求

- R1 注册成功（拿到 sso/email/password）后，立即在 `cpa_auths/` 写入基础 json，字段至少含 `type/email/password/sso`；该写入**不依赖 CPA 开关**（`cpa_export_enabled=False` 时也生成）。
- R2 CPA 成功（拿到 OIDC）后，用完整字段（`access_token/refresh_token/token_type/expires_in/expired/sub/base_url/redirect_uri/token_endpoint/auth_kind/id_token`）覆盖更新**同一** json，且**保留 sso**（需求 1：以后生成的 json 都自动带 sso）。
- R3（需求 1·源头）`cpa_xai/schema.build_cpa_xai_auth` 增加 `sso` 参数并写入 payload；`mint`/`cpa_export` 链路传入 sso。
- R4 文件命名一致：基础 json 与 CPA 完整 json 同名（`xai-<email>.json`），CPA 写入覆盖基础（`cpa_xai/writer` 用 `os.replace` 原子覆盖）。
- R5（Q3）`sub2api_upload.py` 遇到**无 refresh_token**的 json（尚未被 CPA 补全）→ 跳过：不移动、不移 `failed_tokens`，留原地等待补全后下一轮上传；打印跳过提示并在汇总输出跳过计数。
- R6 基础 json 写入异常不得中断注册主流程（与 `persist_account_line` 同级容错：失败记日志、继续）。

### 约束

- N1 不破坏现有注册流程与统计（`success_count/fail_count` 语义不变）。
- N2 基础 json 与完整 json 共用 `cpa_xai` 的原子写入工具（`write_cpa_xai_auth`），保证崩溃安全。
- N3 仅依赖现有库（`curl_cffi`/`filelock` 等）。
- N4 不修改 sub2api 接口契约；仅调整本地脚本对无 rt json 的处理策略。

## Acceptance Criteria

- [ ] AC1 注册成功后（无论 `cpa_export_enabled` True/False），`cpa_auths/` 立即出现 `xai-<email>.json`，含非空 `email/password/sso`。
- [ ] AC2 CPA 成功后，同一 json 含完整 OIDC 字段（access_token/refresh_token 等）且仍含 `sso`。
- [ ] AC3 `build_cpa_xai_auth(..., sso='x')` 输出含 `"sso"` 字段。
- [ ] AC4 `sub2api_upload.py` 遇无 refresh_token 的 json 打印"跳过"且文件留原地；有 rt 的正常上传。
- [ ] AC5 基础 json 写入抛异常时注册继续，账号仍入 accounts/池。
- [ ] AC6 现有注册回归不破（语法/导入通过）。

## Out of Scope

- sub2api 上传本身（已在 `grok-sub2api-upload` 任务实现；本任务仅调整其无 rt 处理）。
- 回补历史 json 的 sso（用户明确不需要回补）。
- GUI/CLI 新增配置开关（基础 json 生成是无条件的，不暴露开关）。
