# Design — 注册成功即生成凭证 JSON

> 配套 `prd.md`。聚焦边界、数据流、契约、权衡、回滚。

## 1. 边界（改动文件）

- `cpa_xai/schema.py`：`build_cpa_xai_auth` 加 `sso` 参数；新增 `build_basic_auth`。
- `cpa_xai/mint.py`：`mint_and_export` 加 `sso` 参数，传入 `build_cpa_xai_auth`。
- `cpa_export.py`：调用 `mint_and_export` 时传 `sso=sso`（该函数已有 `sso` 参数）。
- `registration_flow.py`：`RegistrationOperations` 加 `write_auth_json`；`persist_account_result` 插入调用。
- `grok_register_ttk.py`：`run_registration_common` 绑定 `write_auth_json`。
- `sub2api_upload.py`：`upload_one` 无 rt 跳过；`main` 统计 skipped。

不改动 sub2api 接口契约、不改 GUI 控件、不改 cpa_xai 的 mint 浏览器逻辑。

## 2. 数据流（改造后）

```
register_one_account 成功(拿到 sso)
  → persist_account_result:
      persist_account_line(写 accounts)        # 现有
      write_auth_json(email,password,sso)      # 新增：立即写基础 json → cpa_auths/xai-<email>.json
      add_tokens(入池)                          # 现有
      export_cpa(email,password,sso):          # 现有
          CPA 成功 → mint_and_export → build_cpa_xai_auth(含 sso) → write_cpa_xai_auth 覆盖同一 json（补全 OIDC + sso）
          CPA 关闭/失败 → 基础 json 保留
```

## 3. 基础 json（build_basic_auth）

```python
def build_basic_auth(email, password, sso):
    return {
        "type": "xai",
        "email": str(email or ""),
        "password": str(password or ""),
        "sso": str(sso or ""),
        "last_refresh": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
```

- `type` 沿用 `"xai"`，与 CPA 完整 json 一致；CPA 覆盖后字段补全为完整 xai 凭证。
- 写入复用 `writer.write_cpa_xai_auth`（原子 `os.replace`，权限 0o600），`filename=credential_file_name(email)`。
- 目录用 `config["cpa_auth_dir"]`（默认 `./cpa_auths`），相对路径相对项目根解析（与 `cpa_export.CpaExportSettings.auth_dir` 一致）。

## 4. sso 源头（需求 1）

- `schema.build_cpa_xai_auth` 增加 `sso=""` 参数，payload 在 `password` 后加 `"sso": str(sso or "")`。
- `mint.mint_and_export` 增加 `sso=""` 参数，传入 `build_cpa_xai_auth(..., sso=sso)`。
- `cpa_export.export_cpa_xai_for_account` 已有 `sso` 参数，调用 `mint_and_export` 时传 `sso=sso`。

## 5. ops 接口与主流程

- `RegistrationOperations` 增加 `write_auth_json: Callable[[str,str,str], Dict[str,Any]]`。
- `persist_account_result`：在 `persist_account_line` 块之后、`add_tokens` 块之前，独立 try/except 调用 `ops.write_auth_json(email,password,sso)`；失败仅记日志，不影响后续 `add_tokens`/`export_cpa` 与统计。
- `grok_register_ttk.run_registration_common` 绑定：
  ```python
  write_auth_json=lambda e,p,s: _write_auth_json(e, p, s, log_callback=log_callback)
  ```
- `_write_auth_json(email,password,sso,log_callback)`：
  - 解析 `auth_dir = config.get("cpa_auth_dir","./cpa_auths")`，相对路径相对项目根。
  - `from cpa_xai.schema import build_basic_auth, credential_file_name`
  - `from cpa_xai.writer import write_cpa_xai_auth`
  - `path = write_cpa_xai_auth(auth_dir, build_basic_auth(email,password,sso))`
  - 返回 `{"ok": True, "path": str(path)}`；异常返回 `{"ok": False, "error": str(exc)}` 并记日志。

## 6. sub2api_upload.py 无 rt 跳过（Q3）

- `upload_one` 读 json 后、刷新前：
  ```python
  refresh_token = str(xai_data.get("refresh_token") or "").strip()
  if not refresh_token:
      print("  [~] %s 无 refresh_token，跳过（等待 CPA 补全）" % filename)
      return "skipped"
  ```
- `main` 增加 `skipped_count`，汇总输出 `成功 X / 失败 Y / 跳过 Z`。
- 跳过的文件**不移动**，留原地。

## 7. 命名与覆盖一致性

- 基础与完整 json 均用 `credential_file_name(email)` → `xai-<email>.json`（email 非空时）。
- `writer.write_cpa_xai_auth` 用 `os.replace` 覆盖目标 → CPA 写入安全覆盖基础。
- 单账号串行处理，无并发覆盖；多账号不同 email 不同文件。

## 8. 容错

- `write_auth_json` 异常：记日志、返回 `{ok:False}`，不阻断 `persist_account_result` 后续步骤。
- 与 `persist_account_line` 失败处理同构：账号已注册，归档失败不丢账号（accounts/池仍写）。

## 9. 验证策略

- V0 语法/导入：`schema.py/mint.py/cpa_export.py/registration_flow.py/grok_register_ttk.py/sub2api_upload.py`。
- V1 `build_basic_auth` 输出含 sso；`build_cpa_xai_auth(...,sso='x')` 输出含 sso。
- V2 覆盖一致性：写基础 json → 用 `build_cpa_xai_auth`+`write_cpa_xai_auth` 覆盖 → 确认 sso 保留且 OIDC 字段补全。
- V3 `sub2api_upload.py` 遇无 rt json 跳过且留原地。
- V4（手动端到端）跑一次注册（CPA 关闭），确认 `cpa_auths/` 出现含 sso 的基础 json。

## 10. 回滚

- 各文件 `git checkout` 还原（schema/mint/cpa_export/registration_flow/grok_register_ttk/sub2api_upload）。
- 测试生成的临时 json 可手工删除。
- 不涉及外部数据破坏。
