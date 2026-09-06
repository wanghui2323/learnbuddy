"""每周 6 关自检的周次与实例维度合同。"""

from __future__ import annotations

import importlib
import json


def test_self_check_reads_exact_week_and_custom_instance_dimensions(tmp_path, monkeypatch):
    monkeypatch.setenv("ITUTOR_DB_PATH", str(tmp_path / "self-check.db"))
    import core.auth as auth
    import core.db as db
    import core.self_check as self_check

    importlib.reload(db)
    importlib.reload(auth)
    importlib.reload(self_check)
    db.init_db()
    user = auth.register_user("check@example.com", "secret123")
    inst_dir = tmp_path / "instances" / "path-1"
    inst_dir.mkdir(parents=True)
    dimensions = ["理解", "实现", "评测", "观测", "复盘", "迁移"]
    (inst_dir / "master.json").write_text(
        json.dumps({"learning_methods": {"self_check_dimensions": dimensions}}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = self_check.submit_self_check(
        user_id=user.id,
        instance_id="path-1",
        week=2,
        scores=[4, 4, 4, 4, 3, 2],
        notes=["a", "b"],
    )
    exact = self_check.self_check_for_week(user.id, "path-1", 2, inst_dir)
    missing = self_check.self_check_for_week(user.id, "path-1", 1, inst_dir)

    assert result["week"] == 2
    assert result["passed"] is True
    assert exact is not None and exact["week"] == 2
    assert [item["name"] for item in exact["items"]] == dimensions
    assert missing is None
