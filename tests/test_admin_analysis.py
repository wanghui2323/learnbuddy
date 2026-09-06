"""每日运营分析测试。"""

from __future__ import annotations

import importlib
from datetime import date


def test_daily_analysis_detects_pain_points(tmp_path, monkeypatch):
    monkeypatch.setenv("ITUTOR_DB_PATH", str(tmp_path / "analysis.db"))
    import core.db as db
    import core.auth as auth
    import core.conversations as conv
    import core.admin_analysis as aa

    importlib.reload(db)
    importlib.reload(auth)
    importlib.reload(conv)
    importlib.reload(aa)
    db.init_db()

    u = auth.register_user("pain@learnbuddy.test", "secret123")
    conv.record_message(
        user_id=u.id,
        session_id="s1",
        role="user",
        content="WPS AI 收费了，我想换一个免费替代方案，并且要权威来源链接。",
        scene="chat",
    )

    report = aa.run_daily_analysis(date.today().isoformat())
    titles = {i["title"] for i in report["issues"]}
    assert "收费/替代" in titles
    assert "来源/可信度" in titles
    assert report["metrics"]["user_messages"] == 1

    saved = aa.latest_reports(1)
    assert saved[0]["day"] == report["day"]
    assert saved[0]["suggestions"]
