"""LearnBuddy 学习伙伴 FastAPI 服务。

启动：
    .venv/bin/python server.py
    # 或
    .venv/bin/uvicorn server:app --reload --host 127.0.0.1 --port 8000

路由：
    GET  /                     → web/index.html (对话首页)
    GET  /instance/{id}        → web/instance.html (学习路径页)
    GET  /assets/*             → 静态资源（CSS / JS / 图片）

API：
    GET  /api/opening          → 暖场问候 + 服务 metadata
    POST /api/chat             → SSE 流式对话（核心）
    GET  /api/instance/{id}    → 拉取学习路径数据
    GET  /api/instances        → 学习路径列表（_index.json）

说明：
    - 对外品牌是 LearnBuddy；内部包名/路径仍沿用 itutor/instance，避免破坏已有数据与路由。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

import core.auth as auth_service
from core import __version__
from core.auth import (
    User,
    create_session,
    delete_session,
    get_user_by_token,
)
from core.account_data import cleanup_expired_data, delete_user_data, export_user_data
from core.baseline import (
    assessment_quality_summary,
    bind_assessment_to_instance,
    create_assessment,
    create_dispute,
    get_assessment,
    get_current_assessment,
    recover_inflight_assessments,
    request_assistance,
    save_response,
    submit_assessment,
    submitted_profile_for_session,
)
from core.admin_analysis import latest_reports, run_daily_analysis
from core.coach import coach_reply
from core.concepts import (
    MASTERY_THRESHOLD,
    build_unlocks,
    concept_status,
    load_or_generate,
    sanitize as sanitize_concepts,
)
from core.conversations import (
    claim_session,
    clear_session,
    record_message,
    session_messages,
)
from core.content import ensure_lesson_cards, generate_lesson_reviewed
from core.content_quality import evaluate_lesson_quality
from core.db import get_conn, init_db
from core.credits import (
    balance as credit_balance,
    create_cards,
    grant_points,
    list_cards,
    redeem_card,
    reward_study_completion,
)
from core import tracing
from core.feedback import feedback_summary, record_feedback, trace_summary
from core.intent import (
    build_anchor_context,
    initial_goal_output_choices,
    should_clarify_intent,
    source_queries_for_goal,
)
from core.lesson_flow import attach_lesson_flow
from core.lesson_runtime import load_lesson_runtime, save_lesson_runtime
from core.jobs import get_job as get_background_job
from core.jobs import recover_inflight_jobs, save_job as save_background_job
from core.rate_limit import check_rate_limit
from core.mastery import concept_mastery, mastered_set
from core.path_loop import (
    advance_after_self_check,
    load_path_loop_state,
    record_lesson_completed,
    set_pregeneration_state,
    week_completion,
)
from core.rag import (
    delete_source,
    has_knowledge,
    ingest_text,
    list_sources,
    search,
    search_user_knowledge,
)
from core.source_search import (
    build_grounding_context,
    build_source_pack,
    format_sources_context,
    normalize_source_pack,
    select_quality_sources,
)
from core.engine import ConversationEngine, opening_greeting
from core.generator import DEFAULT_INSTANCES_DIR, generate_instance
from core.learning import (
    analytics_summary,
    list_artifacts,
    record_artifact,
    record_lesson_result,
    studied_topics,
    wrong_questions,
)
from core.llm_client import LLMConfigError, LLMError, set_usage_observer
from core.runtime import (
    RuntimeConfigurationError,
    assert_runtime_boundary,
    runtime_capabilities,
    runtime_edition,
)
from core.usage import (
    admin_overview,
    admin_user_detail,
    check_quota,
    daily_usage,
    log_event,
    record_current_usage,
    set_user_limit,
    usage_context,
    user_recent_usage,
    user_summary,
)


load_dotenv()


# ============================================================================
# 路径
# ============================================================================


PROJECT_ROOT = Path(__file__).resolve().parent
WEB_DIR = PROJECT_ROOT / "web"
ASSETS_DIR = WEB_DIR / "assets"
INSTANCES_DIR = DEFAULT_INSTANCES_DIR


# ============================================================================
# FastAPI app
# ============================================================================


app = FastAPI(
    title="LearnBuddy 学习伙伴",
    version=__version__,
    description="个性化 AI 学习伙伴 · OpenAI-compatible LLM + FastAPI",
)

_RUNTIME_DIAGNOSTIC_PATHS = {"/healthz", "/readyz", "/api/runtime-capabilities"}


@app.middleware("http")
async def _enforce_runtime_boundaries(request: Request, call_next):
    """能力边界由后端强制执行，前端隐藏只负责体验。"""
    path = request.url.path
    if path in _RUNTIME_DIAGNOSTIC_PATHS:
        return await call_next(request)
    try:
        edition = runtime_edition()
        assert_runtime_boundary()
    except RuntimeConfigurationError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=503)
    except (OSError, sqlite3.Error):
        return JSONResponse({"detail": "运行边界暂时无法确认"}, status_code=503)
    if edition == "cloud" and (
        path.startswith("/api/integrations/") or path in {"/api/mcp", "/api/mcp/"}
    ):
        return JSONResponse(
            {"detail": "cloud 体验版不提供个人飞书/OpenClaw 集成"},
            status_code=403,
        )
    if edition == "personal" and (
        path == "/admin"
        or path.startswith("/api/credits/")
        or path.startswith("/api/admin/")
    ):
        return JSONResponse(
            {"detail": "personal 版本不提供平台积分或 SaaS 管理接口"},
            status_code=403,
        )
    return await call_next(request)


@app.get("/healthz")
async def healthz() -> dict:
    """进程存活检查，不依赖外部模型或数据库。"""
    return {"status": "ok"}


@app.get("/api/runtime-capabilities")
async def api_runtime_capabilities() -> Response:
    """公开、只读的当前运行能力合同。"""
    try:
        return JSONResponse(runtime_capabilities(), headers={"Cache-Control": "no-store"})
    except RuntimeConfigurationError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=503)
    except (OSError, sqlite3.Error):
        return JSONResponse(
            {"detail": "运行能力暂时无法读取"}, status_code=503
        )


@app.get("/readyz")
async def readyz() -> Response:
    """可接流量检查：运行版本、数据库及个人版 owner 约束。"""
    checks: dict[str, Any] = {}
    capabilities: dict = {}
    try:
        capabilities = runtime_capabilities()
        checks["runtime"] = {
            "ok": "configuration_error" not in capabilities,
            "edition": capabilities["edition"],
        }
        if capabilities.get("configuration_error"):
            checks["runtime"]["error"] = capabilities["configuration_error"]
    except (RuntimeConfigurationError, OSError, sqlite3.Error) as exc:
        checks["runtime"] = {"ok": False, "error": str(exc)}

    try:
        conn = get_conn()
        try:
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()
        checks["database"] = {"ok": True}
    except Exception as exc:  # noqa: BLE001 - readiness 需返回可诊断的结构
        checks["database"] = {"ok": False, "error": str(exc)}

    checks["llm"] = {
        "configured": bool(capabilities.get("llm_configured")),
        "required_for_readiness": False,
    }
    ready = bool(checks.get("runtime", {}).get("ok")) and bool(
        checks.get("database", {}).get("ok")
    )
    return JSONResponse(
        {"ready": ready, "checks": checks},
        status_code=200 if ready else 503,
        headers={"Cache-Control": "no-store"},
    )


# 静态资源（CSS/JS/图片）
if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")


@app.get("/sw.js")
async def _sw_js():
    """Service worker（根 scope，控制 /instance 等页面收 Web Push）。"""
    return FileResponse(WEB_DIR / "sw.js", media_type="application/javascript")


@app.get("/favicon.ico", include_in_schema=False)
async def _favicon():
    """暂无独立图标资产；显式返回空响应，避免浏览器控制台产生 404 噪声。"""
    return Response(status_code=204)


@app.get("/common.css")
async def _common_css():
    """公共样式（V0.28 产品体验打磨：toast / spinner / loading / 移动端响应式）。"""
    return FileResponse(WEB_DIR / "common.css", media_type="text/css")


@app.get("/common.js")
async def _common_js():
    """公共 JS（V0.28：showToast / setButtonLoading / fetchJson）。"""
    return FileResponse(WEB_DIR / "common.js", media_type="application/javascript")


@app.get("/manifest.json")
async def _manifest_json():
    """PWA manifest（display:standalone，iOS 加桌面后才收 Web Push）。"""
    return FileResponse(WEB_DIR / "manifest.json", media_type="application/manifest+json")


@app.on_event("startup")
async def _startup() -> None:
    """建表（幂等）+ 注册 token 采集观察者 + 打开 LLM tracing + 起推送调度器。"""
    runtime_edition()  # 非法 edition 在接流量前明确失败。
    init_db()
    assert_runtime_boundary()
    recover_inflight_jobs()
    recover_inflight_assessments()
    retention_days = (os.getenv("ITUTOR_RETENTION_DAYS") or "").strip()
    if retention_days:
        try:
            cleanup_expired_data(int(retention_days))
        except (TypeError, ValueError):
            pass
    set_usage_observer(record_current_usage)
    tracing.enable()
    if (
        os.getenv("ITUTOR_PUSH_ENABLED", "1") != "0"
        or os.getenv("ITUTOR_AGENT_REMINDERS_ENABLED", "1") != "0"
    ):
        from core.push_scheduler import start_scheduler

        start_scheduler()
    if os.getenv("ITUTOR_ANALYSIS_ENABLED", "1") != "0":
        from core.analysis_scheduler import start_scheduler as start_analysis_scheduler

        start_analysis_scheduler()


@app.on_event("shutdown")
async def _shutdown() -> None:
    """停推送调度器。"""
    from core.push_scheduler import stop_scheduler
    from core.analysis_scheduler import stop_scheduler as stop_analysis_scheduler

    stop_scheduler()
    stop_analysis_scheduler()


# ============================================================================
# 认证：cookie 会话 + 当前用户
# ============================================================================


COOKIE_NAME = "itutor_session"
_COOKIE_MAX_AGE = 30 * 24 * 3600
_GENERATE_PROGRESS_STEPS = [
    {
        "label": "整理目标画像",
        "detail": "汇总你的对话、目标、基线和偏好，形成路径参数包。",
    },
    {
        "label": "检索与校验资料",
        "detail": "结合用户知识库和外部来源，为大纲生成提供事实依据。",
    },
    {
        "label": "拆解能力图谱",
        "detail": "把目标拆成知识点、阶段门槛、每周主题和每日节奏。",
    },
    {
        "label": "生成学习计划",
        "detail": "生成周计划、首周日计划、学习手册和学习契约。",
    },
    {
        "label": "质量审查与保存",
        "detail": "检查结构完整性和兼容性，保存后进入学习空间。",
    },
]

def _secure_cookie_enabled() -> bool:
    if (os.getenv("ITUTOR_ENV") or "").strip().lower() in {"production", "prod"}:
        return True
    explicit = (os.getenv("ITUTOR_COOKIE_SECURE") or "").strip().lower()
    if explicit:
        return explicit in {"1", "true", "yes", "on"}
    return False


def _set_session_cookie(resp: Response, token: str) -> None:
    resp.set_cookie(
        COOKIE_NAME,
        token,
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        secure=_secure_cookie_enabled(),
        samesite="lax",
        path="/",
    )


def current_user(request: Request) -> User | None:
    """从 cookie 读登录态，未登录返回 None。"""
    return get_user_by_token(request.cookies.get(COOKIE_NAME, ""))


def require_user(request: Request) -> User:
    """API 用：未登录抛 401。"""
    user = current_user(request)
    if user is None:
        raise HTTPException(401, "未登录")
    return user


def _bearer_token(request: Request) -> str:
    authorization = (request.headers.get("authorization") or "").strip()
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(401, "缺少 Bearer 凭证")
    return token.strip()


def _require_openclaw_bridge(request: Request) -> None:
    from core.agent_integration import AgentIntegrationError, verify_bridge_secret

    try:
        verify_bridge_secret(_bearer_token(request))
    except AgentIntegrationError as exc:
        raise HTTPException(401, str(exc)) from exc


def _request_actor(request: Request, *, hint: str = "") -> str:
    host = request.client.host if request.client else "unknown"
    return f"ip:{host}|{(hint or '').strip().lower()[:160]}"


def _enforce_rate_limit(
    request: Request,
    *,
    scope: str,
    actor: str,
    default_limit: int,
) -> None:
    """关键写接口限流；持久层异常时不阻断正常学习链路。"""
    if (os.getenv("ITUTOR_RATE_LIMIT_ENABLED") or "1").strip() == "0":
        return
    env_key = "ITUTOR_RATE_" + scope.upper().replace("-", "_") + "_LIMIT"
    try:
        limit = max(1, int(os.getenv(env_key, str(default_limit))))
        window = max(1, int(os.getenv("ITUTOR_RATE_WINDOW_SECONDS", "60")))
        result = check_rate_limit(actor=actor, scope=scope, limit=limit, window_seconds=window)
    except Exception:  # noqa: BLE001 - 限流存储异常不能让主产品完全不可用
        return
    if not result.get("allowed"):
        retry_after = int(result.get("retry_after") or window)
        raise HTTPException(
            429,
            f"操作过于频繁，请 {retry_after} 秒后重试",
            headers={"Retry-After": str(retry_after)},
        )


async def _enrich_intent_decision(decision):
    """多义概念澄清阶段不把来源名直接甩给用户。

    搜索仍在正式生成/语义接地链路里发挥作用；澄清阶段只问场景和目标，
    避免把不相关的来源标题误展示成系统判断依据。
    """
    return decision


async def _goal_source_context(
    message: str,
    *,
    turn_count: int,
    history,
) -> str:
    """给对话模型注入轻量来源上下文，用于通用技术概念接地。

    这不是最终深度研究，只帮助模型避免把 MCP/RAG/新框架等概念当成未知词。
    """
    queries = source_queries_for_goal(message, turn_count=turn_count, history=history)
    if not queries:
        return ""
    query = queries[0].strip()
    if not query:
        return ""
    try:
        pack = await asyncio.wait_for(
            asyncio.to_thread(build_source_pack, query, count=4, timeout=3.5),
            timeout=4.0,
        )
    except Exception:  # noqa: BLE001 - 语义接地搜索失败不阻断主对话
        return ""
    sources = pack.get("sources") or []
    if not sources:
        return ""
    context = format_sources_context(sources[:4])
    if not context:
        return ""
    return (
        "【轻量资料接地】以下来源只用于理解用户提到的技术概念和后续校验，"
        "不要把它当作已经完成深度调研。回复时先用自然语言复述暂定理解，"
        "再问目标产出、应用场景和当前基础；不要生硬列来源标题给用户。\n\n"
        f"{context}"
    )


def require_admin(request: Request) -> User:
    """管理后台用：非管理员抛 403。"""
    user = require_user(request)
    if not user.is_admin:
        raise HTTPException(403, "需要管理员权限")
    return user


def _quota_message(q: dict) -> str:
    if q.get("scope") == "global":
        return "平台本月总额度已用尽，请稍后或联系管理员。"
    if q.get("scope") == "credits":
        return "你的积分已用完。可以兑换积分卡，或联系管理员赠送积分后继续使用。"
    return (
        f"你本月的 token 额度已用尽（{q['used']}/{q['limit']}）。"
        "下月自动重置，或联系管理员调整额度。"
    )


def _require_llm_quota(user_id: int) -> None:
    """所有可能触发 LLM 的 HTTP 入口共用同一后端额度门。"""
    quota = check_quota(user_id)
    if not quota["allowed"]:
        raise HTTPException(429, _quota_message(quota))


# ============================================================================
# 会话池：(user_id, session_id) → ConversationEngine
# ============================================================================


_sessions: dict[tuple[int, str], ConversationEngine] = {}


def _get_or_create_session(user_id: int, session_id: str) -> ConversationEngine:
    claim_session(user_id, session_id)
    key = (int(user_id), session_id)
    if key not in _sessions:
        engine = ConversationEngine()
        engine.restore(session_messages(user_id, session_id))
        _sessions[key] = engine
    return _sessions[key]


def _reset_session(user_id: int, session_id: str, *, clear_persisted: bool = False) -> None:
    _sessions.pop((int(user_id), session_id), None)
    if clear_persisted:
        clear_session(user_id, session_id)


# ============================================================================
# Web 页面路由
# ============================================================================


@app.get("/login")
async def login_page(request: Request):
    """登录 / 注册页。已登录则直接进学习空间。"""
    if current_user(request) is not None:
        return RedirectResponse("/space", status_code=302)
    return FileResponse(WEB_DIR / "login.html")


@app.get("/")
async def index_page(request: Request):
    """对话首页（需登录）。"""
    if current_user(request) is None:
        return RedirectResponse("/login", status_code=302)
    return FileResponse(WEB_DIR / "index.html")


@app.get("/space")
async def space_page(request: Request):
    """学习空间（书架）：列出全部学习路径，可切换 / 管理状态。"""
    if current_user(request) is None:
        return RedirectResponse("/login", status_code=302)
    return FileResponse(WEB_DIR / "space.html")


@app.get("/self-check/{instance_id}")
async def self_check_page(request: Request, instance_id: str):
    """6 关自检交互页（前端 JS 通过 URL 解析 id 调 /api/instance/{id}/self-check/*）。"""
    _ = instance_id  # URL 参数交给前端 JS 解析
    if current_user(request) is None:
        return RedirectResponse("/login", status_code=302)
    return FileResponse(WEB_DIR / "self-check.html")


@app.get("/instance/{instance_id}")
async def instance_page(request: Request, instance_id: str):
    """学习路径展示页（前端 JS 通过 URL 解析 id 调 /api/instance/{id}）。"""
    _ = instance_id  # URL 参数交给前端 JS 解析
    if current_user(request) is None:
        return RedirectResponse("/login", status_code=302)
    # Runno 代码运行组件依赖 SharedArrayBuffer，需要跨源隔离上下文。
    # credentialless 比 require-corp 宽松：CDN 资源无需 CORP 头即可加载。
    return FileResponse(
        WEB_DIR / "instance.html",
        headers={
            "Cross-Origin-Opener-Policy": "same-origin",
            "Cross-Origin-Embedder-Policy": "credentialless",
        },
    )


@app.get("/admin")
async def admin_page(request: Request):
    """管理后台页（仅管理员）。非管理员跳回学习空间。"""
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=302)
    if not user.is_admin:
        return RedirectResponse("/space", status_code=302)
    return FileResponse(WEB_DIR / "admin.html")


# ============================================================================
# API · 认证
# ============================================================================


@app.post("/api/auth/register")
async def api_register(request: Request) -> Response:
    body = await request.json()
    _enforce_rate_limit(
        request,
        scope="auth-register",
        actor=_request_actor(request, hint=body.get("email", "")),
        default_limit=6,
    )
    try:
        user = auth_service.register_user(
            body.get("email", ""),
            body.get("password", ""),
            body.get("name", ""),
            require_first_user=runtime_edition() == "personal",
        )
    except auth_service.RegistrationClosedError as e:
        raise HTTPException(403, str(e))
    except auth_service.AuthError as e:
        raise HTTPException(400, str(e))
    token = create_session(user.id)
    log_event(user.id, "register", user.email)
    resp = JSONResponse({"ok": True, "user": user.public()})
    _set_session_cookie(resp, token)
    return resp


@app.post("/api/auth/login")
async def api_login(request: Request) -> Response:
    body = await request.json()
    _enforce_rate_limit(
        request,
        scope="auth-login",
        actor=_request_actor(request, hint=body.get("email", "")),
        default_limit=10,
    )
    try:
        user = auth_service.authenticate(body.get("email", ""), body.get("password", ""))
    except auth_service.AuthError as e:
        raise HTTPException(401, str(e))
    token = create_session(user.id)
    log_event(user.id, "login", user.email)
    resp = JSONResponse({"ok": True, "user": user.public()})
    _set_session_cookie(resp, token)
    return resp


@app.post("/api/auth/logout")
async def api_logout(request: Request) -> Response:
    user = current_user(request)
    if user is not None:
        log_event(user.id, "logout")
    delete_session(request.cookies.get(COOKIE_NAME, ""))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(
        COOKIE_NAME,
        path="/",
        secure=_secure_cookie_enabled(),
        httponly=True,
        samesite="lax",
    )
    return resp


# ============================================================================
# API · 积分账户（本人）+ 管理后台运营
# ============================================================================


@app.get("/api/usage")
async def api_usage(request: Request, lite: bool = False) -> dict:
    """当前用户自己的积分账户。用户侧不暴露 token 明细，token 只留在管理员后台核算。"""
    user = require_user(request)
    if lite:
        return {"credits": credit_balance(user.id)}
    summary = user_summary(user.id)
    daily = daily_usage(30, user.id)
    return {
        "summary": {
            "calls": summary.get("calls", 0),
            "by_scene": [
                {"scene": r.get("scene"), "calls": r.get("calls", 0)}
                for r in summary.get("by_scene", [])
            ],
        },
        "quota": check_quota(user.id),
        "credits": credit_balance(user.id),
        "daily": [{"date": r["date"], "calls": r["calls"]} for r in daily],
        "recent": [],
    }


@app.post("/api/credits/redeem")
async def api_redeem_credit_card(request: Request) -> dict:
    """用户兑换积分卡。普通用户可用；生成/赠送只允许管理员。"""
    user = require_user(request)
    body = await request.json()
    try:
        credits = redeem_card(user.id, body.get("code", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))
    log_event(user.id, "redeem_card", f"{body.get('code', '')[:32]}")
    return {"ok": True, "credits": credits}


@app.get("/api/admin/overview")
async def api_admin_overview(request: Request) -> dict:
    """全局总览 + 各用户消耗/活跃度（仅管理员）。"""
    require_admin(request)
    return admin_overview()


@app.get("/api/admin/quality")
async def api_admin_quality(request: Request) -> dict:
    """质量/可观测面板（仅管理员）：用户反馈聚合 + LLM 调用 tracing（fallback 率/延迟）。

    这是 Batch L「环 B 的眼睛」——改 prompt/难度模型前先看这里有没有变好。
    """
    require_admin(request)
    return {
        "feedback": feedback_summary(),
        "traces": trace_summary(),
        "assessments": assessment_quality_summary(),
    }


@app.get("/api/admin/analysis")
async def api_admin_analysis(request: Request) -> dict:
    """每日运营分析报告（仅管理员）。空报告时自动生成今天的快照。"""
    require_admin(request)
    reports = latest_reports(7)
    if not reports:
        reports = [run_daily_analysis(datetime.now().date().isoformat())]
    return {"reports": reports}


@app.post("/api/admin/analysis/run")
async def api_admin_analysis_run(request: Request) -> dict:
    """手动生成/刷新某天运营分析（仅管理员）。"""
    admin = require_admin(request)
    body = await request.json()
    day = (body.get("day") or datetime.now().date().isoformat()).strip()
    report = run_daily_analysis(day)
    log_event(admin.id, "run_analysis", day)
    return {"ok": True, "report": report}


@app.get("/api/admin/user/{user_id}")
async def api_admin_user(request: Request, user_id: int) -> dict:
    """单个用户的消耗明细 + 行为事件（仅管理员）。"""
    require_admin(request)
    detail = admin_user_detail(user_id)
    if not detail:
        raise HTTPException(404, f"用户 {user_id} 不存在")
    return detail


@app.post("/api/admin/user/{user_id}/limit")
async def api_admin_set_limit(request: Request, user_id: int) -> dict:
    """设置某用户每月 token 上限（仅管理员）。

    body: { "limit": 数字 }，limit=0 表示无限，limit=null 表示回退环境默认。
    """
    admin = require_admin(request)
    body = await request.json()
    raw = body.get("limit", None)
    limit = None if raw is None or raw == "" else max(0, int(raw))
    set_user_limit(user_id, limit)
    log_event(admin.id, "set_limit", f"user={user_id} limit={limit}")
    return {"user_id": user_id, "limit": limit}


@app.post("/api/admin/user/{user_id}/credits/grant")
async def api_admin_grant_credits(request: Request, user_id: int) -> dict:
    """管理员直接给用户赠送积分。普通用户不能调用。"""
    admin = require_admin(request)
    body = await request.json()
    try:
        points = max(1, int(body.get("points") or 0))
        credits = grant_points(
            user_id,
            points,
            admin_id=admin.id,
            reason="admin_grant",
            detail=(body.get("detail") or "管理员赠送积分"),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    log_event(admin.id, "grant_credits", f"user={user_id} points={points}")
    return {"ok": True, "user_id": user_id, "credits": credits}


@app.get("/api/admin/credits/cards")
async def api_admin_list_credit_cards(request: Request) -> dict:
    require_admin(request)
    return {"cards": list_cards(120)}


@app.post("/api/admin/credits/cards")
async def api_admin_create_credit_cards(request: Request) -> dict:
    admin = require_admin(request)
    body = await request.json()
    try:
        cards = create_cards(
            int(body.get("points") or 0),
            int(body.get("count") or 1),
            admin_id=admin.id,
            note=(body.get("note") or ""),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    log_event(admin.id, "create_credit_cards", f"points={body.get('points')} count={len(cards)}")
    return {"ok": True, "cards": cards}


@app.get("/api/auth/me")
async def api_me(request: Request) -> dict:
    user = require_user(request)
    return {"user": user.public()}


@app.get("/api/account/export")
async def api_account_export(request: Request) -> Response:
    """下载当前账号的可移植 JSON；不包含密码、会话和推送密钥。"""
    user = require_user(request)
    payload = await asyncio.to_thread(export_user_data, user.id, INSTANCES_DIR)
    return JSONResponse(
        payload,
        headers={
            "Content-Disposition": f'attachment; filename="learnbuddy-account-{user.id}.json"',
            "Cache-Control": "no-store",
        },
    )


@app.delete("/api/account")
async def api_delete_account(request: Request) -> Response:
    """密码确认后删除当前账号、结构化学习数据与全部归属路径。"""
    user = require_user(request)
    _enforce_rate_limit(
        request,
        scope="account-delete",
        actor=f"user:{user.id}",
        default_limit=3,
    )
    body = await request.json()
    try:
        result = await asyncio.to_thread(
            delete_user_data,
            user.id,
            body.get("password") or "",
            INSTANCES_DIR,
        )
    except auth_service.AuthError as exc:
        raise HTTPException(403, str(exc)) from exc
    for key in [key for key in _sessions if key[0] == user.id]:
        _sessions.pop(key, None)
    response = JSONResponse(result)
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        secure=_secure_cookie_enabled(),
        httponly=True,
        samesite="lax",
    )
    return response


# ============================================================================
# 飞书 / OpenClaw 用户级集成（ADR-057）
# ============================================================================


@app.get("/api/integrations/feishu")
async def api_feishu_integration_status(request: Request) -> dict:
    user = require_user(request)
    from core.agent_integration import configured, get_active_binding
    from core.reminders import get_policy

    account_id = (os.getenv("ITUTOR_FEISHU_ACCOUNT_ID") or "default").strip()
    return {
        "configured": configured(),
        "binding": get_active_binding(user.id, account_id=account_id),
        "reminder_policy": get_policy(user.id),
        "account_id": account_id,
    }


@app.post("/api/integrations/feishu/binding-code")
async def api_create_feishu_binding_code(request: Request) -> dict:
    user = require_user(request)
    _enforce_rate_limit(
        request,
        scope="feishu-binding-code",
        actor=f"user:{user.id}",
        default_limit=5,
    )
    from core.agent_integration import AgentIntegrationError, create_binding_code

    account_id = (os.getenv("ITUTOR_FEISHU_ACCOUNT_ID") or "default").strip()
    try:
        result = create_binding_code(user.id, account_id=account_id)
    except AgentIntegrationError as exc:
        raise HTTPException(503, str(exc)) from exc
    log_event(user.id, "feishu_binding_code", f"account={account_id}")
    return {"ok": True, **result}


@app.delete("/api/integrations/feishu/binding")
async def api_revoke_feishu_binding(request: Request) -> dict:
    user = require_user(request)
    from core.agent_integration import revoke_binding

    account_id = (os.getenv("ITUTOR_FEISHU_ACCOUNT_ID") or "default").strip()
    result = revoke_binding(user.id, account_id=account_id)
    log_event(user.id, "feishu_unbind", f"account={account_id}")
    return result


@app.get("/api/reminders/policy")
async def api_get_reminder_policy(request: Request) -> dict:
    user = require_user(request)
    from core.reminders import get_policy

    policy = get_policy(user.id)
    return {"configured": policy is not None, "policy": policy}


@app.put("/api/reminders/policy")
async def api_set_reminder_policy(request: Request) -> dict:
    user = require_user(request)
    body = await request.json()
    instance_id = (body.get("instance_id") or "").strip()
    if instance_id:
        _assert_can_access(instance_id, user)
    from core.reminders import ReminderError, set_policy

    try:
        policy = set_policy(
            user.id,
            instance_id=instance_id,
            timezone_name=body.get("timezone") or "Asia/Shanghai",
            local_time=body.get("local_time") or "08:00",
            weekdays=body.get("weekdays"),
            quiet_start=body.get("quiet_start") or "",
            quiet_end=body.get("quiet_end") or "",
            enabled=body.get("enabled", True),
        )
    except ReminderError as exc:
        raise HTTPException(400, str(exc)) from exc
    log_event(user.id, "reminder_policy", f"enabled={int(bool(policy.get('enabled')))}")
    return {"ok": True, "policy": policy}


@app.post("/api/integrations/openclaw/bind")
async def api_openclaw_bind(request: Request) -> dict:
    """OpenClaw 通道插件用可信 sender id 兑换用户绑定。"""
    _require_openclaw_bridge(request)
    body = await request.json()
    from core.agent_integration import AgentIntegrationError, redeem_binding_code

    try:
        binding = redeem_binding_code(
            body.get("code") or "",
            external_user_id=body.get("requester_sender_id") or "",
            account_id=body.get("account_id") or os.getenv("ITUTOR_FEISHU_ACCOUNT_ID") or "default",
            tenant_key=body.get("tenant_key") or "",
        )
    except AgentIntegrationError as exc:
        raise HTTPException(400, str(exc)) from exc
    log_event(int(binding["user_id"]), "feishu_bind", f"binding={binding['id']}")
    return {"ok": True, "binding": binding}


@app.post("/api/integrations/openclaw/token")
async def api_openclaw_agent_token(request: Request) -> dict:
    """OpenClaw 按当前发送者换取最长 10 分钟的 LearnBuddy token。"""
    _require_openclaw_bridge(request)
    body = await request.json()
    from core.agent_integration import (
        AgentIntegrationError,
        issue_agent_token,
        resolve_active_binding,
    )

    try:
        binding = resolve_active_binding(
            external_user_id=body.get("requester_sender_id") or "",
            account_id=body.get("account_id") or os.getenv("ITUTOR_FEISHU_ACCOUNT_ID") or "default",
        )
        # OpenClaw requester-scoped resolver 最多 5 分钟复用连接；
        # 10 分钟 token 留出重建输送的边界余量。
        token = issue_agent_token(binding, ttl_seconds=600)
    except AgentIntegrationError as exc:
        raise HTTPException(401, str(exc)) from exc
    return {"ok": True, **token}


@app.post("/api/integrations/openclaw/reminders/claim")
async def api_openclaw_claim_reminders(request: Request) -> dict:
    """通道 worker 取待发飞书提醒；真实发送由飞书凭证配置决定。"""
    _require_openclaw_bridge(request)
    body = await request.json()
    from core.reminders import claim_pending_deliveries, queue_due_reminders

    queued = queue_due_reminders()
    account_id = body.get("account_id") or os.getenv("ITUTOR_FEISHU_ACCOUNT_ID") or "default"
    deliveries = claim_pending_deliveries(
        account_id=account_id,
        limit=body.get("limit") or 20,
    )
    return {"ok": True, "queue": queued, "deliveries": deliveries}


@app.post("/api/integrations/openclaw/reminders/{delivery_id}/result")
async def api_openclaw_complete_reminder(delivery_id: int, request: Request) -> dict:
    _require_openclaw_bridge(request)
    body = await request.json()
    from core.reminders import ReminderError, complete_delivery

    try:
        delivery = complete_delivery(
            delivery_id,
            claim_token=body.get("claim_token") or "",
            status=body.get("status") or "",
            error=body.get("error") or "",
        )
    except ReminderError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True, "delivery": delivery}


@app.post("/api/mcp")
async def api_remote_mcp(request: Request) -> Response:
    """无状态 HTTP MCP：用户身份只来自签名 bearer token。"""
    from core.agent_integration import AgentIntegrationError, verify_agent_token
    from mcp_server import handle

    try:
        identity = verify_agent_token(_bearer_token(request), required_scope="mcp")
    except AgentIntegrationError as exc:
        raise HTTPException(401, str(exc)) from exc
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "MCP 请求必须是 JSON-RPC 对象")
    result = handle(body, identity["user_id"])
    if result is None:
        return Response(status_code=204)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


# ============================================================================
# API · 元信息
# ============================================================================


@app.get("/api/opening")
async def api_opening(request: Request) -> dict:
    """暖场问候 + 服务 metadata。前端首次进入对话页时调一次。"""
    user = require_user(request)
    return {
        "greeting": opening_greeting(),
        "version": __version__,
        "user": user.public(),
    }


@app.get("/api/conversation/session")
async def api_conversation_session(request: Request, session_id: str = "") -> dict:
    """认领并恢复当前账号的可见会话；跨账号复用相同 id 时明确拒绝。"""
    user = require_user(request)
    try:
        claim_session(user.id, session_id)
        messages = session_messages(user.id, session_id, limit=60)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"session_id": session_id, "messages": messages}


@app.post("/api/baseline/assessments")
async def api_create_baseline_assessment(request: Request) -> dict:
    """用 BaselineEvaluator 创建并持久化当前 user+session 的入学摸底。"""
    user = require_user(request)
    _enforce_rate_limit(
        request,
        scope="baseline-create",
        actor=f"user:{user.id}",
        default_limit=8,
    )
    _require_llm_quota(user.id)
    body = await request.json()
    try:
        with usage_context(user.id, "baseline_create"):
            return await asyncio.to_thread(
                create_assessment,
                user_id=user.id,
                session_id=(body.get("session_id") or "").strip(),
                domain=(body.get("domain") or "").strip(),
                baseline_hint=(body.get("baseline_hint") or "").strip(),
                num_questions=body.get("num_questions") or 6,
                mode=(body.get("mode") or "quick").strip(),
                force_new=bool(body.get("force_new", False)),
            )
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/baseline/assessments/current")
async def api_current_baseline_assessment(request: Request, session_id: str = "") -> dict:
    """刷新后按 user+session 恢复同一 assessment_id。"""
    user = require_user(request)
    if not session_id.strip():
        raise HTTPException(400, "session_id 必填")
    assessment = get_current_assessment(user_id=user.id, session_id=session_id)
    return {"assessment": assessment}


@app.get("/api/baseline/assessments/{assessment_id}")
async def api_get_baseline_assessment(assessment_id: str, request: Request) -> dict:
    """恢复指定 attempt 的阶段、进度、已保存答案和评分结果。"""
    user = require_user(request)
    try:
        return get_assessment(user_id=user.id, assessment_id=assessment_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.put("/api/baseline/assessments/{assessment_id}/responses/{question_id}")
async def api_save_baseline_response(assessment_id: str, question_id: str, request: Request) -> dict:
    """逐题保存答案；刷新或断网恢复时不丢失已作答内容。"""
    user = require_user(request)
    body = await request.json()
    try:
        return save_response(
            user_id=user.id,
            assessment_id=assessment_id,
            question_id=question_id,
            payload=body,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/baseline/assessments/{assessment_id}/assist")
async def api_baseline_assist(assessment_id: str, request: Request) -> dict:
    """AI 协作阶段的站内助手；过程进入评分证据，不直接替用户作答。"""
    user = require_user(request)
    _enforce_rate_limit(request, scope="baseline-assist", actor=f"user:{user.id}", default_limit=20)
    _require_llm_quota(user.id)
    body = await request.json()
    try:
        with usage_context(user.id, "baseline_assist"):
            return await asyncio.to_thread(
                request_assistance,
                user_id=user.id,
                assessment_id=assessment_id,
                question_id=(body.get("question_id") or "").strip(),
                prompt=(body.get("prompt") or "").strip(),
            )
    except LLMError as exc:
        raise HTTPException(503, f"AI 协作助手暂不可用：{exc}") from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/baseline/assessments/{assessment_id}/submit")
async def api_submit_baseline_assessment(assessment_id: str, request: Request) -> dict:
    """提交当前阶段；独立阶段完成后进入 AI 协作，末阶段完成后客观判分。"""
    user = require_user(request)
    _enforce_rate_limit(request, scope="baseline-submit", actor=f"user:{user.id}", default_limit=12)
    _require_llm_quota(user.id)
    body = await request.json()
    try:
        with usage_context(user.id, "baseline_score"):
            result = await asyncio.to_thread(
                submit_assessment,
                user_id=user.id,
                assessment_id=assessment_id,
                responses=body.get("responses") or [],
                phase=(body.get("phase") or "").strip(),
            )
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return result


@app.post("/api/baseline/assessments/{assessment_id}/dispute")
async def api_dispute_baseline_score(assessment_id: str, request: Request) -> dict:
    """登记用户对评分证据的异议；原始画像保留，进入人工复核队列。"""
    user = require_user(request)
    body = await request.json()
    try:
        return create_dispute(
            user_id=user.id,
            assessment_id=assessment_id,
            evidence_id=(body.get("evidence_id") or "").strip(),
            reason=(body.get("reason") or "").strip(),
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


def _tag_instance_owner(instance_id: str, user_id: int) -> None:
    """把 user_id 写进实例 meta.json 与 _index.json 条目，建立归属。"""
    meta_path = INSTANCES_DIR / instance_id / "meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["user_id"] = user_id
            meta_path.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except (json.JSONDecodeError, OSError):
            pass

    def _set(entry: dict) -> dict:
        entry["user_id"] = user_id
        return entry

    _rewrite_index_entry(instance_id, mutate=_set)


def _generate_hard_timeout_seconds() -> float:
    """路径生成的可见硬超时；后台任务仍可能完成并落盘，避免用户无限等待。"""
    try:
        return max(180.0, float(os.getenv("ITUTOR_GENERATE_HARD_TIMEOUT", "420")))
    except ValueError:
        return 420.0


_GENERATE_JOBS: dict[str, dict] = {}


def _job_snapshot(job: dict, *, exclude: set[str] | None = None) -> dict:
    """生成可持久化快照，排除 asyncio task 和大体积最终内容。"""
    blocked = {"task", "lesson", *(exclude or set())}
    return {key: value for key, value in job.items() if key not in blocked}


def _persist_generate_job(job: dict) -> None:
    user_id = int(job.get("user_id") or 0)
    if user_id <= 0 or not job.get("id"):
        return
    try:
        save_background_job(
            job_id=str(job["id"]),
            user_id=user_id,
            kind="generate",
            status=str(job.get("status") or "generating"),
            instance_id=str(job.get("instance_id") or ""),
            stage_index=int(job.get("stage_index") or 0),
            payload=_job_snapshot(job),
            result={
                "instance_id": job.get("instance_id"),
                "redirect_url": job.get("redirect_url"),
            },
            error=str(job.get("error") or ""),
            started_at=job.get("started_at"),
            finished_at=job.get("finished_at"),
        )
        job.pop("persistence_error", None)
    except Exception as exc:  # noqa: BLE001
        job["persistence_error"] = f"{type(exc).__name__}: {exc}"


def _load_generate_job(user_id: int, job_id: str) -> dict | None:
    try:
        restored = get_background_job(user_id, job_id, kind="generate")
    except Exception:  # noqa: BLE001 - 服务未完成启动时仍保留内存回退
        return None
    if restored:
        _GENERATE_JOBS[job_id] = restored
    return restored


def _new_generate_job_id(user_id: int, domain: str, target: str) -> str:
    raw = f"{user_id}:{domain}:{target}:{datetime.now().isoformat(timespec='microseconds')}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def _set_generate_job_stage(job: dict, index: int) -> None:
    safe_index = max(0, min(index, len(_GENERATE_PROGRESS_STEPS) - 1))
    if safe_index < int(job.get("stage_index") or 0):
        return
    step = _GENERATE_PROGRESS_STEPS[safe_index]
    job["stage_index"] = safe_index
    job["stage_label"] = step["label"]
    job["stage_detail"] = step["detail"]
    job["updated_at"] = datetime.now()
    _persist_generate_job(job)


def _generate_job_elapsed(job: dict) -> int:
    started = job.get("started_at")
    if isinstance(started, datetime):
        return max(0, int((datetime.now() - started).total_seconds()))
    return 0


def _job_task_running(job: dict) -> bool:
    task = job.get("task")
    if task is None:
        return False
    done = getattr(task, "done", None)
    if not callable(done):
        return False
    try:
        return not bool(done())
    except Exception:  # noqa: BLE001
        return False


def _generate_job_steps(job: dict) -> list[dict]:
    status = job.get("status", "generating")
    idx = int(job.get("stage_index") or 0)
    steps = []
    for i, step in enumerate(_GENERATE_PROGRESS_STEPS):
        if status == "ready":
            state = "done"
        elif status == "error" and i == idx:
            state = "error"
        elif i < idx:
            state = "done"
        elif i == idx and status == "generating":
            state = "active"
        else:
            state = "waiting"
        steps.append({**step, "index": i, "status": state})
    return steps


def _expire_generate_job_if_needed(job: dict) -> bool:
    if job.get("status") != "generating":
        return False
    elapsed = _generate_job_elapsed(job)
    hard_timeout = int(_generate_hard_timeout_seconds())
    if elapsed < hard_timeout:
        return False
    if _job_task_running(job):
        job["deadline_exceeded"] = True
        job["can_retry"] = True
        job["deadline_detail"] = (
            f"学习路径生成已经超过 {hard_timeout} 秒，后台任务仍在运行。"
            "LearnBuddy 会继续检查结果；如果已经保存，完成后会自动进入。"
        )
        job["updated_at"] = datetime.now()
        _persist_generate_job(job)
        return True
    job["status"] = "error"
    job["timed_out"] = True
    job["can_retry"] = True
    job["error"] = f"学习路径生成超过 {hard_timeout} 秒仍未完成，后台任务已停止或失联。你可以返回学习空间查看是否已保存，或重新发起生成。"
    job["finished_at"] = datetime.now()
    _persist_generate_job(job)
    return True


def _generate_job_payload(job: dict, *, message: str = "") -> dict:
    _expire_generate_job_if_needed(job)
    status = job.get("status", "generating")
    elapsed = _generate_job_elapsed(job)
    idx = int(job.get("stage_index") or 0)
    step = _GENERATE_PROGRESS_STEPS[max(0, min(idx, len(_GENERATE_PROGRESS_STEPS) - 1))]
    if status == "ready":
        progress = 100
    elif status == "error":
        progress = min(96, round(((idx + 0.7) / len(_GENERATE_PROGRESS_STEPS)) * 100))
    else:
        progress = min(96, round(((idx + 0.45) / len(_GENERATE_PROGRESS_STEPS)) * 100))
    return {
        "status": status,
        "job_id": job.get("id"),
        "instance_id": job.get("instance_id"),
        "redirect_url": job.get("redirect_url"),
        "elapsed_seconds": elapsed,
        "elapsed": elapsed,
        "timeout_seconds": int(_generate_hard_timeout_seconds()),
        "stage_index": idx,
        "index": idx,
        "stage_label": job.get("stage_label") or step["label"],
        "stage_detail": job.get("stage_detail") or step["detail"],
        "label": job.get("stage_label") or step["label"],
        "detail": job.get("stage_detail") or step["detail"],
        "message": message or job.get("deadline_detail") or job.get("error") or "学习路径正在后台生成，完成后会自动进入学习空间。",
        "progress_percent": progress,
        "steps": _generate_job_steps(job),
        "can_retry": bool(job.get("can_retry") or job.get("timed_out") or job.get("deadline_exceeded")),
        "deadline_exceeded": bool(job.get("deadline_exceeded")),
        "error": job.get("error"),
    }


def _start_generate_job(*, user_id: int, domain: str, target: str) -> dict:
    job = {
        "id": _new_generate_job_id(user_id, domain, target),
        "user_id": user_id,
        "domain": domain,
        "target": target,
        "status": "generating",
        "started_at": datetime.now(),
    }
    _set_generate_job_stage(job, 0)
    _GENERATE_JOBS[job["id"]] = job
    _persist_generate_job(job)
    return job


def _run_generate_job_for_user(
    *,
    user_id: int,
    params,
    job_id: str = "",
    session_id: str = "",
) -> Path:
    """生成路径并在后台完成归属绑定；即使 SSE 断开也不能丢实例。"""
    job = _GENERATE_JOBS.get(job_id)
    stage_map = {
        "profile": 0,
        "research": 1,
        "outline": 2,
        "concept": 2,
        "plan": 3,
        "quality": 4,
    }

    def progress(stage_id: str) -> None:
        if job is not None:
            _set_generate_job_stage(job, stage_map.get(stage_id, 0))

    baseline_profile = submitted_profile_for_session(user_id=user_id, session_id=session_id)
    if baseline_profile is not None:
        params.baseline_profile = baseline_profile
        params.baseline_summary = baseline_profile.summary

    knowledge = search_user_knowledge(
        user_id,
        f"{params.domain} {params.target} {params.baseline_summary}",
        k=6,
    )
    user_sources = [
        {
            "source_id": hit.get("source_id"),
            "instance_id": hit.get("instance_id"),
            "title": hit.get("source_title"),
            "text": hit.get("text"),
            "score": hit.get("score"),
        }
        for hit in knowledge.get("hits") or []
    ]
    with usage_context(user_id, "generate"):
        inst_dir = generate_instance(
            params,
            use_outline_llm=True,
            use_outline_web_search=True,
            outline_user_sources=user_sources,
            outline_max_revisions=2,
            require_outline_quality=True,
            progress=progress,
        )
    _tag_instance_owner(inst_dir.name, user_id)
    bind_assessment_to_instance(
        user_id=user_id,
        session_id=session_id,
        instance_id=inst_dir.name,
    )
    try:
        log_event(user_id, "generate", params.domain, inst_dir.name)
    except Exception:
        pass
    return inst_dir


def _attach_generate_job_task(job: dict, task) -> dict:
    job["task"] = task

    def _done(done_task) -> None:
        try:
            inst_dir = done_task.result()
            job["instance_id"] = inst_dir.name
            job["redirect_url"] = f"/instance/{inst_dir.name}"
            job["status"] = "ready"
            job["can_retry"] = False
            job["timed_out"] = False
            job["deadline_exceeded"] = False
            job.pop("error", None)
            job.pop("deadline_detail", None)
            _set_generate_job_stage(job, len(_GENERATE_PROGRESS_STEPS) - 1)
        except Exception as exc:  # noqa: BLE001
            job["status"] = "error"
            job["error"] = f"{type(exc).__name__}: {exc}"
            job["can_retry"] = True
        job["finished_at"] = datetime.now()
        _persist_generate_job(job)

    task.add_done_callback(_done)
    return job


def _lesson_cache_path(inst_dir: Path, topic: str) -> Path:
    """学练单元缓存文件路径：data/instances/{id}/lessons/{topic 哈希}.json。

    用归一化主题的短哈希做文件名，规避中文/空格/标点带来的路径问题。
    """
    key = hashlib.sha1(topic.strip().lower().encode("utf-8")).hexdigest()[:16]
    return inst_dir / "lessons" / f"{key}.json"


LESSON_CACHE_VERSION = "lesson-cache.v0.50"


def _lesson_cache_metadata(saved: dict, lesson: dict) -> dict:
    """判定缓存能否按当前内容合同直接复用。"""
    version = str(saved.get("cache_version") or "legacy")
    quality = lesson.get("_quality") if isinstance(lesson.get("_quality"), dict) else {}
    quality_passed = bool(quality.get("passed")) and not bool(lesson.get("_fallback"))
    if version != LESSON_CACHE_VERSION:
        status = "stale_version"
    elif not quality_passed:
        status = "stale_quality"
    else:
        status = "current"
    return {
        "cache_status": status,
        "cache_version": version,
        "cache_current": status == "current",
        "quality_passed": quality_passed,
    }


def _write_lesson_cache_atomic(cache_path: Path, payload: dict) -> None:
    """先写临时文件再替换，升级异常时保留旧缓存。"""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = cache_path.with_name(f".{cache_path.name}.{time.time_ns()}.tmp")
    try:
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(cache_path)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _display_sources_from_pack(source_pack: dict, *, start: int = 1) -> list[dict]:
    """把来源包转换为前端可展示的 _sources。"""
    web_sources = source_pack.get("sources") or []
    display_sources: list[dict] = []
    for n, src in enumerate(web_sources, start):
        display_sources.append(
            {
                "n": n,
                "title": src.get("title", ""),
                "url": src.get("url", ""),
                "site": src.get("site", ""),
                "kind": src.get("kind", "web"),
                "source_type": src.get("source_type", src.get("kind", "web")),
                "date_published": src.get("date_published", ""),
                "summary": src.get("summary", ""),
                "authority_score": src.get("authority_score"),
                "freshness_score": src.get("freshness_score"),
                "relevance_score": src.get("relevance_score"),
            }
        )
    return display_sources


def _prepare_lesson_runtime(
    lesson: dict,
    *,
    source_pack: dict | None,
    query: str,
    generated_at: str = "",
    context: str = "",
) -> dict:
    """给新旧 lesson 补齐运行时元数据，不迁移、不改旧结构。"""
    normalized_pack = normalize_source_pack(source_pack or lesson.get("_source_pack") or {}, fallback_query=query, count=4)
    lesson["_source_pack"] = normalized_pack
    if normalized_pack.get("sources"):
        lesson["_sources"] = _display_sources_from_pack(normalized_pack)
    if not isinstance(lesson.get("_quality"), dict) or not lesson.get("_quality", {}).get("dimension_scores"):
        quality_context = context or format_sources_context(normalized_pack.get("sources") or [])
        lesson["_quality"] = evaluate_lesson_quality(lesson, context=quality_context)
    lesson["_quality_report"] = lesson.get("_quality") or {}
    lesson["_lesson_contract"] = {
        "version": "v0.46",
        "name": "teaching-content-loop-v1",
        "requires": [
            "grounded_sources",
            "structured_design",
            "interactive_cards",
            "active_produce",
            "quality_gate",
            "runtime_persistence",
        ],
    }
    attach_lesson_flow(lesson, source_pack=normalized_pack, generated_at=generated_at or "")
    return lesson


def _instance_owner(instance_id: str) -> int | None:
    """读实例 meta.json 的 user_id；无归属（老实例）返回 None。"""
    meta_path = INSTANCES_DIR / instance_id / "meta.json"
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8")).get("user_id")
    except (json.JSONDecodeError, OSError):
        return None


def _coerce_user_id(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _instance_owner_for_index_entry(entry: dict) -> int | None:
    """实例列表的归属判断：索引优先，缺失时回读 meta.json。

    生成链路是先写 index，再补 user_id；如果中途刷新、异常或历史数据缺字段，
    只看 index 会让用户在学习空间里看不到自己的路径。
    """
    owner = _coerce_user_id(entry.get("user_id"))
    if owner is not None:
        return owner
    instance_id = entry.get("id")
    if not instance_id:
        return None
    return _instance_owner(str(instance_id))


def _repair_index_entry_owner(entry: dict, owner: int | None) -> tuple[dict, bool]:
    if owner is None or _coerce_user_id(entry.get("user_id")) == owner:
        return entry, False
    repaired = dict(entry)
    repaired["user_id"] = owner
    return repaired, True


def _assert_can_access(instance_id: str, user: User) -> None:
    """实例必须属于当前用户；管理员可访问全部。无归属实例不再对外公开（防跨用户泄漏）。"""
    if getattr(user, "is_admin", False):
        return
    owner = _instance_owner(instance_id)
    if owner != user.id:
        raise HTTPException(403, "无权访问该学习路径")


def _instance_progress_summary(instance_id: str, user_id: int) -> dict:
    """把 path_loop 的完成事实压缩成学习空间可直接展示的进度摘要。"""
    fallback = {
        "source": "path_loop",
        "status": "active",
        "current_week": 1,
        "current_day": 1,
        "completed_units": 0,
        "total_units": 0,
        "progress_percent": 0,
    }
    inst_dir = INSTANCES_DIR / instance_id
    if not inst_dir.is_dir():
        return fallback
    try:
        state = load_path_loop_state(
            inst_dir,
            user_id,
            studied_topics(user_id, instance_id),
            persist=False,
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return fallback

    units = state.get("units") if isinstance(state.get("units"), list) else []
    completed = sum(1 for unit in units if isinstance(unit, dict) and unit.get("completed"))
    current = state.get("current_unit") if isinstance(state.get("current_unit"), dict) else None
    if current is None and units:
        current = units[-1] if isinstance(units[-1], dict) else None
    total = len(units)
    return {
        "source": "path_loop",
        "status": str(state.get("status") or "active"),
        "current_week": max(1, int((current or {}).get("week") or 1)),
        "current_day": max(1, int((current or {}).get("day") or 1)),
        "completed_units": completed,
        "total_units": total,
        "progress_percent": round((completed / total) * 100) if total else 0,
    }


@app.get("/api/instances")
async def api_list_instances(request: Request) -> dict:
    """当前用户的实例列表；索引缺归属时从 meta.json 兜底修复。"""
    user = require_user(request)
    index = _read_instance_index()
    repaired_any = False
    all_entries = []
    mine = []
    for entry in index.get("instances", []):
        owner = _instance_owner_for_index_entry(entry)
        fixed, repaired = _repair_index_entry_owner(entry, owner)
        repaired_any = repaired_any or repaired
        all_entries.append(fixed)
        if user.is_admin or owner == user.id:
            progress_user_id = owner if owner is not None else user.id
            mine.append({
                **fixed,
                "progress": _instance_progress_summary(str(fixed.get("id") or ""), progress_user_id),
            })
    if repaired_any:
        index["instances"] = all_entries
        index["count"] = len(all_entries)
        index["_updated_at"] = datetime.now().isoformat(timespec="seconds")
        (INSTANCES_DIR / "_index.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    index["instances"] = mine
    index["count"] = len(mine)
    index["limits"] = _instance_limits(user.id)
    return index


@app.get("/api/tools")
async def api_tools(request: Request) -> dict:
    """统一工具注册表（透明）：列出 core/tools.py 暴露给端点 / MCP / agent 的能力。

    这是 ADR-011「工具注册层是动态工作流 + agent + OpenClaw 的公共地基」的可观测出口；
    架构总览页据此呈现"能力层"。
    """
    require_user(request)
    from core.tools import list_tools

    tools = list_tools()
    return {"count": len(tools), "tools": tools}


@app.get("/api/overview")
async def api_overview(request: Request) -> dict:
    """跨学习路径聚合：总览数字 + 今日聚焦（待复习/弱项）+ 每套真实进度。

    给学习空间首页用——回答"我今天该学什么、哪些到期复习"。
    """
    user = require_user(request)
    index_path = INSTANCES_DIR / "_index.json"
    instances = []
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
        instances = [
            e
            for e in index.get("instances", [])
            if user.is_admin or _instance_owner_for_index_entry(e) == user.id
        ]

    total_topics = 0
    total_due = 0
    total_weak = 0
    total_answers = 0
    total_correct = 0
    per_instance = {}
    focus = []  # 今日聚焦条目（跨系统的待复习）

    for e in instances:
        iid = e.get("id")
        if not iid:
            continue
        summ = analytics_summary(user.id, iid)
        review = summ.get("review_queue", [])
        total_topics += summ.get("topics_studied", 0)
        total_due += len(review)
        total_weak += len(summ.get("weak_points", []))
        total_answers += summ.get("answers_total", 0)
        total_correct += summ.get("answers_correct", 0)
        per_instance[iid] = {
            "topics_studied": summ.get("topics_studied", 0),
            "due": len(review),
            "objective_pct": summ.get("objective_pct", 0),
        }
        for r in review[:3]:
            focus.append(
                {
                    "instance_id": iid,
                    "domain": e.get("domain", ""),
                    "topic": r["topic"],
                    "next_due": r["next_due"],
                    "self_label": r.get("self_label", "—"),
                }
            )

    focus.sort(key=lambda x: x["next_due"])
    return {
        "systems": len(instances),
        "active": len([e for e in instances if (e.get("status") or "draft") in _CURRENT_INSTANCE_STATUSES]),
        "limits": _instance_limits(user.id),
        "topics_studied": total_topics,
        "due_count": total_due,
        "weak_count": total_weak,
        "objective_pct": round(total_correct / total_answers * 100) if total_answers else 0,
        "focus": focus[:6],
        "per_instance": per_instance,
    }


@app.get("/api/instance/{instance_id}")
async def api_get_instance(request: Request, instance_id: str) -> dict:
    """拉取实例 5 件套全部数据，给 instance.html 渲染用。"""
    user = require_user(request)
    inst_dir = INSTANCES_DIR / instance_id
    if not inst_dir.exists() or not inst_dir.is_dir():
        raise HTTPException(404, f"实例 {instance_id} 不存在")
    _assert_can_access(instance_id, user)

    def _read_text(name: str) -> str:
        p = inst_dir / name
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def _read_json(name: str) -> dict:
        p = inst_dir / name
        if not p.exists():
            return {}
        return json.loads(p.read_text(encoding="utf-8"))

    def _read_week_plans() -> dict[str, dict]:
        plans: dict[str, dict] = {}
        for p in sorted(inst_dir.glob("W*.json")):
            suffix = p.stem[1:]
            if not suffix.isdigit():
                continue
            try:
                plans[suffix] = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
        return plans

    from core.self_check import self_check_history

    return {
        "id": instance_id,
        "meta": _read_json("meta.json"),
        "master": _read_json("master.json"),
        "w1": _read_json("W1.json"),
        "week_plans": _read_week_plans(),
        "learning_manual": _read_text("学习手册.md"),
        "vision_contract": _read_text("愿景与契约.md"),
        "path_loop": _path_loop_state(instance_id, user.id),
        "self_checks": await asyncio.to_thread(self_check_history, user.id, instance_id, 24, inst_dir),
    }


@app.get("/api/instance/{instance_id}/path-loop")
async def api_path_loop(instance_id: str, request: Request) -> dict:
    """路径级 Loop 状态：当前天、已完成天、下一天预生成状态。"""
    user = require_user(request)
    inst_dir = INSTANCES_DIR / instance_id
    if not inst_dir.exists() or not inst_dir.is_dir():
        raise HTTPException(404, f"实例 {instance_id} 不存在")
    _assert_can_access(instance_id, user)
    return _path_loop_state(instance_id, user.id)


@app.patch("/api/instance/{instance_id}/plan")
async def api_update_instance_plan(instance_id: str, request: Request) -> dict:
    """编辑学习计划的目标、周主题与关键产出。只改 JSON 蓝图，不清空学习记录。"""
    user = require_user(request)
    inst_dir = INSTANCES_DIR / instance_id
    if not inst_dir.exists() or not inst_dir.is_dir():
        raise HTTPException(404, f"实例 {instance_id} 不存在")
    _assert_can_access(instance_id, user)

    body = await request.json()
    master_path = inst_dir / "master.json"
    meta_path = inst_dir / "meta.json"
    master = json.loads(master_path.read_text(encoding="utf-8")) if master_path.exists() else {}
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    north = master.setdefault("north_star", {})
    if "target" in body:
        north["target"] = str(body.get("target") or "").strip()[:300]
        meta["target"] = north["target"]
    if "domain" in body:
        north["domain"] = str(body.get("domain") or "").strip()[:120]
        meta["domain"] = north["domain"]

    incoming_weeks = body.get("weeks") if isinstance(body.get("weeks"), list) else None
    if incoming_weeks is not None:
        weeks = []
        for i, w in enumerate(incoming_weeks[:52], 1):
            if not isinstance(w, dict):
                continue
            try:
                week_no = max(1, int(w.get("week") or i))
            except (TypeError, ValueError):
                week_no = i
            title = str(w.get("title") or f"第 {week_no} 周").strip()[:180]
            outcomes = [
                str(x).strip()[:180]
                for x in (w.get("key_outcomes") or [])
                if str(x).strip()
            ][:8]
            weeks.append({
                "week": week_no,
                "title": title,
                "date_range": str(w.get("date_range") or "").strip()[:80],
                "key_outcomes": outcomes,
            })
        if weeks:
            master["weeks"] = weeks
            meta["weeks"] = len(weeks)

    now = datetime.now().isoformat(timespec="seconds")
    meta["updated_at"] = now
    master_path.write_text(json.dumps(master, ensure_ascii=False, indent=2), encoding="utf-8")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def _mut(e):
        e["domain"] = meta.get("domain") or e.get("domain")
        e["target"] = meta.get("target") or e.get("target")
        e["weeks"] = meta.get("weeks") or e.get("weeks")
        e["updated_at"] = now
        return e

    _rewrite_index_entry(instance_id, mutate=_mut)
    log_event(user.id, "edit_plan", (meta.get("target") or "")[:120], instance_id)
    return {"ok": True, "meta": meta, "master": master}


# ============================================================================
# API · 实例状态管理（多任务书架用）
# ============================================================================


_VALID_STATUSES = {"draft", "active", "paused", "done", "archived"}
_CURRENT_INSTANCE_STATUSES = {"draft", "active", "paused"}


def _max_current_instances() -> int:
    """每个用户最多同时推进的学习路径数；默认 5，可用环境变量微调。"""
    try:
        return max(1, int(os.getenv("ITUTOR_MAX_CURRENT_INSTANCES", "5")))
    except ValueError:
        return 5


def _lesson_timeout_seconds() -> float:
    """学练单元首个请求最长等待时间；超时后转后台轮询，避免前端长 HTTP 卡住。"""
    try:
        return max(15.0, float(os.getenv("ITUTOR_LESSON_TIMEOUT", "45")))
    except ValueError:
        return 45.0


def _lesson_hard_timeout_seconds() -> float:
    """后台生成硬超时；超过后任务进入可重试错误态，避免一直显示进行中。"""
    try:
        configured = float(os.getenv("ITUTOR_LESSON_HARD_TIMEOUT", "300"))
    except ValueError:
        configured = 300.0
    return max(_lesson_timeout_seconds() + 30.0, configured)


_LESSON_STAGE_DEFS = [
    {"id": "cache", "label": "检查是否已有保存内容", "detail": "如果这节以前生成过，会直接复用。"},
    {"id": "context", "label": "读取路径上下文", "detail": "整理目标、基线、当前计划和这一节的知识点。"},
    {"id": "kb", "label": "检索你的资料库", "detail": "先查你上传或沉淀过的资料，作为事实锚点。"},
    {"id": "sources", "label": "整理外部参考来源", "detail": "筛选权威网页、文档或案例来源，丢掉低质内容。"},
    {"id": "source_fetch", "label": "抓取来源正文", "detail": "把高质量来源的正文作为事实锚点，避免只看摘要。"},
    {"id": "lesson_design", "label": "设计这一节怎么教", "detail": "想清楚本节的核心问题、主线和一个非显然的判断。"},
    {"id": "lesson_sections", "label": "拆分完整学习单元", "detail": "把 1–2 小时内容拆成讲解、例题、练习和产出几段分别生成。"},
    {"id": "lesson_section_core", "label": "生成核心讲解", "detail": "先写目标、来源、先想问题和核心机制/边界卡。"},
    {"id": "lesson_section_examples", "label": "生成完整例题", "detail": "补 worked example、失败分支和边界例子。"},
    {"id": "lesson_section_practice", "label": "生成分层练习", "detail": "生成互动题、干扰项和解析。"},
    {"id": "lesson_section_produce", "label": "生成主动产出", "detail": "生成任务、活动链和 rubric。"},
    {"id": "lesson_write", "label": "生成讲解与练习", "detail": "围绕课设主线写讲解卡片、互动题和输出任务。"},
    {"id": "lesson_expand", "label": "分段补强核心内容", "detail": "按质量门缺口补深讲卡、边界反例和 worked example，不重写整节课。"},
    {"id": "lesson_critic", "label": "锐度评审", "detail": "判断内容是否太通用、有没有真洞见、讲透没有。"},
    {"id": "lesson_revise", "label": "按评审定向打磨", "detail": "针对评审指出的问题做有边界的定向重写，并保留最佳版本。"},
    {"id": "quality_gate", "label": "质量审查与保存", "detail": "检查准确性、深度、互动性，并保存为下次秒开。"},
]
_LESSON_STAGE_INDEX = {s["id"]: i for i, s in enumerate(_LESSON_STAGE_DEFS)}
# 单段式兜底链路的旧阶段名 → 映射到流水线展示阶段，进度条不回退也不出现孤立阶段。
_LESSON_STAGE_ALIAS = {
    "lesson_draft": "lesson_write",
    "lesson_review": "lesson_critic",
}


_LESSON_JOBS: dict[str, dict] = {}


def _persist_lesson_job(job: dict) -> None:
    user_id = int(job.get("user_id") or 0)
    if user_id <= 0 or not job.get("id"):
        return
    try:
        save_background_job(
            job_id=str(job["id"]),
            user_id=user_id,
            kind="lesson",
            status=str(job.get("status") or "generating"),
            instance_id=str(job.get("instance_id") or ""),
            stage_index=int(job.get("stage_index") or 0),
            stage_id=str(job.get("stage_id") or ""),
            payload=_job_snapshot(job),
            result={
                "cache_status": job.get("cache_status"),
                "recovered_from_cache": bool(job.get("recovered_from_cache")),
            },
            error=str(job.get("error") or ""),
            started_at=job.get("started_at"),
            finished_at=job.get("finished_at"),
        )
        job.pop("persistence_error", None)
    except Exception as exc:  # noqa: BLE001
        job["persistence_error"] = f"{type(exc).__name__}: {exc}"


def _load_lesson_job(user_id: int, job_id: str) -> dict | None:
    try:
        restored = get_background_job(user_id, job_id, kind="lesson")
    except Exception:  # noqa: BLE001 - 服务未完成启动时仍保留内存回退
        return None
    if restored:
        _LESSON_JOBS[job_id] = restored
    return restored


def _lesson_job_key(instance_id: str, topic: str, custom_instruction: str = "") -> str:
    raw = "\n".join([instance_id, topic.strip().lower(), custom_instruction.strip()])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def _lesson_stage(stage_id: str | None) -> dict:
    resolved = _LESSON_STAGE_ALIAS.get(stage_id or "", stage_id)
    if resolved in _LESSON_STAGE_INDEX:
        return _LESSON_STAGE_DEFS[_LESSON_STAGE_INDEX[resolved or "cache"]]
    return _LESSON_STAGE_DEFS[0]


def _iso_seconds(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _lesson_stage_trace(job: dict) -> list[dict]:
    trace = job.setdefault("stage_trace", [])
    if isinstance(trace, list):
        return trace
    job["stage_trace"] = []
    return job["stage_trace"]


def _current_lesson_trace_entry(job: dict) -> dict | None:
    stage_id = job.get("stage_id")
    for entry in reversed(_lesson_stage_trace(job)):
        if entry.get("id") == stage_id and entry.get("status") == "active":
            return entry
    return None


def _close_lesson_job_stage(job: dict, *, status: str = "done", now: datetime | None = None) -> None:
    entry = _current_lesson_trace_entry(job)
    if not entry:
        return
    finished = now or datetime.now()
    started = entry.get("_started_at")
    if isinstance(started, datetime):
        entry["duration_seconds"] = max(0.0, round((finished - started).total_seconds(), 2))
    entry["_finished_at"] = finished
    entry["finished_at"] = _iso_seconds(finished)
    entry["status"] = status


def _lesson_trace_payload(job: dict) -> list[dict]:
    items: list[dict] = []
    for entry in _lesson_stage_trace(job):
        items.append({k: v for k, v in entry.items() if not k.startswith("_")})
    return items


def _set_lesson_job_stage(job: dict, stage_id: str) -> None:
    stage = _lesson_stage(stage_id)
    idx = _LESSON_STAGE_INDEX[stage["id"]]
    if idx < int(job.get("stage_index") or 0):
        return
    now = datetime.now()
    previous_stage = job.get("stage_id")
    if previous_stage and previous_stage != stage["id"]:
        _close_lesson_job_stage(job, now=now)
    job["stage_id"] = stage["id"]
    job["stage_index"] = idx
    job["stage_label"] = stage["label"]
    job["stage_detail"] = stage["detail"]
    if not _current_lesson_trace_entry(job):
        _lesson_stage_trace(job).append(
            {
                "id": stage["id"],
                "index": idx,
                "label": stage["label"],
                "detail": stage["detail"],
                "status": "active",
                "_started_at": now,
                "started_at": _iso_seconds(now),
            }
        )
    job["updated_at"] = now
    _persist_lesson_job(job)


def _lesson_job_elapsed(job: dict) -> int:
    started = job.get("started_at")
    if isinstance(started, datetime):
        return max(0, int((datetime.now() - started).total_seconds()))
    return 0


def _expire_lesson_job_if_needed(job: dict) -> bool:
    if job.get("status") != "generating":
        return False
    hard_timeout = int(_lesson_hard_timeout_seconds())
    elapsed = _lesson_job_elapsed(job)
    if elapsed < hard_timeout:
        return False
    if _job_task_running(job):
        job["deadline_exceeded"] = True
        job["can_retry"] = True
        job["deadline_detail"] = (
            f"本节生成已经超过 {hard_timeout} 秒，后台任务仍在运行。"
            "LearnBuddy 会继续检查结果；如果内容已经保存，会自动打开。"
        )
        job["updated_at"] = datetime.now()
        _persist_lesson_job(job)
        return True
    _close_lesson_job_stage(job, status="error")
    job["status"] = "error"
    job["timed_out"] = True
    job["can_retry"] = True
    job["error"] = f"本节生成超过 {hard_timeout} 秒仍未完成，后台任务已停止或失联。请稍后重试或缩小重生成要求。"
    job["finished_at"] = datetime.now()
    _persist_lesson_job(job)
    return True


def _lesson_job_steps(job: dict) -> list[dict]:
    status = job.get("status", "generating")
    idx = int(job.get("stage_index") or 0)
    trace_by_id = {entry.get("id"): entry for entry in _lesson_trace_payload(job)}
    steps = []
    for i, s in enumerate(_LESSON_STAGE_DEFS):
        if status == "ready":
            state = "done"
        elif status == "error" and i == idx:
            state = "error"
        elif i < idx:
            state = "done"
        elif i == idx and status == "generating":
            state = "active"
        else:
            state = "waiting"
        trace = trace_by_id.get(s["id"]) or {}
        steps.append(
            {
                **s,
                "index": i,
                "status": state,
                "started_at": trace.get("started_at"),
                "finished_at": trace.get("finished_at"),
                "duration_seconds": trace.get("duration_seconds"),
            }
        )
    return steps


def _lesson_job_payload(job: dict, *, message: str = "") -> dict:
    _expire_lesson_job_if_needed(job)
    status = job.get("status", "generating")
    elapsed = _lesson_job_elapsed(job)
    stage = _lesson_stage(job.get("stage_id"))
    idx = int(job.get("stage_index") or _LESSON_STAGE_INDEX[stage["id"]])
    if status == "ready":
        progress = 100
    elif status == "error":
        progress = min(96, round(((idx + 0.7) / len(_LESSON_STAGE_DEFS)) * 100))
    else:
        progress = min(96, round(((idx + 0.45) / len(_LESSON_STAGE_DEFS)) * 100))
    default_detail = "本节内容仍在后台生成，完成后会自动打开。"
    if job.get("cache_status") == "upgrading":
        default_detail = "已保存内容来自旧版本或未通过当前质量门，LearnBuddy 正在后台升级；成功前不会覆盖原文件。"
    return {
        "status": status,
        "job_id": job.get("id"),
        "topic": job.get("topic", ""),
        "cache_status": job.get("cache_status") or "miss",
        "cache_version": LESSON_CACHE_VERSION,
        "previous_cache_status": job.get("previous_cache_status") or "",
        "elapsed_seconds": elapsed,
        "timeout_seconds": int(_lesson_hard_timeout_seconds()),
        "stage_id": stage["id"],
        "stage_index": idx,
        "stage_label": stage["label"],
        "stage_detail": stage["detail"],
        "progress_percent": progress,
        "steps": _lesson_job_steps(job),
        "stage_trace": _lesson_trace_payload(job),
        "can_retry": bool(job.get("can_retry") or job.get("timed_out") or job.get("deadline_exceeded")),
        "deadline_exceeded": bool(job.get("deadline_exceeded")),
        "detail": message or job.get("deadline_detail") or job.get("error") or default_detail,
    }


def _path_loop_state(instance_id: str, user_id: int) -> dict:
    inst_dir = INSTANCES_DIR / instance_id
    state = load_path_loop_state(inst_dir, user_id, studied_topics(user_id, instance_id))
    pre = state.get("pre_generation") if isinstance(state.get("pre_generation"), dict) else {}
    topic = str(pre.get("topic") or "").strip()
    if topic and pre.get("status") in {"queued", "running"}:
        ctx = _instance_lesson_context(inst_dir)
        cached = _load_cached_lesson(inst_dir, topic=topic, domain=ctx["domain"], target=ctx["target"])
        if cached and cached.get("_cache_current"):
            unit = next((u for u in state.get("units", []) if u.get("unit_id") == pre.get("unit_id")), None)
            state = set_pregeneration_state(
                inst_dir,
                user_id,
                unit=unit,
                status="succeeded",
                job_id=str(pre.get("job_id") or ""),
                detail="这一天的内容已经准备好，可以直接开始。",
                stage_id="quality_gate",
                stage_label="质量审查与保存",
                progress_percent=100,
                completed_topics=studied_topics(user_id, instance_id),
            )
    return state


def _set_path_pregen_from_job(
    *,
    user_id: int,
    instance_id: str,
    inst_dir: Path,
    unit: dict,
    job: dict,
    status: str = "running",
    detail: str = "",
    error: str = "",
) -> dict:
    payload = _lesson_job_payload(job, message=detail)
    return set_pregeneration_state(
        inst_dir,
        user_id,
        unit=unit,
        status=status,
        job_id=str(job.get("id") or ""),
        detail=detail or payload.get("detail") or "",
        error=error,
        stage_id=str(payload.get("stage_id") or ""),
        stage_label=str(payload.get("stage_label") or ""),
        progress_percent=int(payload.get("progress_percent") or 0),
        completed_topics=studied_topics(user_id, instance_id),
    )


def _maybe_start_next_pregeneration(
    *,
    user: User,
    instance_id: str,
    inst_dir: Path,
    state: dict,
) -> dict:
    """完成当前天后，异步准备下一天。只更新路径状态，不阻塞完成接口。"""
    unit = state.get("current_unit") if isinstance(state.get("current_unit"), dict) else None
    topic = str(unit.get("topic") or "").strip() if unit else ""
    if not unit or not topic:
        return set_pregeneration_state(
            inst_dir,
            user.id,
            unit=None,
            status="skipped",
            detail="这条路径已经没有待准备的下一天。",
            completed_topics=studied_topics(user.id, instance_id),
        )

    ctx = _instance_lesson_context(inst_dir)
    cache_path = _lesson_cache_path(inst_dir, topic)
    cached = _load_cached_lesson(inst_dir, topic=topic, domain=ctx["domain"], target=ctx["target"])
    if cached and cached.get("_cache_current"):
        return set_pregeneration_state(
            inst_dir,
            user.id,
            unit=unit,
            status="succeeded",
            detail="下一天内容已保存，可以直接开始。",
            stage_id="quality_gate",
            stage_label="质量审查与保存",
            progress_percent=100,
            completed_topics=studied_topics(user.id, instance_id),
        )

    job_key = _lesson_job_key(instance_id, topic, "")
    existing_job = _LESSON_JOBS.get(job_key) or _load_lesson_job(user.id, job_key)
    if existing_job:
        if existing_job.get("status") == "ready":
            return set_pregeneration_state(
                inst_dir,
                user.id,
                unit=unit,
                status="succeeded",
                job_id=job_key,
                detail="下一天内容已经准备好。",
                stage_id="quality_gate",
                stage_label="质量审查与保存",
                progress_percent=100,
                completed_topics=studied_topics(user.id, instance_id),
            )
        if existing_job.get("status") == "error":
            return _set_path_pregen_from_job(
                user_id=user.id,
                instance_id=instance_id,
                inst_dir=inst_dir,
                unit=unit,
                job=existing_job,
                status="failed",
                detail="下一天内容准备失败，可以进入当天后手动重试。",
                error=str(existing_job.get("error") or "生成失败"),
            )
        return _set_path_pregen_from_job(
            user_id=user.id,
            instance_id=instance_id,
            inst_dir=inst_dir,
            unit=unit,
            job=existing_job,
            status="running",
            detail="LearnBuddy 正在准备下一天内容。",
        )

    q = check_quota(user.id)
    if not q["allowed"]:
        return set_pregeneration_state(
            inst_dir,
            user.id,
            unit=unit,
            status="skipped",
            detail=_quota_message(q),
            error=_quota_message(q),
            completed_topics=studied_topics(user.id, instance_id),
        )

    job = _start_lesson_job(
        key=job_key,
        topic=topic,
        user_id=user.id,
        instance_id=instance_id,
        cache_status="upgrading" if cached else "miss",
        previous_cache_status=str((cached or {}).get("_cache_status") or ""),
    )

    def progress(stage_id: str) -> None:
        _set_lesson_job_stage(job, stage_id)
        try:
            _set_path_pregen_from_job(
                user_id=user.id,
                instance_id=instance_id,
                inst_dir=inst_dir,
                unit=unit,
                job=job,
                status="running",
                detail="LearnBuddy 正在准备下一天内容。",
            )
        except Exception:  # noqa: BLE001
            pass

    task = asyncio.create_task(
        asyncio.to_thread(
            _build_lesson_and_save,
            user_id=user.id,
            instance_id=instance_id,
            inst_dir=inst_dir,
            cache_path=cache_path,
            domain=ctx["domain"],
            target=ctx["target"],
            baseline=ctx["baseline"],
            topic=topic,
            custom_instruction="",
            regenerate=False,
            progress=progress,
        )
    )
    _attach_lesson_job_task(job, task)

    def _finish(_done_task) -> None:
        try:
            if job.get("status") == "ready":
                set_pregeneration_state(
                    inst_dir,
                    user.id,
                    unit=unit,
                    status="succeeded",
                    job_id=job_key,
                    detail="下一天内容已准备好，可以直接开始。",
                    stage_id="quality_gate",
                    stage_label="质量审查与保存",
                    progress_percent=100,
                    completed_topics=studied_topics(user.id, instance_id),
                )
            else:
                _set_path_pregen_from_job(
                    user_id=user.id,
                    instance_id=instance_id,
                    inst_dir=inst_dir,
                    unit=unit,
                    job=job,
                    status="failed",
                    detail="下一天内容准备失败，可以进入当天后手动重试。",
                    error=str(job.get("error") or "生成失败"),
                )
        except Exception:  # noqa: BLE001
            pass

    task.add_done_callback(_finish)
    return _set_path_pregen_from_job(
        user_id=user.id,
        instance_id=instance_id,
        inst_dir=inst_dir,
        unit=unit,
        job=job,
        status="running",
        detail="LearnBuddy 正在准备下一天内容。",
    )


def _read_instance_index() -> dict:
    index_path = INSTANCES_DIR / "_index.json"
    if not index_path.exists():
        return {"instances": [], "count": 0, "_updated_at": None}
    return json.loads(index_path.read_text(encoding="utf-8"))


def _user_instance_entries(user_id: int) -> list[dict]:
    index = _read_instance_index()
    return [
        e
        for e in index.get("instances", [])
        if _instance_owner_for_index_entry(e) == user_id
    ]


def _instance_limits(user_id: int) -> dict:
    max_current = _max_current_instances()
    current = len([
        e
        for e in _user_instance_entries(user_id)
        if (e.get("status") or "draft") in _CURRENT_INSTANCE_STATUSES
    ])
    return {
        "max": max_current,
        "current": current,
        "remaining": max(0, max_current - current),
    }


def _instance_limit_message(limits: dict) -> str:
    return (
        f"你当前已有 {limits.get('current', 0)}/{limits.get('max', 5)} 个在建学习任务。"
        "先删除、完成或归档一个，再新建下一条学习路径。"
    )


def _assert_can_create_instance(user_id: int) -> None:
    limits = _instance_limits(user_id)
    if limits["remaining"] <= 0:
        raise HTTPException(409, _instance_limit_message(limits))


def _rewrite_index_entry(instance_id: str, *, mutate) -> None:
    """读 _index.json，对匹配 id 的条目应用 mutate(entry)；mutate 返回 None 表示删除。"""
    index_path = INSTANCES_DIR / "_index.json"
    if not index_path.exists():
        return
    index = json.loads(index_path.read_text(encoding="utf-8"))
    new_list = []
    for e in index.get("instances", []):
        if e.get("id") == instance_id:
            updated = mutate(e)
            if updated is not None:
                new_list.append(updated)
        else:
            new_list.append(e)
    index["instances"] = new_list
    index["count"] = len(new_list)
    index["_updated_at"] = datetime.now().isoformat(timespec="seconds")
    index_path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )


@app.post("/api/instance/{instance_id}/status")
async def api_set_status(instance_id: str, request: Request) -> dict:
    """更新实例状态（active / paused / done / archived / draft），同步 meta + index。"""
    user = require_user(request)
    inst_dir = INSTANCES_DIR / instance_id
    if not inst_dir.is_dir():
        raise HTTPException(404, f"实例 {instance_id} 不存在")
    _assert_can_access(instance_id, user)
    body = await request.json()
    status = (body.get("status") or "").strip()
    if status not in _VALID_STATUSES:
        raise HTTPException(400, f"非法状态 {status!r}，允许：{sorted(_VALID_STATUSES)}")

    meta_path = inst_dir / "meta.json"
    old_status = "draft"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        old_status = meta.get("status") or old_status
        if status in _CURRENT_INSTANCE_STATUSES and old_status not in _CURRENT_INSTANCE_STATUSES:
            _assert_can_create_instance(user.id)
        meta["status"] = status
        meta["updated_at"] = datetime.now().isoformat(timespec="seconds")
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    elif status in _CURRENT_INSTANCE_STATUSES:
        _assert_can_create_instance(user.id)

    def _set(entry: dict) -> dict:
        entry["status"] = status
        return entry

    _rewrite_index_entry(instance_id, mutate=_set)
    return {"id": instance_id, "status": status}


@app.delete("/api/instance/{instance_id}")
async def api_delete_instance(instance_id: str, request: Request) -> dict:
    """彻底删除实例（删目录 + 从 index 移除）。"""
    user = require_user(request)
    inst_dir = INSTANCES_DIR / instance_id
    if not inst_dir.is_dir():
        raise HTTPException(404, f"实例 {instance_id} 不存在")
    _assert_can_access(instance_id, user)
    shutil.rmtree(inst_dir)
    _rewrite_index_entry(instance_id, mutate=lambda e: None)
    return {"id": instance_id, "deleted": True}


# ============================================================================
# API · 学练单元（内容生成 agent · 实时出课）
# ============================================================================


def _load_cached_lesson(
    inst_dir: Path,
    *,
    topic: str,
    domain: str,
    target: str,
) -> dict | None:
    cache_path = _lesson_cache_path(inst_dir, topic)
    if not cache_path.exists():
        return None
    try:
        saved = json.loads(cache_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    lesson = saved.get("lesson", saved)
    if not isinstance(lesson, dict):
        return None
    ensure_lesson_cards(lesson)
    lesson["_cached"] = True
    lesson["_generated_at"] = saved.get("generated_at") or lesson.get("_generated_at")
    _prepare_lesson_runtime(
        lesson,
        source_pack=lesson.get("_source_pack"),
        query=" ".join(x for x in (domain, target, topic) if x),
        generated_at=lesson.get("_generated_at") or "",
    )
    cache_meta = _lesson_cache_metadata(saved if isinstance(saved, dict) else {}, lesson)
    lesson["_cache_status"] = cache_meta["cache_status"]
    lesson["_cache_version"] = cache_meta["cache_version"]
    lesson["_cache_current"] = cache_meta["cache_current"]
    return lesson


def _instance_lesson_context(inst_dir: Path) -> dict:
    meta_path = inst_dir / "meta.json"
    master_path = inst_dir / "master.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    master = json.loads(master_path.read_text(encoding="utf-8")) if master_path.exists() else {}
    north = master.get("north_star", {}) if isinstance(master.get("north_star"), dict) else {}
    ctx = {
        "meta": meta,
        "master": master,
        "domain": meta.get("domain") or north.get("domain") or "学习",
        "target": meta.get("target") or north.get("target") or "",
        "baseline": north.get("baseline_summary") or "",
    }
    ctx["session_contract"] = _lesson_session_contract(meta=meta, master=master)
    return ctx


def _first_number(*values: Any) -> float:
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            return number
    return 0.0


def _lesson_session_contract(*, meta: dict, master: dict) -> dict:
    """把 onboarding 已收集的每日学习时长转成 lesson 生成的硬约束。"""
    schedule = master.get("schedule") if isinstance(master.get("schedule"), dict) else {}
    weekday = _first_number(schedule.get("weekday_hours"), meta.get("weekday_hours"))
    weekend = _first_number(schedule.get("weekend_hours"), meta.get("weekend_hours"))
    weekly = _first_number(schedule.get("weekly_total_hours"), meta.get("weekly_total_hours"))

    if weekly:
        daily_hours = weekly / 7.0
    elif weekday or weekend:
        daily_hours = ((weekday or weekend) * 5 + (weekend or weekday) * 2) / 7.0
    else:
        return {}

    raw_minutes = daily_hours * 60.0
    target_minutes = int(round(raw_minutes / 15.0) * 15)
    target_minutes = max(25, min(120, target_minutes))
    if target_minutes >= 90:
        label = "1–2 小时"
        mode = "full_unit"
    elif target_minutes >= 60:
        label = "约 1 小时"
        mode = "full_unit"
    else:
        label = f"约 {target_minutes} 分钟"
        mode = "focused_unit"

    min_estimated = max(25, int(target_minutes * 0.75))
    if target_minutes >= 60:
        min_estimated = max(60, min_estimated)
    return {
        "label": label,
        "mode": mode,
        "target_minutes": target_minutes,
        "min_estimated_minutes": min_estimated,
        "pace": f"weekday={weekday:g}h/weekend={weekend:g}h/weekly={weekly:g}h",
        "source": "onboarding_schedule",
        "required_activity_types": [
            "lecture",
            "worked_example",
            "drill",
            "produce",
            "reflection",
        ] if target_minutes >= 60 else ["lecture", "example", "produce"],
    }


def _lesson_learner_signals(user_id: int, instance_id: str) -> str:
    """把掌握度 + 近期记忆压成给"课设"用的学员信号，让本节对齐这个人而非通用。"""
    lines: list[str] = []
    try:
        mastery = concept_mastery(user_id, instance_id)
    except Exception:  # noqa: BLE001
        mastery = {}
    if mastery:
        ranked = sorted(mastery.items(), key=lambda kv: kv[1].get("prob", 0.0))
        weak = [name for name, m in ranked if m.get("prob", 0.0) < 0.6][:5]
        strong = [name for name, m in ranked if m.get("prob", 0.0) >= 0.8][-5:]
        if weak:
            lines.append("掌握度偏弱（需要补/讲透）：" + "、".join(weak))
        if strong:
            lines.append("已较扎实（不必再啰嗦）：" + "、".join(strong))
    try:
        from core.memory import format_for_prompt, recall

        mem = format_for_prompt(recall(user_id, instance_id, limit=6))
    except Exception:  # noqa: BLE001
        mem = ""
    if mem:
        lines.append("学员画像/偏好/近期信号：\n" + mem)
    return "\n".join(lines).strip()


def _build_lesson_and_save(
    *,
    user_id: int,
    instance_id: str,
    inst_dir: Path,
    cache_path: Path,
    domain: str,
    target: str,
    baseline: str,
    topic: str,
    custom_instruction: str,
    regenerate: bool,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """在线程中完整生成并落盘；即使请求端断开，任务也能继续保存。"""
    def tick(stage_id: str) -> None:
        if progress:
            progress(stage_id)

    tick("context")
    context_parts: list[str] = []
    used_sources: list[dict] = []
    try:
        tick("kb")
        res = search(user_id, instance_id, topic, 4)
        if res.get("hits"):
            parts = []
            for i, h in enumerate(res["hits"], 1):
                parts.append(f"[来源{i}]《{h['source_title']}》\n{h['text']}")
                used_sources.append({"n": i, "title": h["source_title"], "kind": "kb"})
            context_parts.append("\n\n".join(parts))
    except Exception:  # noqa: BLE001 — 检索失败不挡出课
        pass

    source_query = " ".join(x for x in (domain, target, topic) if x)
    try:
        tick("sources")
        source_pack = build_source_pack(source_query, count=4, timeout=5.0)
    except Exception as exc:  # noqa: BLE001 — 外部来源失败不挡出课
        source_pack = {"status": "error", "query": topic, "sources": [], "note": type(exc).__name__}
    source_pack = normalize_source_pack(source_pack, fallback_query=source_query, count=4)
    web_sources = source_pack.get("sources") or []
    quality_sources = select_quality_sources(web_sources, limit=3)
    if quality_sources:
        tick("source_fetch")
        start = len(used_sources) + 1
        try:
            grounding = build_grounding_context(quality_sources, start=start)
        except Exception:  # noqa: BLE001 — 抓正文失败退回摘要拼接
            grounding = format_sources_context(quality_sources, start=start)
        if grounding:
            context_parts.append(grounding)
            used_sources.extend(
                _display_sources_from_pack({"sources": quality_sources}, start=start)
            )
        source_pack["grounding"] = "grounded"
        source_pack["grounded_sources"] = [s.get("url") for s in quality_sources if s.get("url")]
    else:
        # 没有高权威来源就诚实地不喂噪声：宁可按通用知识生成，也不把 SEO 垃圾当依据。
        source_pack["grounding"] = "ungrounded"
        if web_sources:
            source_pack["note"] = (
                (source_pack.get("note") or "")
                + " 联网来源权威性不足，本节按通用知识生成，未作为事实依据注入。"
            ).strip()
    context = "\n\n".join(x for x in context_parts if x)
    learner_signals = _lesson_learner_signals(user_id, instance_id)
    try:
        session_contract = _instance_lesson_context(inst_dir).get("session_contract") or {}
    except Exception:  # noqa: BLE001
        session_contract = {}

    with usage_context(user_id, "lesson", instance_id):
        lesson = generate_lesson_reviewed(
            domain=domain,
            target=target,
            topic=topic,
            baseline=baseline,
            context=context,
            custom_instruction=custom_instruction,
            learner_signals=learner_signals,
            session_contract=session_contract,
            progress=tick,
        )
    ensure_lesson_cards(lesson)
    if session_contract:
        lesson["_session_contract"] = session_contract
    if used_sources:
        lesson["_sources"] = used_sources
    lesson["_source_pack"] = source_pack
    if custom_instruction:
        lesson["_custom_instruction"] = custom_instruction

    now = datetime.now().isoformat(timespec="seconds")
    lesson["_cached"] = False
    lesson["_generated_at"] = now
    tick("quality_gate")
    _prepare_lesson_runtime(lesson, source_pack=source_pack, query=source_query, generated_at=now, context=context)
    quality = lesson.get("_quality") if isinstance(lesson.get("_quality"), dict) else {}
    if lesson.get("_fallback") or not quality.get("passed"):
        score = int(quality.get("score") or 0)
        raise ValueError(f"本节未通过当前质量门（{score}/100），旧缓存未被覆盖，请重试生成。")

    lesson["_cache_status"] = "current"
    lesson["_cache_version"] = LESSON_CACHE_VERSION
    lesson["_cache_current"] = True
    _write_lesson_cache_atomic(
        cache_path,
        {
            "cache_version": LESSON_CACHE_VERSION,
            "topic": topic,
            "generated_at": now,
            "custom_instruction": custom_instruction,
            "lesson": lesson,
        },
    )

    suffix = f" · 要求：{custom_instruction[:80]}" if custom_instruction else ""
    log_event(user_id, "lesson", ("重新生成 · " if regenerate else "") + topic + suffix, instance_id)
    return lesson


def _start_lesson_job(
    *,
    key: str,
    topic: str,
    user_id: int | None = None,
    instance_id: str = "",
    cache_status: str = "miss",
    previous_cache_status: str = "",
) -> dict:
    job = {
        "id": key,
        "user_id": user_id,
        "instance_id": instance_id,
        "topic": topic,
        "status": "generating",
        "cache_status": cache_status,
        "previous_cache_status": previous_cache_status,
        "started_at": datetime.now(),
    }
    _set_lesson_job_stage(job, "context")
    _LESSON_JOBS[key] = job
    _persist_lesson_job(job)
    return job


def _attach_lesson_job_task(job: dict, task) -> dict:
    job["task"] = task

    def _done(done_task) -> None:
        finished = datetime.now()
        try:
            job["lesson"] = done_task.result()
            job["status"] = "ready"
            job["cache_status"] = "current"
            job["can_retry"] = False
            job["timed_out"] = False
            job["deadline_exceeded"] = False
            job.pop("error", None)
            job.pop("deadline_detail", None)
            _set_lesson_job_stage(job, "quality_gate")
            _close_lesson_job_stage(job, now=finished)
        except Exception as exc:  # noqa: BLE001
            _close_lesson_job_stage(job, status="error", now=finished)
            job["status"] = "error"
            job["error"] = f"{type(exc).__name__}: {exc}"
            job["can_retry"] = True
        job["finished_at"] = finished
        _persist_lesson_job(job)

    task.add_done_callback(_done)
    return job


def _lesson_cache_is_new_for_job(cache_path: Path, job: dict) -> bool:
    if not cache_path.exists():
        return False
    started = job.get("started_at")
    if not isinstance(started, datetime):
        return True
    try:
        return cache_path.stat().st_mtime >= started.timestamp() - 1
    except OSError:
        return False


def _recover_lesson_job_from_cache(
    job: dict,
    *,
    cache_path: Path,
    inst_dir: Path,
    topic: str,
    domain: str,
    target: str,
) -> dict | None:
    """轮询时以落盘结果为准，避免内存 job 状态滞后导致前端一直等待。"""
    if not _lesson_cache_is_new_for_job(cache_path, job):
        return None
    lesson = _load_cached_lesson(inst_dir, topic=topic, domain=domain, target=target)
    if not lesson or not lesson.get("_cache_current"):
        return None
    lesson["_cached"] = False
    job["lesson"] = lesson
    job["status"] = "ready"
    job["can_retry"] = False
    job["timed_out"] = False
    job["deadline_exceeded"] = False
    job.pop("error", None)
    job.pop("deadline_detail", None)
    job["recovered_from_cache"] = True
    _set_lesson_job_stage(job, "quality_gate")
    finished = datetime.now()
    _close_lesson_job_stage(job, now=finished)
    job["finished_at"] = finished
    _persist_lesson_job(job)
    return lesson


async def _await_lesson_job(job: dict):
    try:
        return await asyncio.wait_for(asyncio.shield(job["task"]), timeout=_lesson_timeout_seconds())
    except asyncio.TimeoutError:
        return JSONResponse(
            status_code=202,
            content=_lesson_job_payload(
                job,
                message="本节内容仍在后台生成。你可以停留在本页，LearnBuddy 会自动检查结果；生成好后会直接打开。",
            ),
        )


@app.post("/api/instance/{instance_id}/lesson")
async def api_lesson(instance_id: str, request: Request) -> dict:
    """就指定主题，结合实例的领域/目标/基线，实时生成一小节"学+练"微课。

    输入 (JSON): { "topic": "本节主题（一般传周主题/知识点）" }
    输出: 讲解卡 + N 道点选练习（见 core.content.generate_lesson）
    """
    user = require_user(request)
    _enforce_rate_limit(
        request,
        scope="lesson-generate",
        actor=f"user:{user.id}",
        default_limit=12,
    )
    inst_dir = INSTANCES_DIR / instance_id
    if not inst_dir.is_dir():
        raise HTTPException(404, f"实例 {instance_id} 不存在")
    _assert_can_access(instance_id, user)

    body = await request.json()
    topic = (body.get("topic") or "").strip()
    custom_instruction = (body.get("custom_instruction") or body.get("instruction") or "").strip()[:1000]
    regenerate = bool(body.get("regenerate")) or bool(custom_instruction)

    meta_path = inst_dir / "meta.json"
    master_path = inst_dir / "master.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    master = json.loads(master_path.read_text(encoding="utf-8")) if master_path.exists() else {}
    north = master.get("north_star", {})

    domain = meta.get("domain") or north.get("domain") or "学习"
    target = meta.get("target") or north.get("target") or ""
    baseline = north.get("baseline_summary") or ""
    if not topic:
        topic = f"{domain} 入门第一课"

    # 只有当前版本且质量通过的缓存可以直接返回；旧缓存保留到升级成功后再原子替换。
    cache_path = _lesson_cache_path(inst_dir, topic)
    cached_lesson = None
    if cache_path.exists() and not regenerate:
        cached_lesson = _load_cached_lesson(inst_dir, topic=topic, domain=domain, target=target)
        if cached_lesson and cached_lesson.get("_cache_current"):
            return cached_lesson

    job_key = _lesson_job_key(instance_id, topic, custom_instruction)
    if regenerate:
        _LESSON_JOBS.pop(job_key, None)
    existing_job = None if regenerate else (
        _LESSON_JOBS.get(job_key) or _load_lesson_job(user.id, job_key)
    )
    if existing_job:
        recovered = _recover_lesson_job_from_cache(
            existing_job,
            cache_path=cache_path,
            inst_dir=inst_dir,
            topic=topic,
            domain=domain,
            target=target,
        )
        if recovered:
            return recovered
        if existing_job.get("status") == "generating":
            return await _await_lesson_job(existing_job)
        if existing_job.get("status") == "ready" and isinstance(existing_job.get("lesson"), dict):
            return existing_job["lesson"]
        if existing_job.get("status") == "error":
            # 前端“重试生成”再次调用同一主题时，必须能启动新任务，而不是永久复读旧错误。
            _LESSON_JOBS.pop(job_key, None)

    # 需要真正生成 → 此时才查额度
    q = check_quota(user.id)
    if not q["allowed"]:
        raise HTTPException(429, _quota_message(q))

    job = _start_lesson_job(
        key=job_key,
        topic=topic,
        user_id=user.id,
        instance_id=instance_id,
        cache_status="upgrading" if cached_lesson else "miss",
        previous_cache_status=str((cached_lesson or {}).get("_cache_status") or ""),
    )
    task = asyncio.create_task(
        asyncio.to_thread(
            _build_lesson_and_save,
            user_id=user.id,
            instance_id=instance_id,
            inst_dir=inst_dir,
            cache_path=cache_path,
            domain=domain,
            target=target,
            baseline=baseline,
            topic=topic,
            custom_instruction=custom_instruction,
            regenerate=regenerate,
            progress=lambda stage_id: _set_lesson_job_stage(job, stage_id),
        )
    )
    _attach_lesson_job_task(job, task)
    try:
        return await _await_lesson_job(job)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"内容生成失败：{type(e).__name__}: {e}")


@app.get("/api/instance/{instance_id}/lesson/status")
async def api_lesson_status(instance_id: str, request: Request) -> dict:
    """查询学练单元后台生成状态；用于请求断开后的自动恢复。"""
    user = require_user(request)
    inst_dir = INSTANCES_DIR / instance_id
    if not inst_dir.is_dir():
        raise HTTPException(404, f"实例 {instance_id} 不存在")
    _assert_can_access(instance_id, user)

    topic = (request.query_params.get("topic") or "").strip()
    custom_instruction = (request.query_params.get("custom_instruction") or "").strip()[:1000]
    job_id = (request.query_params.get("job_id") or "").strip()
    if not topic:
        raise HTTPException(400, "缺少 topic")

    meta_path = inst_dir / "meta.json"
    master_path = inst_dir / "master.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    master = json.loads(master_path.read_text(encoding="utf-8")) if master_path.exists() else {}
    north = master.get("north_star", {})
    domain = meta.get("domain") or north.get("domain") or "学习"
    target = meta.get("target") or north.get("target") or ""
    cache_path = _lesson_cache_path(inst_dir, topic)

    resolved_job_id = job_id or _lesson_job_key(instance_id, topic, custom_instruction)
    job = _LESSON_JOBS.get(resolved_job_id) or _load_lesson_job(user.id, resolved_job_id)
    if job:
        recovered = _recover_lesson_job_from_cache(
            job,
            cache_path=cache_path,
            inst_dir=inst_dir,
            topic=topic,
            domain=domain,
            target=target,
        )
        if recovered:
            return {"status": "ready", "lesson": recovered, **_lesson_job_payload(job)}
        status = job.get("status")
        if status == "ready" and isinstance(job.get("lesson"), dict):
            lesson = job["lesson"]
            lesson["_cached"] = False
            return {"status": "ready", "lesson": lesson, **_lesson_job_payload(job)}
        if status == "error":
            return {
                "status": "error",
                "error": job.get("error") or "生成失败",
                **_lesson_job_payload(job, message="后台生成失败，请重试。"),
            }
        return _lesson_job_payload(job)

    lesson = _load_cached_lesson(inst_dir, topic=topic, domain=domain, target=target)
    if lesson and lesson.get("_cache_current"):
        return {"status": "ready", "lesson": lesson, "topic": topic}
    if lesson:
        return {
            "status": "error",
            "topic": topic,
            "cache_status": lesson.get("_cache_status"),
            "cache_version": lesson.get("_cache_version"),
            "can_retry": True,
            "detail": "已保存内容不符合当前版本或质量合同，需要重新生成后才能继续使用。",
        }
    return {"status": "idle", "topic": topic, "detail": "暂未找到后台任务或已保存内容。"}


@app.get("/api/instance/{instance_id}/lesson/runtime")
async def api_lesson_runtime(instance_id: str, request: Request) -> dict:
    """读取某一学练单元的用户运行态（已看步骤、选项、输入和产出）。"""
    user = require_user(request)
    inst_dir = INSTANCES_DIR / instance_id
    if not inst_dir.is_dir():
        raise HTTPException(404, f"实例 {instance_id} 不存在")
    _assert_can_access(instance_id, user)
    topic = (request.query_params.get("topic") or "").strip()
    if not topic:
        raise HTTPException(400, "缺少 topic")
    return load_lesson_runtime(inst_dir, topic)


@app.post("/api/instance/{instance_id}/lesson/runtime")
async def api_save_lesson_runtime(instance_id: str, request: Request) -> dict:
    """增量保存学练单元运行态；与 lesson 内容缓存分离，避免覆盖课程内容。"""
    user = require_user(request)
    inst_dir = INSTANCES_DIR / instance_id
    if not inst_dir.is_dir():
        raise HTTPException(404, f"实例 {instance_id} 不存在")
    _assert_can_access(instance_id, user)
    body = await request.json()
    topic = (body.get("topic") or "").strip()
    if not topic:
        raise HTTPException(400, "缺少 topic")
    patch = body.get("runtime") if isinstance(body.get("runtime"), dict) else body
    return save_lesson_runtime(inst_dir, topic, patch)


# ============================================================================
# API · 学情 / 资料（学习事件沉淀 → 派生学情、错题本、要点卡）
# ============================================================================


@app.post("/api/instance/{instance_id}/event")
async def api_record_event(instance_id: str, request: Request) -> dict:
    """记录一次学练完成（逐题对错 + 自评）。

    输入 (JSON): {
      topic, week,
      answers: [{question, options, answer_index, chosen_index, correct, why}],
      self_rating: "cant" | "with_help" | "independent"
    }
    """
    user = require_user(request)
    inst_dir = INSTANCES_DIR / instance_id
    if not inst_dir.is_dir():
        raise HTTPException(404, f"实例 {instance_id} 不存在")
    _assert_can_access(instance_id, user)

    body = await request.json()
    answers = body.get("answers") if isinstance(body.get("answers"), list) else []
    try:
        week = int(body.get("week")) if body.get("week") is not None else None
    except (TypeError, ValueError):
        week = None

    result = record_lesson_result(
        user_id=user.id,
        instance_id=instance_id,
        topic=(body.get("topic") or "").strip(),
        week=week,
        answers=answers,
        self_rating=body.get("self_rating"),
    )
    reward = reward_study_completion(
        user.id,
        instance_id=instance_id,
        topic=(body.get("topic") or "").strip(),
    )
    log_event(user.id, "study", (body.get("topic") or "").strip(), instance_id)
    try:
        from core.memory import refresh_derived
        refresh_derived(user.id, instance_id)  # 学练完成 → 刷新派生记忆
    except Exception:  # noqa: BLE001
        pass
    path_state = None
    try:
        path_state = record_lesson_completed(
            inst_dir,
            user.id,
            topic=(body.get("topic") or "").strip(),
            week=week,
            summary={**result, "reward": reward},
            completed_topics=studied_topics(user.id, instance_id),
        )
        path_state = _maybe_start_next_pregeneration(
            user=user,
            instance_id=instance_id,
            inst_dir=inst_dir,
            state=path_state,
        )
    except Exception as exc:  # noqa: BLE001
        path_state = {
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {"ok": True, **result, "reward": reward, "path_loop": path_state}


@app.get("/api/instance/{instance_id}/analytics")
async def api_analytics(instance_id: str, request: Request) -> dict:
    """学情数据包：双坐标、弱项、复习队列、趋势。"""
    user = require_user(request)
    inst_dir = INSTANCES_DIR / instance_id
    if not inst_dir.is_dir():
        raise HTTPException(404, f"实例 {instance_id} 不存在")
    _assert_can_access(instance_id, user)
    return analytics_summary(user.id, instance_id)


@app.get("/api/instance/{instance_id}/recommend")
async def api_recommend(instance_id: str, request: Request) -> dict:
    """今日推荐（Batch L · 环 A 决策）：复习/新课/弱项的有优先级编排（确定性，不调 LLM）。"""
    user = require_user(request)
    inst_dir = INSTANCES_DIR / instance_id
    if not inst_dir.is_dir():
        raise HTTPException(404, f"实例 {instance_id} 不存在")
    _assert_can_access(instance_id, user)

    master = {}
    mp = inst_dir / "master.json"
    if mp.exists():
        try:
            master = json.loads(mp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            master = {}
    from core.recommend import daily_plan
    return await asyncio.to_thread(
        daily_plan, user_id=user.id, instance_id=instance_id, inst_dir=inst_dir, master=master
    )


# ============================================================================
# 移动端推送触点（轨道 A · PWA + Web Push；公共地基见 core/notify + push_*）
# ============================================================================


@app.get("/api/push/vapid-public")
async def api_push_vapid_public() -> dict:
    """前端 applicationServerKey 用。未配 VAPID → {enabled:false}，前端据此隐藏订阅按钮（优雅降级）。"""
    from core.push_web import is_configured

    return {"enabled": is_configured(), "public_key": os.getenv("ITUTOR_VAPID_PUBLIC_KEY", "")}


@app.post("/api/push/subscribe")
async def api_push_subscribe(request: Request) -> dict:
    """{endpoint, keys:{p256dh,auth}, expirationTime?, instance_id?} → 存订阅（endpoint 幂等 upsert）。"""
    user = require_user(request)
    body = await request.json()
    keys = body.get("keys") or {}
    from core.push_subs import SubscriptionOwnershipError, add_subscription

    instance_id = body.get("instance_id")
    if instance_id is not None and not isinstance(instance_id, str):
        raise HTTPException(400, "instance_id 必须是字符串")
    instance_id = (instance_id or "").strip() or None
    if instance_id:
        inst_dir = INSTANCES_DIR / instance_id
        if not inst_dir.is_dir():
            raise HTTPException(404, f"实例 {instance_id} 不存在")
        _assert_can_access(instance_id, user)

    try:
        sub_id = add_subscription(
            user_id=user.id,
            endpoint=body["endpoint"],
            p256dh=keys.get("p256dh", ""),
            auth=keys.get("auth", ""),
            instance_id=instance_id,
            expiration_time=body.get("expirationTime"),
        )
    except SubscriptionOwnershipError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True, "sub_id": sub_id}


@app.delete("/api/push/subscribe")
async def api_push_unsubscribe(request: Request) -> dict:
    """{endpoint} → 删该设备订阅。"""
    user = require_user(request)
    body = await request.json()
    from core.push_subs import delete_by_endpoint

    delete_by_endpoint(body["endpoint"], user_id=user.id)
    return {"ok": True}


@app.post("/api/push/test")
async def api_push_test(request: Request) -> dict:
    """dogfood：给自己立即发一条真实 plan 推送（不经 scheduler）。未配 VAPID → 400 提示。"""
    user = require_user(request)
    from core.notify import build_payload
    from core.push_subs import last_active_instance
    from core.push_web import is_configured, send_to_user
    from core.recommend import daily_plan

    if not is_configured():
        raise HTTPException(400, "未配置 VAPID（见 .env.example 的 ITUTOR_VAPID_*）")
    body = await request.json()
    instance_id = (body or {}).get("instance_id") or last_active_instance(user.id)
    if not instance_id:
        raise HTTPException(400, "没有可用的学习路径")
    inst_dir = INSTANCES_DIR / instance_id
    if not inst_dir.is_dir():
        raise HTTPException(404, f"实例 {instance_id} 不存在")
    _assert_can_access(instance_id, user)
    master = {}
    mp = inst_dir / "master.json"
    if mp.exists():
        try:
            master = json.loads(mp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            master = {}
    plan = await asyncio.to_thread(
        daily_plan, user_id=user.id, instance_id=instance_id, inst_dir=inst_dir, master=master
    )
    payload = build_payload(plan, instance_id=instance_id, base_url=os.getenv("ITUTOR_BASE_URL", ""))
    result = await send_to_user(user.id, payload)
    return {"ok": True, "payload": payload, "result": result}


@app.get("/api/instance/{instance_id}/replan")
async def api_replan(instance_id: str, request: Request) -> dict:
    """动态重规划（Batch L · 环 A）：检测卡壳/超额/中断三触发器，给路径调整建议（确定性）。"""
    user = require_user(request)
    inst_dir = INSTANCES_DIR / instance_id
    if not inst_dir.is_dir():
        raise HTTPException(404, f"实例 {instance_id} 不存在")
    _assert_can_access(instance_id, user)

    master = {}
    mp = inst_dir / "master.json"
    if mp.exists():
        try:
            master = json.loads(mp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            master = {}
    from core.replan import current_proposal, detect
    detection = await asyncio.to_thread(
        detect, user_id=user.id, instance_id=instance_id, inst_dir=inst_dir, master=master
    )
    proposal = await asyncio.to_thread(current_proposal, user.id, instance_id)
    return {**detection, "proposal": proposal}


@app.post("/api/instance/{instance_id}/replan/proposals")
async def api_create_replan_proposal(instance_id: str, request: Request) -> dict:
    """根据当前检测创建待确认提案，本步不修改 master。"""
    user = require_user(request)
    _enforce_rate_limit(
        request,
        scope="replan-create",
        actor=f"user:{user.id}",
        default_limit=8,
    )
    inst_dir = INSTANCES_DIR / instance_id
    if not inst_dir.is_dir():
        raise HTTPException(404, f"实例 {instance_id} 不存在")
    _assert_can_access(instance_id, user)
    try:
        master = json.loads((inst_dir / "master.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(409, "master.json 不可用") from exc
    from core.replan import create_proposal

    return await asyncio.to_thread(
        create_proposal,
        user_id=user.id,
        instance_id=instance_id,
        inst_dir=inst_dir,
        master=master,
    )


@app.post("/api/instance/{instance_id}/replan/proposals/{proposal_id}/decision")
async def api_decide_replan_proposal(instance_id: str, proposal_id: str, request: Request) -> dict:
    """只有明确 confirm 才修改 master；reject/undo 都保留生命周期记录。"""
    user = require_user(request)
    inst_dir = INSTANCES_DIR / instance_id
    if not inst_dir.is_dir():
        raise HTTPException(404, f"实例 {instance_id} 不存在")
    _assert_can_access(instance_id, user)
    body = await request.json()
    from core.replan import decide_proposal

    try:
        return await asyncio.to_thread(
            decide_proposal,
            user_id=user.id,
            instance_id=instance_id,
            proposal_id=proposal_id,
            action=body.get("action"),
            inst_dir=inst_dir,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/instance/{instance_id}/memory")
async def api_get_memory(instance_id: str, request: Request) -> dict:
    """取关于学习者的长期记忆（全局 + 该空间）——"关于你"。"""
    user = require_user(request)
    inst_dir = INSTANCES_DIR / instance_id
    if not inst_dir.is_dir():
        raise HTTPException(404, f"实例 {instance_id} 不存在")
    _assert_can_access(instance_id, user)
    from core.memory import recall
    mems = recall(user.id, instance_id, limit=12)
    return {"count": len(mems), "memories": mems}


@app.post("/api/instance/{instance_id}/memory")
async def api_add_memory(instance_id: str, request: Request) -> dict:
    """用户手写一条记忆（偏好/目标）：{content, kind?, global?}。global=true 存为跨空间记忆。"""
    user = require_user(request)
    inst_dir = INSTANCES_DIR / instance_id
    if not inst_dir.is_dir():
        raise HTTPException(404, f"实例 {instance_id} 不存在")
    _assert_can_access(instance_id, user)

    body = await request.json()
    from core.memory import write_memory
    try:
        res = write_memory(
            user_id=user.id,
            content=(body.get("content") or "").strip(),
            kind=(body.get("kind") or "preference").strip() or "preference",
            instance_id=None if body.get("global") else instance_id,
            source="user",
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, **res}


@app.delete("/api/instance/{instance_id}/memory/{memory_id}")
async def api_del_memory(instance_id: str, memory_id: int, request: Request) -> dict:
    """删除一条记忆（仅本人）。"""
    user = require_user(request)
    from core.memory import delete_memory
    ok = delete_memory(user.id, memory_id)
    return {"ok": ok}


@app.get("/api/instance/{instance_id}/materials")
async def api_materials(instance_id: str, request: Request) -> dict:
    """资料：错题本（来自作答流水）+ 要点卡（来自已学主题的讲解缓存）。"""
    user = require_user(request)
    inst_dir = INSTANCES_DIR / instance_id
    if not inst_dir.is_dir():
        raise HTTPException(404, f"实例 {instance_id} 不存在")
    _assert_can_access(instance_id, user)

    wrong = wrong_questions(user.id, instance_id)

    # 要点卡：读已学主题的讲解缓存，抽取标题/要点/易错点
    cards = []
    for topic in studied_topics(user.id, instance_id):
        cache_path = _lesson_cache_path(inst_dir, topic)
        if not cache_path.exists():
            continue
        try:
            saved = json.loads(cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        explain = (saved.get("lesson") or {}).get("explain") or {}
        points = [p for p in (explain.get("points") or []) if str(p).strip()]
        if not points and not explain.get("summary"):
            continue
        cards.append(
            {
                "topic": topic,
                "title": explain.get("title") or topic,
                "summary": explain.get("summary") or "",
                "points": points,
                "tip": explain.get("tip") or "",
            }
        )

    return {
        "wrong_questions": wrong,
        "key_cards": cards,
        "artifacts": list_artifacts(user.id, instance_id),
    }


@app.post("/api/instance/{instance_id}/artifact")
async def api_artifact(instance_id: str, request: Request) -> dict:
    """归档一个学习产物（4-2-1 的"1 产出物"：主动复述/笔记/代码）。"""
    user = require_user(request)
    inst_dir = INSTANCES_DIR / instance_id
    if not inst_dir.is_dir():
        raise HTTPException(404, f"实例 {instance_id} 不存在")
    _assert_can_access(instance_id, user)

    body = await request.json()
    res = record_artifact(
        user_id=user.id,
        instance_id=instance_id,
        topic=(body.get("topic") or "").strip(),
        content=(body.get("content") or "").strip(),
        kind=(body.get("kind") or "recall").strip() or "recall",
        prompt=(body.get("prompt") or "").strip(),
    )
    if not res.get("ok"):
        raise HTTPException(400, "产出内容为空")
    log_event(user.id, "artifact", (body.get("topic") or "").strip(), instance_id)
    return res


@app.post("/api/instance/{instance_id}/feedback")
async def api_feedback(instance_id: str, request: Request) -> dict:
    """记录用户对 AI 产出的反馈（Batch L · 环 B 的眼睛）。

    body: { rating: "up"|"down"|"error", scene?: "lesson"|"coach"|..., topic?, reason? }
    """
    user = require_user(request)
    inst_dir = INSTANCES_DIR / instance_id
    if not inst_dir.is_dir():
        raise HTTPException(404, f"实例 {instance_id} 不存在")
    _assert_can_access(instance_id, user)

    body = await request.json()
    try:
        res = record_feedback(
            user_id=user.id,
            instance_id=instance_id,
            scene=(body.get("scene") or "lesson").strip(),
            rating=(body.get("rating") or "").strip(),
            topic=(body.get("topic") or "").strip(),
            reason=(body.get("reason") or "").strip(),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    log_event(user.id, "feedback", f"{body.get('rating')} · {(body.get('topic') or '')[:60]}", instance_id)
    return {"ok": True, **res}


@app.post("/api/instance/{instance_id}/coach")
async def api_coach(instance_id: str, request: Request) -> dict:
    """LearnBuddy 复盘（元认知）：基于学情现场生成，可追问。

    输入 (JSON): { history: [{role, content}, ...] }  空 history = 首次本周复盘
    """
    user = require_user(request)
    inst_dir = INSTANCES_DIR / instance_id
    if not inst_dir.is_dir():
        raise HTTPException(404, f"实例 {instance_id} 不存在")
    _assert_can_access(instance_id, user)

    q = check_quota(user.id)
    if not q["allowed"]:
        raise HTTPException(429, _quota_message(q))

    body = await request.json()
    history = body.get("history") if isinstance(body.get("history"), list) else []

    master = {}
    mp = inst_dir / "master.json"
    if mp.exists():
        try:
            master = json.loads(mp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            master = {}
    ns = master.get("north_star") or {}
    analytics = analytics_summary(user.id, instance_id)
    from core.memory import format_for_prompt, recall
    memory = format_for_prompt(recall(user.id, instance_id))

    try:
        last_user = ""
        for h in reversed(history):
            if isinstance(h, dict) and h.get("role") == "user":
                last_user = str(h.get("content") or "")
                break
        if last_user:
            record_message(
                user_id=user.id,
                role="user",
                content=last_user,
                session_id=f"coach:{instance_id}",
                scene="coach",
                instance_id=instance_id,
            )
        with usage_context(user.id, "coach", instance_id):
            reply = await asyncio.to_thread(
                coach_reply,
                domain=ns.get("domain", ""),
                target=ns.get("target", ""),
                baseline=ns.get("baseline_summary", ""),
                analytics=analytics,
                history=history,
                memory=memory,
            )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"教练复盘失败：{type(e).__name__}: {e}")

    log_event(user.id, "coach", "本周复盘" if not history else "追问教练", instance_id)
    record_message(
        user_id=user.id,
        role="assistant",
        content=reply,
        session_id=f"coach:{instance_id}",
        scene="coach",
        instance_id=instance_id,
    )
    return {"reply": reply}


@app.get("/api/instance/{instance_id}/graph")
async def api_graph(instance_id: str, request: Request, regenerate: bool = False) -> dict:
    """知识疆域地图：Concept 图谱 + 真实掌握度 + 已征服/前线/锁定状态。

    首次会调教研 Agent 现场生成图谱并落盘；之后直接复用 concepts.json。
    """
    user = require_user(request)
    inst_dir = INSTANCES_DIR / instance_id
    if not inst_dir.is_dir():
        raise HTTPException(404, f"实例 {instance_id} 不存在")
    _assert_can_access(instance_id, user)

    master = {}
    mp = inst_dir / "master.json"
    if mp.exists():
        try:
            master = json.loads(mp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            master = {}

    needs_gen = regenerate or not (inst_dir / "concepts.json").exists()
    if needs_gen:
        q = check_quota(user.id)
        if not q["allowed"]:
            raise HTTPException(429, _quota_message(q))

    try:
        if needs_gen:
            with usage_context(user.id, "graph", instance_id):
                graph = await asyncio.to_thread(load_or_generate, inst_dir, master, regenerate=regenerate)
        else:
            graph = await asyncio.to_thread(load_or_generate, inst_dir, master)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"知识图谱生成失败：{type(e).__name__}: {e}")

    concepts = graph.get("concepts") or []
    mastery = concept_mastery(user.id, instance_id)
    mastered = mastered_set(mastery, MASTERY_THRESHOLD)
    status = concept_status(concepts, mastered)
    unlocks = build_unlocks(concepts)
    by_id = {c["id"]: c for c in concepts}

    items = []
    counts = {"mastered": 0, "frontier": 0, "locked": 0, "total": len(concepts)}
    for c in concepts:
        st = status.get(c["id"], "locked")
        counts[st] = counts.get(st, 0) + 1
        m = mastery.get(c["name"]) or {}
        items.append({
            "id": c["id"],
            "name": c["name"],
            "week": c["week"],
            "difficulty": c["difficulty"],
            "prerequisites": c["prerequisites"],
            "sources": c.get("sources") or [],
            "prereq_names": [by_id[p]["name"] for p in c["prerequisites"] if p in by_id],
            "unlocks": unlocks.get(c["id"], []),
            "status": st,
            "mastery": m.get("prob", 0.0),
            "objective_pct": m.get("objective_pct"),
            "self_label": m.get("self_label", "—"),
            "due": m.get("due", False),
        })

    if needs_gen:
        log_event(user.id, "graph", ("重建图谱" if regenerate else "生成图谱"), instance_id)

    from core.phases import compute_phases
    phases = compute_phases(master, items)

    return {
        "source": graph.get("_source", "cache"),
        "threshold": MASTERY_THRESHOLD,
        "concepts": items,
        "counts": counts,
        "phases": phases,
    }


@app.patch("/api/instance/{instance_id}/concept/{concept_id}/sources")
async def api_update_concept_sources(instance_id: str, concept_id: str, request: Request) -> dict:
    """维护某个知识点的权威来源。来源落在 concepts.json，兼容旧图谱。"""
    user = require_user(request)
    inst_dir = INSTANCES_DIR / instance_id
    if not inst_dir.is_dir():
        raise HTTPException(404, f"实例 {instance_id} 不存在")
    _assert_can_access(instance_id, user)
    path = inst_dir / "concepts.json"
    if not path.exists():
        raise HTTPException(404, "请先生成知识图谱")
    try:
        graph = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(400, "知识图谱文件损坏")

    body = await request.json()
    concepts = sanitize_concepts(graph.get("concepts") or [])
    found = False
    for c in concepts:
        if c["id"] == concept_id:
            c["sources"] = sanitize_concepts([{**c, "sources": body.get("sources") or []}])[0].get("sources") or []
            found = True
            break
    if not found:
        raise HTTPException(404, "知识点不存在")
    graph["concepts"] = concepts
    graph["updated_at"] = datetime.now().isoformat(timespec="seconds")
    path.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
    log_event(user.id, "edit_concept_sources", concept_id, instance_id)
    return {"ok": True, "concept_id": concept_id, "sources": next(c["sources"] for c in concepts if c["id"] == concept_id)}


# ============================================================================
# API · 知识库（RAG 检索层 · 防幻觉锚点）
# ============================================================================


@app.get("/api/instance/{instance_id}/kb")
async def api_kb_list(instance_id: str, request: Request) -> dict:
    """列出该学习空间的知识库来源。"""
    user = require_user(request)
    inst_dir = INSTANCES_DIR / instance_id
    if not inst_dir.is_dir():
        raise HTTPException(404, f"实例 {instance_id} 不存在")
    _assert_can_access(instance_id, user)
    from core.embeddings import is_configured
    return {
        "sources": list_sources(user.id, instance_id),
        "dense_enabled": is_configured(),
    }


@app.post("/api/instance/{instance_id}/kb")
async def api_kb_add(instance_id: str, request: Request) -> dict:
    """添加一个文本来源到知识库（切块 + 可选向量化 + 入库）。

    输入 (JSON): { title, text }
    """
    user = require_user(request)
    inst_dir = INSTANCES_DIR / instance_id
    if not inst_dir.is_dir():
        raise HTTPException(404, f"实例 {instance_id} 不存在")
    _assert_can_access(instance_id, user)

    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "内容不能为空")

    res = await asyncio.to_thread(
        ingest_text,
        user_id=user.id,
        instance_id=instance_id,
        title=(body.get("title") or "").strip() or "未命名来源",
        text=text,
    )
    if not res.get("ok"):
        raise HTTPException(400, res.get("error") or "入库失败")
    log_event(user.id, "kb_add", (body.get("title") or "").strip(), instance_id)
    return res


@app.delete("/api/instance/{instance_id}/kb/{source_id}")
async def api_kb_delete(instance_id: str, source_id: int, request: Request) -> dict:
    """删除一个知识库来源及其切块。"""
    user = require_user(request)
    inst_dir = INSTANCES_DIR / instance_id
    if not inst_dir.is_dir():
        raise HTTPException(404, f"实例 {instance_id} 不存在")
    _assert_can_access(instance_id, user)
    ok = await asyncio.to_thread(delete_source, user.id, instance_id, source_id)
    return {"ok": ok}


@app.post("/api/instance/{instance_id}/kb/search")
async def api_kb_search(instance_id: str, request: Request) -> dict:
    """检索知识库（调试 / 透明展示用）。输入 (JSON): { query, k? }"""
    user = require_user(request)
    inst_dir = INSTANCES_DIR / instance_id
    if not inst_dir.is_dir():
        raise HTTPException(404, f"实例 {instance_id} 不存在")
    _assert_can_access(instance_id, user)
    body = await request.json()
    query = (body.get("query") or "").strip()
    try:
        k = int(body.get("k") or 4)
    except (TypeError, ValueError):
        k = 4
    res = await asyncio.to_thread(search, user.id, instance_id, query, max(1, min(k, 10)))
    return res


# ============================================================================
# API · 6 关自检（WeeklySelfCheck · 交互页核心数据）
# ============================================================================


@app.post("/api/instance/{instance_id}/self-check")
async def api_self_check_submit(instance_id: str, request: Request) -> dict:
    """提交一次 6 关自检。

    body: { week: int, scores: [int*6], notes?: [str*6], ai_feedback?: str, r1_action?: str }
    同一 (user, instance, week) 二次提交覆盖（用户刷新页面后再提交）。
    """
    from core.self_check import submit_self_check

    user = require_user(request)
    inst_dir = INSTANCES_DIR / instance_id
    if not inst_dir.is_dir():
        raise HTTPException(404, f"实例 {instance_id} 不存在")
    _assert_can_access(instance_id, user)
    body = await request.json()
    try:
        week = int(body.get("week") or 0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "week 必须是正整数") from exc
    completed_topics = studied_topics(user.id, instance_id)
    state_before = load_path_loop_state(inst_dir, user.id, completed_topics, persist=False)
    completion = week_completion(state_before, week)
    if not completion["total"]:
        raise HTTPException(409, f"W{week} 还没有生成详细计划。")
    if not completion["completed"]:
        raise HTTPException(
            409,
            f"W{week} 还有 {completion['total'] - completion['done']} 天未完成，暂不能提交周自检。",
        )
    try:
        res = await asyncio.to_thread(
            submit_self_check,
            user_id=user.id,
            instance_id=instance_id,
            week=week,
            scores=body.get("scores") or [],
            notes=body.get("notes") or None,
            ai_feedback=str(body.get("ai_feedback") or ""),
            r1_action=str(body.get("r1_action") or ""),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    try:
        path_state = await asyncio.to_thread(
            advance_after_self_check,
            inst_dir,
            user.id,
            week=week,
            passed=bool(res.get("passed")),
            passed_count=int(res.get("passed_cnt") or 0),
            completed_topics=completed_topics,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    log_event(user.id, "self_check", f"W{res.get('week','')} · 通过 {res.get('passed_cnt',0)}/6", instance_id)
    return {**res, "path_loop": path_state}


@app.get("/api/instance/{instance_id}/self-check/latest")
async def api_self_check_latest(instance_id: str, request: Request, week: int | None = None) -> dict:
    """最近一次 6 关自检（前端预填用）。"""
    from core.self_check import latest_self_check, self_check_for_week

    user = require_user(request)
    inst_dir = INSTANCES_DIR / instance_id
    if not inst_dir.is_dir():
        raise HTTPException(404, f"实例 {instance_id} 不存在")
    _assert_can_access(instance_id, user)
    if week is not None:
        latest = await asyncio.to_thread(self_check_for_week, user.id, instance_id, week, inst_dir)
    else:
        latest = await asyncio.to_thread(latest_self_check, user.id, instance_id, inst_dir)
    return {"latest": latest}


@app.get("/api/instance/{instance_id}/self-check/history")
async def api_self_check_history(instance_id: str, request: Request, limit: int = 12) -> dict:
    """历史 6 关自检（按周倒序）。"""
    from core.self_check import self_check_history

    user = require_user(request)
    inst_dir = INSTANCES_DIR / instance_id
    if not inst_dir.is_dir():
        raise HTTPException(404, f"实例 {instance_id} 不存在")
    _assert_can_access(instance_id, user)
    rows = await asyncio.to_thread(self_check_history, user.id, instance_id, limit, inst_dir)
    return {"count": len(rows), "items": rows}


# ============================================================================
# API · 对话（SSE 流式核心）
# ============================================================================


@app.get("/api/generate/status")
async def api_generate_status(request: Request, job_id: str = "") -> dict:
    """查询学习路径后台生成状态；用于 SSE 断开或刷新后的自动恢复。"""
    user = require_user(request)
    safe_job_id = (job_id or "").strip()
    if not safe_job_id:
        raise HTTPException(400, "缺少 job_id")
    job = _GENERATE_JOBS.get(safe_job_id) or _load_generate_job(user.id, safe_job_id)
    if not job or int(job.get("user_id") or 0) != user.id:
        raise HTTPException(404, "生成任务不存在或已过期")
    message = ""
    if job.get("status") == "generating":
        message = "学习路径仍在后台生成。你可以停留在本页，LearnBuddy 会在完成后自动进入学习空间。"
    return _generate_job_payload(job, message=message)


@app.post("/api/chat")
async def api_chat(request: Request):
    """SSE 流式对话核心。

    输入 (JSON body):
        - session_id (str): 浏览器侧生成的 UUID，用于会话隔离
        - message (str): 用户本轮输入

    SSE 事件流：
        - event: token / data: {"token": "片段"}    每收到 1 个流式 token
        - event: generate / data: {"instance_id"}    LLM 输出 GENERATE 触发实例生成
        - event: error / data: {"error": "..."}      LLM 调用失败
        - event: end / data: {}                       本轮结束
    """
    user = require_user(request)
    _enforce_rate_limit(
        request,
        scope="chat",
        actor=f"user:{user.id}",
        default_limit=30,
    )

    q = check_quota(user.id)
    if not q["allowed"]:
        return _sse_stream(_one_shot_event("error", {"error": _quota_message(q)}))

    body = await request.json()
    session_id = (body.get("session_id") or "").strip()
    user_message = (body.get("message") or "").strip()

    if not session_id:
        raise HTTPException(400, "session_id 必填")
    if not user_message:
        raise HTTPException(400, "message 不能为空")
    if user_message in (":reset", ":r"):
        _reset_session(user.id, session_id, clear_persisted=True)
        return _sse_stream(_one_shot_event("reset", {"ok": True}))

    try:
        engine = _get_or_create_session(user.id, session_id)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    except LLMConfigError as e:
        return _sse_stream(_one_shot_event("error", {"error": str(e)}))

    async def stream() -> AsyncIterator[str]:
        import sys, time
        log = lambda m: (sys.stderr.write(f"[chat:{session_id[:8]}] {m}\n"), sys.stderr.flush())
        log(f"REQ msg={user_message!r}")
        t0 = time.time()
        token_count = 0
        assistant_text = []
        try:
            log_event(user.id, "chat", user_message[:120])
            intent_decision = should_clarify_intent(
                user_message,
                turn_count=engine.turn_count,
                history=engine.history,
            )
            if intent_decision is not None:
                intent_decision = await _enrich_intent_decision(intent_decision)
                assistant = intent_decision.assistant_message()
                engine.record_exchange(user_message, assistant)
                record_message(user_id=user.id, role="user", content=user_message, session_id=session_id, scene="chat")
                record_message(
                    user_id=user.id,
                    role="assistant",
                    content=assistant,
                    session_id=session_id,
                    scene="chat_anchor",
                )
                log(f"ANCHOR_CLARIFY concept={intent_decision.concept_type}")
                yield _sse_event("token", {"token": assistant})
                return

            goal_output_decision = initial_goal_output_choices(
                user_message,
                turn_count=engine.turn_count,
                history=engine.history,
            )
            if goal_output_decision is not None:
                assistant = goal_output_decision.assistant_message()
                engine.record_exchange(user_message, assistant)
                record_message(user_id=user.id, role="user", content=user_message, session_id=session_id, scene="chat")
                record_message(
                    user_id=user.id,
                    role="assistant",
                    content=assistant,
                    session_id=session_id,
                    scene="chat_choice",
                )
                log("GOAL_OUTPUT_CHOICES")
                yield _sse_event("token", {"token": assistant})
                return

            record_message(user_id=user.id, role="user", content=user_message, session_id=session_id, scene="chat")
            anchor_context = build_anchor_context(user_message, engine.history)
            source_context = await _goal_source_context(
                user_message,
                turn_count=engine.turn_count,
                history=engine.history,
            )
            extra_context = "\n\n".join(x for x in (anchor_context, source_context) if x)
            if anchor_context:
                log("ANCHOR_CONTEXT injected")
            if source_context:
                log("SOURCE_GROUNDING injected")
            with usage_context(user.id, "chat"):
                async for token in engine.respond_stream(
                    user_message,
                    extra_system_context=extra_context,
                ):
                    if token_count == 0:
                        log(f"FIRST_TOKEN after {time.time()-t0:.2f}s")
                    token_count += 1
                    assistant_text.append(token)
                    yield _sse_event("token", {"token": token})
            log(f"DONE tokens={token_count} dur={time.time()-t0:.2f}s")
            record_message(
                user_id=user.id,
                role="assistant",
                content="".join(assistant_text),
                session_id=session_id,
                scene="chat",
            )

            # 检测 GENERATE 标记（流式结束后 engine 已存好完整 assistant 文本）
            params = engine.try_extract_params()
            if params is not None:
                log("GENERATE params detected → building instance")
                try:
                    limits = _instance_limits(user.id)
                    if limits["remaining"] <= 0:
                        log("INSTANCE_LIMIT reached")
                        yield _sse_event("error", {"error": _instance_limit_message(limits)})
                        return
                    job = _start_generate_job(
                        user_id=user.id,
                        domain=params.domain,
                        target=params.target,
                    )
                    yield _sse_event(
                        "generate_start",
                        {
                            "title": "正在构建你的学习路径",
                            "message": "这一步会做资料接地、能力拆解和计划生成，通常需要几十秒。",
                            "steps": _GENERATE_PROGRESS_STEPS,
                            "job_id": job["id"],
                        },
                    )

                    # generate_instance 内部有同步 LLM 调用（学习手册润色），
                    # 必须丢到线程池，否则会阻塞整个事件循环 → 所有请求挂起。
                    # 任务自身负责归属绑定；即使 SSE 断开也不会丢用户数据。
                    task = asyncio.create_task(
                        asyncio.to_thread(
                            _run_generate_job_for_user,
                            user_id=user.id,
                            params=params,
                            job_id=job["id"],
                            session_id=session_id,
                        )
                    )
                    _attach_generate_job_task(job, task)
                    while not task.done() and job.get("status") == "generating":
                        yield _sse_event("generate_progress", _generate_job_payload(job))
                        await asyncio.sleep(2.4)
                    if job.get("status") == "generating":
                        try:
                            await asyncio.shield(task)
                        except Exception:
                            pass
                    payload = _generate_job_payload(job)
                    yield _sse_event("generate_progress", payload)
                    if payload.get("status") == "ready" and payload.get("redirect_url"):
                        _reset_session(user.id, session_id)
                        log(f"INSTANCE built: {payload.get('instance_id')} owner={user.id}")
                        yield _sse_event(
                            "generate",
                            {
                                "instance_id": payload.get("instance_id"),
                                "redirect_url": payload["redirect_url"],
                                "job_id": job["id"],
                            },
                        )
                    else:
                        yield _sse_event(
                            "error",
                            {
                                "error": payload.get("error")
                                or payload.get("message")
                                or "实例生成失败，请稍后重试。",
                                "job_id": job["id"],
                            },
                        )
                except Exception as e:  # noqa: BLE001
                    log(f"INSTANCE_FAIL: {e}")
                    yield _sse_event("error", {"error": f"实例生成失败：{e}"})
        except LLMError as e:
            log(f"LLM_ERROR: {e}")
            yield _sse_event("error", {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            log(f"UNEXPECTED: {type(e).__name__}: {e}")
            yield _sse_event("error", {"error": f"内部错误：{type(e).__name__}: {e}"})
        finally:
            log(f"END after {time.time()-t0:.2f}s")
            yield _sse_event("end", {})

    return StreamingResponse(stream(), media_type="text/event-stream")


# ============================================================================
# SSE 工具
# ============================================================================


def _sse_event(event: str, data: dict) -> str:
    """构造一条 SSE 事件。"""
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


async def _one_shot_event(event: str, data: dict) -> AsyncIterator[str]:
    yield _sse_event(event, data)
    yield _sse_event("end", {})


def _sse_stream(generator: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(generator, media_type="text/event-stream")


# ============================================================================
# 入口
# ============================================================================


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "127.0.0.1")
    # reload 会监视 iCloud 同步目录下成百上千个文件，启动极慢且易卡死。
    # 默认关闭；开发想要热重载用 ITUTOR_RELOAD=1 显式打开。
    reload = os.getenv("ITUTOR_RELOAD") == "1"
    print(f"LearnBuddy 学习伙伴启动：http://{host}:{port}（reload={reload}）")
    uvicorn.run("server:app", host=host, port=port, reload=reload)
