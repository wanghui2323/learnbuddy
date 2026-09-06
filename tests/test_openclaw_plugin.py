"""OpenClaw 插件包静态合同测试（真实 Gateway 运行留到联调门）。"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent / "integrations" / "openclaw-learnbuddy"
PROJECT_ROOT = ROOT.parent.parent


def test_openclaw_plugin_manifest_and_package_are_consistent():
    manifest = json.loads((ROOT / "openclaw.plugin.json").read_text(encoding="utf-8"))
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

    assert manifest["id"] == "learnbuddy"
    assert manifest["contracts"]["tools"] == ["learnbuddy_bind"]
    assert package["openclaw"]["extensions"] == ["./index.js"]
    assert "typebox" in package["dependencies"]
    assert (ROOT / "index.js").is_file()


def test_openclaw_plugin_uses_trusted_requester_and_scoped_mcp():
    source = (ROOT / "index.js").read_text(encoding="utf-8")
    config = (ROOT / "openclaw.example.json5").read_text(encoding="utf-8")

    assert "ctx.requesterSenderId" in source
    assert "registerMcpServerConnectionResolver" in source
    assert 'serverName: MCP_SERVER_NAME' in source
    assert '"/api/integrations/openclaw/token"' in source
    assert '"/api/integrations/openclaw/bind"' in source
    assert "user_id" not in source
    assert 'dmScope: "per-account-channel-peer"' in config
    assert "dynamicAgentCreation: { enabled: false }" in config
    for dangerous in ("exec", "process", "write", "edit", "apply_patch"):
        assert f'"{dangerous}"' in config


def test_openclaw_runtime_pin_matches_requester_scoped_plugin_floor():
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    env_example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    setup = (PROJECT_ROOT / "scripts/setup_openclaw.sh").read_text(encoding="utf-8")

    assert package["peerDependencies"]["openclaw"] == ">=2026.8.2"
    assert package["openclaw"]["compat"] == {
        "pluginApi": ">=2026.8.2",
        "minGatewayVersion": "2026.8.2",
    }
    for source in (compose, env_example, setup):
        assert "ghcr.io/openclaw/openclaw:2026.8.2" in source
    assert "OPENCLAW_FEISHU_PLUGIN_VERSION=2026.8.2" in env_example
