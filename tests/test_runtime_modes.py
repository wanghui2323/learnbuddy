"""cloud / personal 运行能力与后端守卫回归。"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

import server
import core.auth as auth_service
import core.push_scheduler as push_scheduler
from core.credits import credits_enabled, spend_for_tokens
from core.db import init_db
from core.runtime import RuntimeConfigurationError, runtime_capabilities, runtime_edition
from core.usage import check_quota


@pytest.fixture()
def runtime_db(tmp_path, monkeypatch):
    monkeypatch.setenv("ITUTOR_DB_PATH", str(tmp_path / "runtime.db"))
    monkeypatch.setenv("ITUTOR_RATE_LIMIT_ENABLED", "0")
    monkeypatch.setenv("ITUTOR_PUSH_ENABLED", "0")
    monkeypatch.setenv("ITUTOR_AGENT_REMINDERS_ENABLED", "0")
    monkeypatch.delenv("LEARNBUDDY_LLM_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    init_db()
    return TestClient(server.app)


def test_cloud_is_default_and_blocks_personal_integrations(runtime_db, monkeypatch):
    monkeypatch.delenv("LEARNBUDDY_EDITION", raising=False)
    payload = runtime_db.get("/api/runtime-capabilities").json()
    assert payload == {
        "edition": "cloud",
        "registration": "enabled",
        "quota_mode": "platform",
        "llm_credentials": "platform",
        "feishu": False,
        "openclaw": False,
        "backup_restore": False,
        "admin_console": True,
        "setup_required": False,
        "llm_configured": False,
    }
    assert runtime_db.get("/api/integrations/feishu").status_code == 403
    assert runtime_db.post("/api/mcp", json={}).status_code == 403
    assert runtime_db.get("/favicon.ico").status_code == 204


def test_cloud_never_runs_or_registers_agent_reminder_delivery(runtime_db, monkeypatch):
    monkeypatch.setenv("LEARNBUDDY_EDITION", "cloud")
    monkeypatch.setenv("ITUTOR_PUSH_ENABLED", "0")
    monkeypatch.setenv("ITUTOR_AGENT_REMINDERS_ENABLED", "1")
    monkeypatch.setenv("FEISHU_APP_ID", "legacy-app")
    monkeypatch.setenv("FEISHU_APP_SECRET", "legacy-secret")
    monkeypatch.setattr(push_scheduler, "_scheduler", None)
    monkeypatch.setattr(
        "core.reminders.queue_due_reminders",
        lambda: pytest.fail("cloud must not queue Feishu reminders"),
    )
    monkeypatch.setattr(
        "core.feishu_sender.send_pending_reminders",
        lambda: pytest.fail("cloud must not send Feishu reminders"),
    )

    push_scheduler.start_scheduler()
    assert push_scheduler._scheduler is None
    asyncio.run(push_scheduler.queue_agent_reminders())


def test_personal_allows_one_time_owner_setup_and_disables_saas(runtime_db, monkeypatch):
    monkeypatch.setenv("LEARNBUDDY_EDITION", "personal")
    before = runtime_db.get("/api/runtime-capabilities").json()
    assert before["registration"] == "setup_only"
    assert before["setup_required"] is True
    assert before["quota_mode"] == "provider"

    created = runtime_db.post(
        "/api/auth/register",
        json={"email": "owner@example.com", "password": "secret123", "name": "Owner"},
    )
    assert created.status_code == 200
    owner_id = created.json()["user"]["id"]
    assert credits_enabled() is False
    assert spend_for_tokens(owner_id, 12_000, scene="test") == 0
    assert check_quota(owner_id)["scope"] == "provider"
    after = runtime_db.get("/api/runtime-capabilities").json()
    assert after["registration"] == "disabled"
    assert after["setup_required"] is False
    assert after["owner_count"] == 1

    rejected = runtime_db.post(
        "/api/auth/register",
        json={"email": "second@example.com", "password": "secret123"},
    )
    assert rejected.status_code == 403
    assert runtime_db.post("/api/credits/redeem", json={"code": "x"}).status_code == 403
    assert runtime_db.get("/api/admin/overview").status_code == 403
    assert runtime_db.get("/admin").status_code == 403
    assert runtime_db.get("/api/integrations/feishu").status_code == 200


def test_personal_owner_initialization_is_atomic(runtime_db, monkeypatch):
    monkeypatch.setenv("LEARNBUDDY_EDITION", "personal")

    def create(index: int) -> str:
        try:
            auth_service.register_user(
                f"owner-{index}@example.com",
                "secret123",
                require_first_user=True,
            )
            return "created"
        except auth_service.RegistrationClosedError:
            return "closed"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, range(2)))
    assert sorted(results) == ["closed", "created"]
    assert runtime_capabilities()["user_count"] == 1


def test_health_readiness_and_personal_multiuser_migration_error(runtime_db, monkeypatch):
    monkeypatch.setenv("LEARNBUDDY_EDITION", "cloud")
    auth_service.register_user("one@example.com", "secret123")
    auth_service.register_user("two@example.com", "secret123")
    monkeypatch.setenv("LEARNBUDDY_EDITION", "personal")

    assert runtime_db.get("/healthz").json() == {"status": "ok"}
    capabilities = runtime_db.get("/api/runtime-capabilities")
    assert capabilities.status_code == 200
    assert capabilities.json()["user_count"] == 2
    assert "configuration_error" in capabilities.json()
    readiness = runtime_db.get("/readyz")
    assert readiness.status_code == 503
    assert readiness.json()["ready"] is False
    assert readiness.json()["checks"]["database"]["ok"] is True

    with pytest.raises(RuntimeConfigurationError, match="业务访问已停止"):
        asyncio.run(server._startup())

    # readiness 不是安全边界：即使调用方忽略探针，业务 HTTP 也必须 fail closed。
    assert runtime_db.get("/login").status_code == 503
    assert runtime_db.get("/space").status_code == 503
    assert runtime_db.get("/api/auth/me").status_code == 503
    assert runtime_db.post(
        "/api/auth/login",
        json={"email": "one@example.com", "password": "secret123"},
    ).status_code == 503


def test_cloud_baseline_llm_routes_share_zero_credit_guard(runtime_db, monkeypatch):
    monkeypatch.setenv("LEARNBUDDY_EDITION", "cloud")
    monkeypatch.setenv("ITUTOR_CREDITS_ENABLED", "1")
    monkeypatch.setenv("ITUTOR_SIGNUP_POINTS", "0")
    monkeypatch.setenv("ITUTOR_GLOBAL_TOKEN_LIMIT", "0")
    registered = runtime_db.post(
        "/api/auth/register",
        json={"email": "zero@example.com", "password": "secret123"},
    )
    assert registered.status_code == 200

    monkeypatch.setattr(server, "create_assessment", lambda **_: pytest.fail("create called"))
    monkeypatch.setattr(server, "request_assistance", lambda **_: pytest.fail("assist called"))
    monkeypatch.setattr(server, "submit_assessment", lambda **_: pytest.fail("submit called"))

    responses = [
        runtime_db.post(
            "/api/baseline/assessments",
            json={"session_id": "quota-session", "domain": "日语"},
        ),
        runtime_db.post(
            "/api/baseline/assessments/ba_fake/assist",
            json={"question_id": "q1", "prompt": "帮我拆解"},
        ),
        runtime_db.post(
            "/api/baseline/assessments/ba_fake/submit",
            json={"phase": "ai_collaboration", "responses": []},
        ),
    ]
    assert [response.status_code for response in responses] == [429, 429, 429]
    assert all("积分已用完" in response.json()["detail"] for response in responses)


def test_baseline_llm_routes_use_explicit_usage_scenes(runtime_db, monkeypatch):
    monkeypatch.setenv("LEARNBUDDY_EDITION", "cloud")
    monkeypatch.setenv("ITUTOR_CREDITS_ENABLED", "1")
    monkeypatch.setenv("ITUTOR_SIGNUP_POINTS", "10")
    registered = runtime_db.post(
        "/api/auth/register",
        json={"email": "scenes@example.com", "password": "secret123"},
    )
    user_id = registered.json()["user"]["id"]
    scenes: list[tuple[int, str]] = []

    @contextmanager
    def observe_usage(uid, scene, _instance_id=None):
        scenes.append((uid, scene))
        yield

    monkeypatch.setattr(server, "usage_context", observe_usage)
    monkeypatch.setattr(server, "create_assessment", lambda **_: {"id": "ba_fake"})
    monkeypatch.setattr(server, "request_assistance", lambda **_: {"reply": "ok"})
    monkeypatch.setattr(server, "submit_assessment", lambda **_: {"status": "completed"})

    assert runtime_db.post(
        "/api/baseline/assessments",
        json={"session_id": "scene-session", "domain": "日语"},
    ).status_code == 200
    assert runtime_db.post(
        "/api/baseline/assessments/ba_fake/assist",
        json={"question_id": "q1", "prompt": "帮我拆解"},
    ).status_code == 200
    assert runtime_db.post(
        "/api/baseline/assessments/ba_fake/submit",
        json={"phase": "ai_collaboration", "responses": []},
    ).status_code == 200
    assert scenes == [
        (user_id, "baseline_create"),
        (user_id, "baseline_assist"),
        (user_id, "baseline_score"),
    ]


def test_invalid_edition_fails_explicitly(runtime_db, monkeypatch):
    monkeypatch.setenv("LEARNBUDDY_EDITION", "enterprise")
    with pytest.raises(RuntimeConfigurationError, match="cloud 或 personal"):
        runtime_edition()
    with pytest.raises(RuntimeConfigurationError, match="cloud 或 personal"):
        asyncio.run(server._startup())
    response = runtime_db.get("/readyz")
    assert response.status_code == 503
    assert "LEARNBUDDY_EDITION" in response.json()["checks"]["runtime"]["error"]
