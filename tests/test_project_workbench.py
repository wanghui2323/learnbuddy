"""项目工作台的已验收迭代任务归档合同。"""

from pathlib import Path


WORKBENCH = Path(__file__).resolve().parent.parent / "docs" / "LearnBuddy项目工作台.html"


def test_v050_tasks_are_recorded_with_owners_acceptance_and_final_status():
    html = WORKBENCH.read_text(encoding="utf-8")
    section = html.split('id="version-v050"', 1)[1].split('id="version-v045"', 1)[0]

    for task_id in ("V050-01", "V050-02", "V050-03", "V050-04", "V050-05"):
        assert task_id in section
    assert section.count("<td>已验收</td>") == 5
    assert "负责模块" in section
    assert "完成标准" in section
    assert "304 tests" in section


def test_v052_continuous_task_tree_is_fully_accepted():
    html = WORKBENCH.read_text(encoding="utf-8")
    continuation = html.split('id="continuation"', 1)[1].split('id="versions"', 1)[0]
    version = html.split('id="version-v052"', 1)[1].split('id="version-v050"', 1)[0]

    for task_id in ("V052-01", "V052-02", "V052-03", "FINAL-01", "RELEASE-01"):
        assert task_id in continuation
    assert "全部验收" in continuation
    assert continuation.count("<td>已验收</td>") == 14
    assert "user+session" in version
    assert "第 31 次聊天请求返回 429" in version
    assert "密码删除后账号、会话、路径均消失" in version


def test_v053_local_development_is_recorded_and_release_remains_open():
    html = WORKBENCH.read_text(encoding="utf-8")
    section = html.split('id="iteration-v053"', 1)[1].split('id="continuation"', 1)[0]

    for task_id in ("V053-01", "V053-02", "V053-03", "V053-04", "V053-05", "V053-06", "V053-07", "V053-08", "V053-09", "V053-10"):
        assert task_id in section
    assert section.count("<td>已完成</td>") == 10  # REQ + 01-09
    assert "Release Loop 已建立，待双人评分与发布" in section
    assert "release_loop.py status" in section
    assert "尚缺 36 个评分单元" in section
    assert "生产在线备份" in section
