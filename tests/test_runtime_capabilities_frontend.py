"""双版本前端入口的静态产品合同。"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
COMMON = (ROOT / "web/common.js").read_text(encoding="utf-8")
INDEX = (ROOT / "web/index.html").read_text(encoding="utf-8")
LOGIN = (ROOT / "web/login.html").read_text(encoding="utf-8")
SPACE = (ROOT / "web/space.html").read_text(encoding="utf-8")
INSTANCE = (ROOT / "web/instance.html").read_text(encoding="utf-8")


def test_common_runtime_adapter_uses_backend_contract_and_legacy_fallback():
    assert "'/api/runtime-capabilities'" in COMMON
    assert "['cloud', 'personal']" in COMMON
    assert "['enabled', 'setup_only', 'disabled']" in COMMON
    assert "quota_mode" in COMMON
    assert "llm_credentials" in COMMON
    assert "llm_configured" in COMMON
    assert "source: 'fallback'" in COMMON
    assert "runtime capabilities" in COMMON
    assert "['http:', 'https:']" in COMMON


def test_cloud_entry_exposes_quota_source_and_private_deploy_ctas():
    assert 'id="runtimeQuota"' in SPACE
    assert 'id="spaceGithubLink"' in SPACE
    assert 'id="spaceDeployLink"' in SPACE
    assert "在线体验 · 免费额度有限" in INDEX
    assert "部署自己的 LearnBuddy" in INDEX
    assert "fetch('/api/usage?lite=1'" in INDEX
    assert "runtime.edition === 'cloud'" in INDEX
    assert 'id="feishuIntegrationSection" hidden' in SPACE
    assert 'id="reminderIntegrationSection" hidden' in SPACE
    assert "feishuSection.hidden = true" in SPACE
    assert "reminderSection.hidden = true" in SPACE
    assert "feishuSection.style.display = 'none'" in SPACE
    assert "reminderSection.style.display = 'none'" in SPACE


def test_personal_entry_is_owner_only_byok_and_does_not_claim_agent_connected():
    open_account = SPACE.split("function openAccount(){", 1)[1].split("function closeAccount()", 1)[0]
    personal_branch = open_account.split("RUNTIME_CAPABILITIES.edition === 'personal'", 1)[1].split("} else if", 1)[0]

    assert "runtimeRegistration === 'setup_only' && runtimeSetupRequired" in LOGIN
    assert "这是私人部署，仅已配置的所有者可以登录" in LOGIN
    assert "请先在本地 .env 配置模型（BYOK）" in SPACE
    assert "managedBinding.style.display = ''" in SPACE
    assert "RUNTIME_CAPABILITIES.edition === 'personal'" in SPACE
    assert "loadFeishuIntegration();" in personal_branch
    assert "loadReminderPolicy();" in personal_branch
    assert "function loadReminderPolicy()" in SPACE
    assert "fetch('/api/reminders/policy', {cache:'no-store'})" in SPACE
    assert "window.__feishuBindingCode = ''" in SPACE
    assert "当前版本支持配置飞书与 OpenClaw" in SPACE
    assert "只有真实消息往返后，才能确认连接成功" in SPACE
    assert "连接能力已启用" not in SPACE


def test_personal_entry_hides_platform_quota_and_keeps_export_semantics_honest():
    assert "RUNTIME_CAPABILITIES.quota_mode !== 'platform'" in SPACE
    assert "RUNTIME_CAPABILITIES.edition === 'personal'" in SPACE
    assert "RUNTIME_CAPABILITIES.admin_console" in SPACE
    assert "完整备份与恢复请使用部署脚本" in SPACE
    assert "accountExportTitle').textContent = '导出我的学习数据'" in SPACE
    assert "备份我的学习数据" not in SPACE
    assert "这只会永久删除当前 LearnBuddy 部署中的账号" in SPACE
    assert "OpenClaw 会话、OpenClaw 凭据和飞书应用凭据不会自动删除" in SPACE
    assert "需按部署指南单独清理" in SPACE
    assert "const personalCleanup = RUNTIME_CAPABILITIES.edition === 'personal'" in SPACE
    assert "runtime.edition !== 'personal' && runtime.quota_mode === 'platform'" in INSTANCE


def test_runtime_statuses_have_loading_fallback_and_mobile_layout_contracts():
    for html in (INDEX, LOGIN, SPACE, INSTANCE):
        assert "识别版本中" in html or "正在识别运行模式" in html
        assert "兼容模式" in html or "运行模式暂未读取" in html
        assert "@media" in html
    assert ":focus-visible" in INDEX
    assert ":focus-visible" in LOGIN
    assert ":focus-visible" in SPACE
