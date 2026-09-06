#!/usr/bin/env python3
"""交互式创建 LearnBuddy personal 的唯一 owner，密码只进入当次 HTTPS/localhost 请求。"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
import urllib.error
import urllib.parse
import urllib.request


def _read_error(exc: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(exc.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return f"HTTP {exc.code}"
    return str(payload.get("detail") or payload.get("error") or f"HTTP {exc.code}")


def validate_base_url(value: str) -> str:
    """只允许 HTTPS，或精确的本机 HTTP，避免把 owner 密码发给伪 localhost。"""
    raw = (value or "").strip().rstrip("/")
    try:
        parsed = urllib.parse.urlsplit(raw)
        _ = parsed.port  # 显式触发非法端口校验。
    except ValueError as exc:
        raise ValueError("base URL 格式不正确") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("base URL 必须使用 HTTPS 或本机 HTTP")
    if parsed.username or parsed.password:
        raise ValueError("base URL 不允许包含用户名或密码")
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("base URL 只能指向 LearnBuddy 服务根地址")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("拒绝通过非 localhost 明文 HTTP 发送 owner 密码")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def main() -> int:
    parser = argparse.ArgumentParser(description="初始化 LearnBuddy personal 唯一 owner")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()

    try:
        base_url = validate_base_url(args.base_url)
    except ValueError as exc:
        print(f"拒绝初始化：{exc}。", file=sys.stderr)
        return 2

    email = input("Owner 邮箱：").strip()
    name = input("Owner 名称（可留空）：").strip()
    password = getpass.getpass("Owner 密码（至少 6 位）：")
    password_again = getpass.getpass("再次输入密码：")
    if password != password_again:
        print("两次密码不一致。", file=sys.stderr)
        return 2

    request = urllib.request.Request(
        f"{base_url}/api/auth/register",
        data=json.dumps({"email": email, "name": name, "password": password}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(f"初始化失败：{_read_error(exc)}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"无法连接 LearnBuddy：{exc.reason}", file=sys.stderr)
        return 1

    user = payload.get("user") or {}
    print(f"✓ Owner 已创建：{user.get('email', email)}")
    print("请打开 /login 登录；此脚本不保存密码或会话 cookie。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
