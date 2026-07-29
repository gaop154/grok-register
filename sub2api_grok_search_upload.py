"""批量上传 cpa_auths/xai-*.json (SSO 凭证) 到 sub2api (platform=grok_search)。

流程（逐文件）：
    读取 xai-*.json -> 取 sso -> 无 sso 则跳过(留原地)
    -> 用 sso 拼 credentials -> POST /api/v1/admin/accounts (platform=grok_search)
    -> 成功归档到 cpa_auths/uploaded_search/，失败归档到 cpa_auths/failed_search/。

与 sub2api_upload.py (platform=grok) 互不干扰：独立脚本、独立归档目录。
本脚本不调用 refresh-token（SSO 直传）。

用法：
    1. 在下方配置区填入 SUB2API_URL / SUB2API_TOKEN
    2. python sub2api_grok_search_upload.py

依赖：curl_cffi（项目已有）
"""

import json
import os
import shutil
import sys

from curl_cffi import requests as curl_requests


# ================= 配置区（按需修改） =================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# sub2api 服务地址与管理员 token（请填入真实值后再运行）
SUB2API_URL = "http://localhost:8080"
SUB2API_TOKEN = ""

# sub2api 账号参数（写死，与 grok_search 接口契约一致）
GROK_SEARCH_PROXY_ID = 1
GROK_SEARCH_GROUP_IDS = [7]
GROK_SEARCH_CONCURRENCY = 10
GROK_SEARCH_PRIORITY = 1
GROK_SEARCH_RATE_MULTIPLIER = 1

# grok_search 走 console.x.ai，绕过 multi-agent 的 402
GROK_SEARCH_BASE_URL = "https://console.x.ai"

# 凭证目录与归档目录（相对脚本所在目录，与 CWD 无关）
AUTH_DIR = os.path.join(BASE_DIR, "cpa_auths")
UPLOADED_DIR = os.path.join(AUTH_DIR, "uploaded_search")
FAILED_DIR = os.path.join(AUTH_DIR, "failed_search")


# ================= 通用工具 =================

def _sub2api_request(method, path, json_data=None):
    """通用 sub2api 请求封装。

    返回 (http_status, body_json|None)；网络异常返回 (-1, None)。
    """
    url = SUB2API_URL.rstrip("/") + path
    session = None
    try:
        session = curl_requests.Session()
        resp = session.request(
            method,
            url,
            json=json_data,
            headers={
                "Authorization": "Bearer %s" % SUB2API_TOKEN,
                "Content-Type": "application/json",
                "Accept": "application/json, text/plain, */*",
            },
            verify=False,
            timeout=30,
        )
        body = None
        if getattr(resp, "text", ""):
            try:
                body = resp.json()
            except Exception:
                body = None
        return resp.status_code, body
    except Exception as exc:
        print("  [!] 请求异常 %s %s: %s" % (method, path, exc))
        return -1, None
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass


def _short(value, limit=300):
    try:
        text = json.dumps(value, ensure_ascii=False)
    except Exception:
        text = str(value)
    return text[:limit]


# ================= 构造请求体 =================

def build_request_body(xai_data, fallback_name=""):
    """按 sub2api grok_search 账号契约构造 POST /api/v1/admin/accounts 请求体。

    type=apikey，credentials={sso_token, base_url}，extra 含 chat_completions/email/password。
    """
    email = str(xai_data.get("email") or "").strip()
    if email:
        name = email.split("@")[0]
    else:
        name = fallback_name

    credentials = {
        "sso_token": str(xai_data.get("sso") or ""),
        "base_url": GROK_SEARCH_BASE_URL,
    }

    return {
        "name": name,
        "notes": "",
        "platform": "grok_search",
        "type": "apikey",
        "credentials": credentials,
        "extra": {
            "grok_search_chat_completions": True,
            "email": email,
            "password": str(xai_data.get("password") or ""),
        },
        "proxy_id": GROK_SEARCH_PROXY_ID,
        "concurrency": GROK_SEARCH_CONCURRENCY,
        "priority": GROK_SEARCH_PRIORITY,
        "rate_multiplier": GROK_SEARCH_RATE_MULTIPLIER,
        "load_factor": None,
        "group_ids": GROK_SEARCH_GROUP_IDS,
        "auto_pause_on_expired": True,
    }


