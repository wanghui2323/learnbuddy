"""web/index.html 关键交互契约测试。"""

from __future__ import annotations

from pathlib import Path


WEB_INDEX = Path(__file__).resolve().parent.parent / "web" / "index.html"


def test_quiz_payload_keeps_full_prompt_and_dimension():
    html = WEB_INDEX.read_text(encoding="utf-8")

    assert "slice(0, 30)" not in html
    assert "const dim = q.dimension ? `【${q.dimension}】` : '';" in html
    assert "const prompt = String(q.prompt || '').replace(/\\s+/g, ' ').trim();" in html
    assert "作答信心 ${confidence[qid]}/5" in html
    assert "verification_notes" in html


def test_quiz_uses_persisted_two_stage_observed_assessment():
    html = WEB_INDEX.read_text(encoding="utf-8")

    assert "function createBaselineAssessment" in html
    assert "'/api/baseline/assessments'" in html
    assert "phase: stage" in html
    assert "self_confidence" in html
    assert "/assist" in html
    assert "/responses/${encodeURIComponent(qid)}" in html
    assert "证据 →" in html
    assert "查看题目级评分依据" in html
    assert "对评分有异议" in html
    assert "/dispute" in html
    assert "function resumeBaselineAssessment" in html
    assert "/api/baseline/assessments/current?session_id=" in html
    assert "assessment_id=${assessmentId}" in html


def test_generate_progress_events_are_visible():
    html = WEB_INDEX.read_text(encoding="utf-8")

    assert "generate_start" in html
    assert "generate_progress" in html
    assert "function showGenerateOverlay" in html
    assert "function updateGenerateProgress" in html
    assert "id=\"gen-bar\"" in html
    assert "id=\"gen-live\"" in html
    assert "progress_percent" in html


def test_generate_job_can_recover_after_stream_disconnect():
    html = WEB_INDEX.read_text(encoding="utf-8")

    assert "const GENERATE_JOB_KEY = 'learnbuddy:active_generate_job';" in html
    assert "function rememberGenerateJob" in html
    assert "function pollGenerateJob" in html
    assert "/api/generate/status?job_id=" in html
    assert "function resumeGenerateJob" in html
    assert "sessionStorage.setItem(GENERATE_JOB_KEY, jobId)" in html
    assert "sessionStorage.removeItem(GENERATE_JOB_KEY)" in html
    assert "resumeGenerateJob();" in html
    assert "连接中断，正在继续检查后台生成结果" in html


def test_conversation_session_rotates_by_owner_and_restores_visibly():
    html = WEB_INDEX.read_text(encoding="utf-8")

    assert "const SESSION_OWNER_KEY = 'learnbuddy:session_owner';" in html
    assert "storedOwnerId !== ownerId" in html
    assert "sessionStorage.removeItem(GENERATE_JOB_KEY);" in html
    assert "function restoreConversation()" in html
    assert "/api/conversation/session?session_id=" in html
    assert "setStatus('已恢复上次对话');" in html
    assert "await loadOpening();" in html
    assert "const choices = index === messages.length - 1 ? parseChoices(message.content) : null;" in html
    assert "renderChoices(choices, bubble);" in html


def test_generate_confirmation_shows_preparing_overlay_immediately():
    html = WEB_INDEX.read_text(encoding="utf-8")

    assert "function looksLikeGenerateConfirmation" in html
    assert "function showGeneratePreparingOverlay" in html
    assert "正在准备学习路径生成" in html
    assert "if (optimisticGenerate) showGeneratePreparingOverlay();" in html
    assert "整理生成参数" in html
    assert "function hideGenerateOverlay" in html


def test_preparing_overlay_uses_local_elapsed_timer_until_job_starts():
    html = WEB_INDEX.read_text(encoding="utf-8")

    assert "let generateLocalTimer = null;" in html
    assert "function startGenerateLocalTimer" in html
    assert "function stopGenerateLocalTimer" in html
    assert "setInterval(tick, 1000)" in html
    assert "startGenerateLocalTimer({" in html
    assert "if (data.job_id) stopGenerateLocalTimer();" in html
