"""6 关自检页的周次和返回路径合同。"""

from pathlib import Path


WEB_SELF_CHECK = Path(__file__).resolve().parent.parent / "web" / "self-check.html"


def test_self_check_uses_requested_week_and_returns_to_same_instance():
    html = WEB_SELF_CHECK.read_text(encoding="utf-8")

    assert 'REQUESTED_WEEK = Math.max(1, Number(query.get("week") || 1) || 1);' in html
    assert "self-check/latest?week=${REQUESTED_WEEK}" in html
    assert "latest.week + 1" not in html
    assert "?view=path#week-${REQUESTED_WEEK}" in html
    assert 'window.__suggestWeek = REQUESTED_WEEK;' in html
