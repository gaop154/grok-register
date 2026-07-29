"""批量上传 cpa_auths/xai-*.json (xAI OIDC 凭证) 到 sub2api (platform=grok)。

流程（逐文件）：
    读取 xai-*.json -> 调 /api/v1/admin/grok/oauth/refresh-token 刷新
    -> 用刷新响应拼 credentials -> POST /api/v1/admin/accounts (platform=grok)
    -> 成功归档到 cpa_auths/uploaded/，失败归档到 cpa_auths/failed_tokens/。

用法：
    1. 在下方配置区填入 SUB2API_URL / SUB2API_TOKEN
    2. python sub2api_upload.py

依赖：curl_cffi（项目已有）
"""

import base64
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

# sub2api 账号参数（写死，与接口抓包一致）
GROK_PROXY_ID = 1
GROK_GROUP_IDS = [5]
GROK_CONCURRENCY = 10
GROK_PRIORITY = 1
GROK_RATE_MULTIPLIER = 1

# xAI 凭证默认 base_url
DEFAULT_BASE_URL = "https://cli-chat-proxy.grok.com/v1"

# 凭证目录与归档目录（相对脚本所在目录，与 CWD 无关）
AUTH_DIR = os.path.join(BASE_DIR, "cpa_auths")
UPLOADED_DIR = os.path.join(AUTH_DIR, "uploaded")
FAILED_DIR = os.path.join(AUTH_DIR, "failed_tokens")


# ================= 通用工具 =================

def _decode_jwt_payload(token):
    """解码 JWT 的 payload 段，失败返回 {}。仅作字段兜底用。"""
    try:
        parts = str(token or "").split(".")
        if len(parts) < 2:
            return {}
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
        return json.loads(decoded)
    except Exception:
        return {}


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


# ================= 刷新 token =================

def refresh_grok_token(refresh_token):
    """调用 sub2api 的 grok refresh-token 接口，返回 data dict 或 None。"""
    status, body = _sub2api_request(
        "POST",
        "/api/v1/admin/grok/oauth/refresh-token",
        {"refresh_token": refresh_token, "proxy_id": GROK_PROXY_ID},
    )
    if not body or body.get("code") != 0:
        print("  [!] 刷新失败: HTTP %s body=%s" % (status, _short(body)))
        return None
    data = body.get("data") or {}
    if not data.get("access_token"):
        print("  [!] 刷新响应缺少 access_token: %s" % _short(data))
        return None
    return data


# ================= 构造请求体 =================

def build_request_body(xai_data, refreshed, fallback_name=""):
    """按 sub2api grok 账号契约构造 POST /api/v1/admin/accounts 请求体。

    credentials 字段优先取刷新响应，缺失时从 access_token 的 JWT 兜底解码。
    """
    access_token = refreshed.get("access_token", "")
    jwt = _decode_jwt_payload(access_token)

    credentials = {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_at": refreshed.get("expires_at") or jwt.get("exp"),
        "client_id": refreshed.get("client_id") or jwt.get("aud"),
        "scope": refreshed.get("scope") or jwt.get("scope"),
        "sub": refreshed.get("sub") or jwt.get("sub"),
        "team_id": refreshed.get("team_id") or jwt.get("team_id"),
        "base_url": xai_data.get("base_url") or DEFAULT_BASE_URL,
        "refresh_token": refreshed.get("refresh_token", ""),
    }

    email = str(xai_data.get("email") or "").strip()
    if email:
        name = email.split("@")[0]
    else:
        name = refreshed.get("sub") or fallback_name

    return {
        "name": name,
        "notes": "",
        "platform": "grok",
        "type": "oauth",
        "credentials": credentials,
        "extra": {"password": str(xai_data.get("password") or "")},
        "proxy_id": GROK_PROXY_ID,
        "concurrency": GROK_CONCURRENCY,
        "priority": GROK_PRIORITY,
        "rate_multiplier": GROK_RATE_MULTIPLIER,
        "group_ids": GROK_GROUP_IDS,
        "expires_at": None,
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


def _short(value, limit=300):
    try:
        text = json.dumps(value, ensure_ascii=False)
    except Exception:
        text = str(value)
    return text[:limit]


# ================= 单文件上传编排 =================

def upload_one(filepath):
    """处理单个 xai-*.json：读 -> 刷新 -> 构造 -> 上传 -> 归档。

    返回 "ok" 或 "failed"；任何异常都归入 failed_tokens/，不抛出。
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

        refresh_token = str(xai_data.get("refresh_token") or "").strip()
        if not refresh_token:
            print("  [~] %s 无 refresh_token，跳过（等待 CPA 补全）" % filename)
            return "skipped"

        refreshed = refresh_grok_token(refresh_token)
        if not refreshed:
            print("  [!] %s 刷新 token 失败，归入 failed_tokens" % filename)
            _safe_move(filepath, FAILED_DIR)
            return "failed"

        request_body = build_request_body(xai_data, refreshed, fallback_name=fallback_name)
        status, resp = _sub2api_request("POST", "/api/v1/admin/accounts", request_body)
        if status == -1:
            print("  [!] %s 上传网络异常，归入 failed_tokens" % filename)
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
    print("[*] 待上传 %d 个，目标 %s" % (len(files), SUB2API_URL))
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
