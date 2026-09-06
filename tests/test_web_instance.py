"""web/instance.html 学习页关键交互契约测试。"""

from __future__ import annotations

from pathlib import Path


WEB_INSTANCE = Path(__file__).resolve().parent.parent / "web" / "instance.html"
COMMON_JS = Path(__file__).resolve().parent.parent / "web" / "common.js"


def test_lesson_generation_shows_agent_process_steps():
    html = WEB_INSTANCE.read_text(encoding="utf-8")

    assert "function renderLessonAgentLoading" in html
    assert "const LESSON_FETCH_TIMEOUT_MS" in html
    assert "new AbortController()" in html
    assert "async function pollLessonResult" in html
    assert "/lesson/status?" in html
    assert "loading.update(data)" in html
    assert "progress_percent" in html
    assert "data.steps" in html
    assert ".agent-step.error" in html
    assert "请求已转入后台生成" in html
    assert "不需要手动刷新" in html
    assert "本节生成没有完成" in html
    assert "后台仍在检查结果" in html
    assert "data.deadline_exceeded" in html
    assert "i < 150" in html
    assert "LearnBuddy 正在构建本节学习任务" in html
    assert "检索你的资料库" in html
    assert "整理外部参考来源" in html
    assert "设计教学链路" in html
    assert "质量审查与保存" in html
    assert "首次生成会更久一些，因为要检索来源、生成互动内容并做质量审查。" in html
    assert "等待时间已经偏长" in html


def test_lesson_page_shows_instructional_design_before_content():
    html = WEB_INSTANCE.read_text(encoding="utf-8")

    assert "function lessonDesign" in html
    assert "function designBriefHtml" in html
    assert "function designMapHtml" in html
    assert "function lessonVisualCardHtml" in html
    assert "function diagramNodesFromCard" in html
    assert "function asciiDiagramNodes" in html
    assert "function stripAsciiDiagram" in html
    assert "compact.split(/(?:——|—|→|->|，|,|、)" in html
    assert "本节问题" in html
    assert "学完能做" in html
    assert "学习主线" in html
    assert "本节怎么学" in html
    assert "diagram-node" in html
    assert "pedagogy: '课设'" in html


def test_lesson_prime_choice_does_not_overwrite_manual_input():
    html = WEB_INSTANCE.read_text(encoding="utf-8")

    assert "点选项不会覆盖这里的输入" in html
    assert "state.primeChoice = b.dataset.v || b.textContent.trim();" in html
    assert "state.primeCustom = pt ? pt.value.trim() : '';" in html
    assert "ta.value = b.dataset.v" not in html
    assert "ta.value = b.textContent" not in html


def test_path_loop_state_drives_home_and_plan_progress():
    html = WEB_INSTANCE.read_text(encoding="utf-8")

    assert "function pathLoop()" in html
    assert "function loadPathLoop" in html
    assert "/path-loop" in html
    assert "function schedulePathLoopPoll" in html
    assert "path_loop" in html
    assert "pre_generation" in html
    assert "正在准备这一天" in html
    assert "这一天已准备好" in html
    assert "unitForDay(wk, day)" in html
    assert "DATA.path_loop = d.path_loop" in html


def test_llm_markdown_uses_one_allowlist_sanitizer_without_bypass():
    html = WEB_INSTANCE.read_text(encoding="utf-8")
    common = COMMON_JS.read_text(encoding="utf-8")

    assert '<script src="/common.js?v=0.50"></script>' in html.split("</head>", 1)[0]
    assert "window.renderSafeMarkdown(t, false)" in html
    assert "window.renderSafeMarkdown(t, true)" in html
    assert "body.innerHTML = md(" in html
    assert "marked.parse(" not in html
    assert "marked.parseInline(" not in html
    assert "function sanitizeHtml(html)" in common
    assert "MARKDOWN_TAGS" in common
    assert "BLOCKED_MARKDOWN_TAGS" in common
    assert "javascript|vbscript|data" in common
    assert "Array.from(el.attributes)" in common


def test_primary_path_navigation_has_button_tab_semantics():
    html = WEB_INSTANCE.read_text(encoding="utf-8")

    assert 'role="tablist"' in html
    assert html.count('role="tab"') == 4
    assert html.count('type="button" role="tab"') == 4
    assert 'aria-selected="true"' in html
    assert ".nav button { width: 100%; min-height: 44px;" in html
    assert "button.setAttribute('aria-selected', selected ? 'true' : 'false')" in html
    assert "document.querySelectorAll('.nav [data-view]')" in html
    assert "event.key !== 'Enter' && event.key !== ' '" in html
    assert "go(button.dataset.view)" in html
    assert ".nav a" not in html


def test_future_weeks_do_not_render_fake_daily_preview_and_use_self_check_gate():
    html = WEB_INSTANCE.read_text(encoding="utf-8")

    assert "function weekPreviewDays" not in html
    assert "详细日计划尚未生成" in html
    assert "至少 4/6 关自检" in html
    assert "function selfCheckUrl" in html
    assert "开始 W${wk.week} · 6 关自检" in html


def test_replan_ui_requires_explicit_confirmation_and_supports_reject_undo():
    html = WEB_INSTANCE.read_text(encoding="utf-8")

    assert "function createReplanProposal" in html
    assert "function decideReplan" in html
    assert "确认应用" in html
    assert "暂不调整" in html
    assert "撤销本次调整" in html
