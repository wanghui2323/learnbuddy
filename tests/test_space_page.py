"""web/space.html 关键交互契约测试。"""

from __future__ import annotations

from pathlib import Path


SPACE_HTML = Path(__file__).resolve().parent.parent / "web" / "space.html"


def test_rename_uses_product_dialog_not_browser_prompt():
    html = SPACE_HTML.read_text(encoding="utf-8")

    assert "prompt(" not in html
    assert 'id="renameOverlay"' in html
    assert "function openRenameDialog" in html
    assert "function submitRename" in html
    assert "重命名学习路径" in html


def test_path_cards_use_completion_progress_instead_of_calendar_time():
    html = SPACE_HTML.read_text(encoding="utf-8")

    assert "function pathProgress(instance)" in html
    assert "pathProgress(i)" in html
    assert "weekProgress" not in html
    assert "当前 W${week} · D${day}" in html


def test_account_data_has_visible_export_and_confirmed_delete_flow():
    html = SPACE_HTML.read_text(encoding="utf-8")

    assert 'id="accountBtn"' in html
    assert 'id="accountOverlay"' in html
    assert "function downloadAccountData()" in html
    assert "fetch('/api/account/export'" in html
    assert "function deleteAccount()" in html
    assert "method:'DELETE'" in html
    assert "请输入当前密码再次确认" in html
    assert "永久删除 LearnBuddy 账号及其学习数据" in html
    assert "OpenClaw 会话、OpenClaw 凭据和飞书应用凭据不会自动删除" in html
    assert "需按部署指南单独清理" in html


def test_space_waits_for_user_identity_before_rendering_greeting():
    html = SPACE_HTML.read_text(encoding="utf-8")

    assert "async function initSpace()" in html
    assert "await loadUser();" in html
    assert "await load();" in html
    assert "loadUser();\nload();" not in html


def test_account_dialog_contains_feishu_binding_and_personal_reminders():
    html = SPACE_HTML.read_text(encoding="utf-8")

    assert 'id="feishuIntegrationSection"' in html
    assert "你不需要创建飞书应用" in html
    assert "function createFeishuBindingCode()" in html
    assert "'/api/integrations/feishu/binding-code'" in html
    assert "function unbindFeishu()" in html
    assert 'id="reminderTime"' in html
    assert "function loadReminderPolicy()" in html
    assert "fetch('/api/reminders/policy', {cache:'no-store'})" in html
    assert "function saveReminderPolicy()" in html
    assert "'/api/reminders/policy'" in html
