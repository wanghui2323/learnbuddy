#!/usr/bin/env python3
"""iTutor MCP Server（Batch L · ADR-011）。

把 `core/tools.py` 的统一工具注册表，按 **MCP（Model Context Protocol）** 暴露给
外部 agent（OpenClaw / Claude Desktop 等）。这是 ADR-010「不 fork OpenClaw，用 skill
包对接」+ ADR-011「工具注册层是公共地基」的落地件。

实现说明：
- 官方 `mcp` SDK 需 Python ≥3.10，本项目 venv 是 3.9 → 这里用**纯标准库**实现一个
  spec 兼容的 MCP stdio 服务（JSON-RPC 2.0 over stdio，行分隔）。升级到 3.10+ 后可
  平滑替换为官方 SDK（协议一致）。

用户身份：本文件保留 `ITUTOR_MCP_USER_EMAIL` 的本地 stdio 单用户兼容模式；
2C OpenClaw 通道走 `server.py /api/mcp`，用短期签名 token 在服务端注入 user_id。

运行：
    ITUTOR_MCP_USER_EMAIL=you@example.com python mcp_server.py
（由 MCP 客户端以子进程方式拉起，stdin/stdout 走协议，stderr 打日志）
"""

from __future__ import annotations

import json
import os
import sys

from core.auth import get_user_by_email
from core.db import init_db
from core.tools import ToolError, call_tool, list_tools

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "learnbuddy", "version": "0.2.0"}

_TYPE_MAP = {"string": "string", "integer": "integer", "array": "array", "object": "object", "number": "number", "boolean": "boolean"}


def log(*a):
    print("[itutor-mcp]", *a, file=sys.stderr, flush=True)


def _resolve_user_id():
    email = os.getenv("ITUTOR_MCP_USER_EMAIL", "").strip()
    if not email:
        log("警告：未设置 ITUTOR_MCP_USER_EMAIL，工具调用会报错。")
        return None
    u = get_user_by_email(email)
    if u is None:
        log(f"警告：找不到用户 {email}（请先在 iTutor 注册该邮箱）。")
        return None
    log(f"已绑定用户 {email} (id={u.id})")
    return u.id


def _input_schema(params: dict) -> dict:
    props, required = {}, []
    for name, spec in (params or {}).items():
        props[name] = {"type": _TYPE_MAP.get(spec.get("type", "string"), "string"),
                       "description": spec.get("desc", "")}
        if spec.get("required"):
            required.append(name)
    schema = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


def _mcp_tools() -> list:
    out = []
    for t in list_tools():
        desc = t["description"] + ("（会写数据）" if t.get("mutates") else "")
        out.append({"name": t["name"], "description": desc, "inputSchema": _input_schema(t["params"])})
    return out


def handle(req: dict, user_id) -> dict | None:
    """处理一条 JSON-RPC 请求；通知（无 id）返回 None（不回包）。"""
    method = req.get("method")
    rid = req.get("id")
    is_notification = "id" not in req

    def ok(result):
        return None if is_notification else {"jsonrpc": "2.0", "id": rid, "result": result}

    def err(code, message):
        return None if is_notification else {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}

    if method == "initialize":
        client_ver = (req.get("params") or {}).get("protocolVersion") or PROTOCOL_VERSION
        return ok({"protocolVersion": client_ver, "capabilities": {"tools": {}}, "serverInfo": SERVER_INFO})

    if method in ("notifications/initialized", "initialized"):
        return None

    if method == "ping":
        return ok({})

    if method == "tools/list":
        return ok({"tools": _mcp_tools()})

    if method == "tools/call":
        params = req.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        if user_id is None:
            return ok({"content": [{"type": "text", "text": "错误：MCP 未绑定用户（设置 ITUTOR_MCP_USER_EMAIL）"}], "isError": True})
        try:
            result = call_tool(name, user_id=user_id, args=args)
            text = json.dumps(result, ensure_ascii=False, indent=2)
            return ok({"content": [{"type": "text", "text": text}]})
        except ToolError as e:
            return ok({"content": [{"type": "text", "text": f"工具错误：{e}"}], "isError": True})
        except Exception as e:  # noqa: BLE001
            log(f"tool {name} 异常: {type(e).__name__}: {e}")
            return ok({"content": [{"type": "text", "text": "执行异常，请稍后重试"}], "isError": True})

    return err(-32601, f"Method not found: {method}")


def main():
    init_db()
    user_id = _resolve_user_id()
    log(f"iTutor MCP server 启动 · {len(list_tools())} 个工具 · 协议 {PROTOCOL_VERSION}")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            log("收到非法 JSON，忽略")
            continue
        try:
            resp = handle(req, user_id)
        except Exception as e:  # noqa: BLE001
            log(f"handle 异常: {e}")
            resp = {"jsonrpc": "2.0", "id": req.get("id"), "error": {"code": -32603, "message": str(e)}}
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