# ================= 文件归档 =================

def _safe_move(filepath, dest_dir):
    """移动文件到 dest_dir；目标重名则追加 .<n> 后缀，避免覆盖。"""
    os.makedirs(dest_dir, exist_ok=True)
    base = os.path.basename(filepath)
    target = os.path.join(dest_dir, base)
    if os.path.exists(target):
        name, ext = os.path.splitext(base)
        index = 1
        while os.path.exists(target):
            target = os.path.join(dest_dir, "%s.%d%s" % (name, index, ext))
            index += 1
    shutil.move(filepath, target)
    return target


# ================= 单文件上传编排 =================

def upload_one(filepath):
    """处理单个 xai-*.json：读 -> 取 sso -> 构造 -> 上传 -> 归档。

    返回 "ok" / "failed" / "skipped"；任何异常都归入 failed_search/，不抛出。
    无 sso 时返回 "skipped"，文件留原地（等待补全，下一轮再传）。
    """
    filename = os.path.basename(filepath)
    fallback_name = os.path.splitext(filename)[0]
    try:
        try:
            with open(filepath, "r", encoding="utf-8") as handle:
                xai_data = json.load(handle)
        except Exception as exc:
            print("  [!] %s 读取/解析失败: %s" % (filename, exc))
            _safe_move(filepath, FAILED_DIR)
            return "failed"

        sso = str(xai_data.get("sso") or "").strip()
        if not sso:
            print("  [~] %s 无 sso，跳过（等待补全）" % filename)
            return "skipped"

        request_body = build_request_body(xai_data, fallback_name=fallback_name)
        status, resp = _sub2api_request("POST", "/api/v1/admin/accounts", request_body)
        if status == -1:
            print("  [!] %s 上传网络异常，归入 failed_search" % filename)
            _safe_move(filepath, FAILED_DIR)
            return "failed"
        if not resp or resp.get("code") != 0:
            print("  [!] %s 上传失败: HTTP %s body=%s" % (filename, status, _short(resp)))
            _safe_move(filepath, FAILED_DIR)
            return "failed"

        data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
        account_id = data.get("id")
        print("  [+] %s 上传成功 (account_id=%s)" % (filename, account_id))
        _safe_move(filepath, UPLOADED_DIR)
        return "ok"
    except Exception as exc:
        print("  [!] %s 处理异常: %s" % (filename, exc))
        try:
            if os.path.exists(filepath):
                _safe_move(filepath, FAILED_DIR)
        except Exception:
            pass
        return "failed"


# ================= 入口 =================

def main():
    if not SUB2API_TOKEN:
        print("[!] 未配置 SUB2API_TOKEN，请在脚本顶部填入后重试。")
        sys.exit(1)
    if not os.path.isdir(AUTH_DIR):
        print("[!] 凭证目录不存在: %s" % AUTH_DIR)
        sys.exit(1)

    files = []
    for name in sorted(os.listdir(AUTH_DIR)):
        full = os.path.join(AUTH_DIR, name)
        if os.path.isfile(full) and name.startswith("xai-") and name.endswith(".json"):
            files.append(full)

    if not files:
        print("[*] 未发现待上传的 xai-*.json (%s)" % AUTH_DIR)
        return

    print("=" * 60)
    print("[*] 待上传 %d 个，目标 %s (platform=grok_search)" % (len(files), SUB2API_URL))
    print("=" * 60)

    ok_count = 0
    failed_count = 0
    skipped_count = 0
    for filepath in files:
        result = upload_one(filepath)
        if result == "ok":
            ok_count += 1
        elif result == "skipped":
            skipped_count += 1
        else:
            failed_count += 1

    print("=" * 60)
    print("[*] 完成: 成功 %d / 失败 %d / 跳过 %d" % (ok_count, failed_count, skipped_count))
    print("[*] 成功归档: %s" % UPLOADED_DIR)
    print("[*] 失败归档: %s" % FAILED_DIR)
    print("=" * 60)


if __name__ == "__main__":
    main()
