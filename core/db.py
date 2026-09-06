"""SQLite 数据层（V1 用户体系起步）。

V0/V0.5 学习实例仍存 JSON 文件（data/instances/）；这里只承载
"需要跨请求、需要唯一性约束"的结构化数据——目前是用户与会话。

设计取向：
- 用标准库 sqlite3，零额外依赖。
- 单文件 data/itutor.db，WAL 模式，足够支撑单机多用户。
- 连接每次现开现关（sqlite3 轻量），避免线程共享连接的坑。
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "itutor.db"


def _db_path() -> Path:
    """允许用环境变量覆盖（测试用临时库）。"""
    override = os.getenv("ITUTOR_DB_PATH")
    return Path(override) if override else DEFAULT_DB_PATH


def get_conn() -> sqlite3.Connection:
    """打开一个连接（行可按列名访问）。调用方负责 close。"""
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT    NOT NULL UNIQUE,
    name          TEXT,
    password_hash TEXT    NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    token_limit   INTEGER,
    created_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT    PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT    NOT NULL,
    expires_at TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

-- LLM token 消耗流水（成本归因的事实表）
CREATE TABLE IF NOT EXISTS usage (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at        TEXT    NOT NULL,
    model             TEXT,
    scene             TEXT,            -- chat / generate / lesson
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens      INTEGER NOT NULL DEFAULT 0,
    instance_id       TEXT
);

CREATE INDEX IF NOT EXISTS idx_usage_user ON usage(user_id);
CREATE INDEX IF NOT EXISTS idx_usage_created ON usage(created_at);

-- 用户行为事件（行为分析用）
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at  TEXT    NOT NULL,
    action      TEXT    NOT NULL,      -- register/login/logout/chat/generate/lesson/...
    detail      TEXT,
    instance_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_id);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);

-- 逐题作答流水（学情/错题本的事实表）
CREATE TABLE IF NOT EXISTS learning_answers (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    instance_id  TEXT    NOT NULL,
    topic        TEXT    NOT NULL,
    question     TEXT    NOT NULL,
    options      TEXT,                    -- json array（重做/错题本用）
    answer_index INTEGER,                 -- 正确选项下标
    chosen_index INTEGER,                 -- 用户所选下标
    correct      INTEGER NOT NULL DEFAULT 0,
    why          TEXT,
    created_at   TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_answers_user_inst ON learning_answers(user_id, instance_id);
CREATE INDEX IF NOT EXISTS idx_answers_topic ON learning_answers(user_id, instance_id, topic);

-- 学练单元完成记录（双坐标/复习队列的事实表）
CREATE TABLE IF NOT EXISTS lesson_completions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    instance_id   TEXT    NOT NULL,
    topic         TEXT    NOT NULL,
    week          INTEGER,
    correct_count INTEGER NOT NULL DEFAULT 0,
    total         INTEGER NOT NULL DEFAULT 0,
    self_rating   TEXT,                   -- cant / with_help / independent
    created_at    TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_completions_user_inst ON lesson_completions(user_id, instance_id);
CREATE INDEX IF NOT EXISTS idx_completions_topic ON lesson_completions(user_id, instance_id, topic);

-- 学习产物（4-2-1 的"1 产出物" + 知识沉淀）：每节课用户的主动产出/复述
CREATE TABLE IF NOT EXISTS artifacts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    instance_id   TEXT    NOT NULL,
    topic         TEXT    NOT NULL,
    kind          TEXT    NOT NULL DEFAULT 'recall',  -- recall / note / code
    prompt        TEXT,                                -- 当时的产出任务
    content       TEXT    NOT NULL,                    -- 用户的产出内容
    created_at    TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_artifacts_user_inst ON artifacts(user_id, instance_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_topic ON artifacts(user_id, instance_id, topic);

-- RAG 知识库：来源 + 切块（dense 向量可选，未配 embedding 时为 NULL，走 BM25）
CREATE TABLE IF NOT EXISTS kb_sources (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    instance_id   TEXT    NOT NULL,
    title         TEXT    NOT NULL,
    kind          TEXT    NOT NULL DEFAULT 'text',   -- text / url / file
    n_chunks      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_kb_sources_user_inst ON kb_sources(user_id, instance_id);

CREATE TABLE IF NOT EXISTS kb_chunks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id     INTEGER NOT NULL REFERENCES kb_sources(id) ON DELETE CASCADE,
    user_id       INTEGER NOT NULL,
    instance_id   TEXT    NOT NULL,
    ord           INTEGER NOT NULL DEFAULT 0,
    text          TEXT    NOT NULL,
    embedding     TEXT,                              -- JSON float[]；NULL = 未向量化
    created_at    TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_kb_chunks_user_inst ON kb_chunks(user_id, instance_id);
CREATE INDEX IF NOT EXISTS idx_kb_chunks_source ON kb_chunks(source_id);

-- 用户对 AI 产出的显式反馈（Batch L · 环 B 的"眼睛"：质量信号事实表）
CREATE TABLE IF NOT EXISTS feedback (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER REFERENCES users(id) ON DELETE SET NULL,
    instance_id  TEXT,
    scene        TEXT    NOT NULL DEFAULT 'lesson',  -- lesson / coach / graph / ...
    topic        TEXT,                                -- 反馈针对的主题/对象
    rating       TEXT    NOT NULL,                    -- up / down / error
    reason       TEXT,                                -- 文字补充（"这里错了"/regenerate 原因）
    created_at   TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_feedback_scene ON feedback(scene, rating);
CREATE INDEX IF NOT EXISTS idx_feedback_user_inst ON feedback(user_id, instance_id);

-- LLM 调用追踪（Batch L · 极简 Tracing：延迟 / 成败 / 是否兜底，补 usage 表缺的质量维度）
CREATE TABLE IF NOT EXISTS llm_traces (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER REFERENCES users(id) ON DELETE SET NULL,
    instance_id   TEXT,
    scene         TEXT    NOT NULL DEFAULT 'other',
    ok            INTEGER NOT NULL DEFAULT 1,        -- 1=成功 0=异常
    fallback      INTEGER NOT NULL DEFAULT 0,        -- 1=走了兜底（LLM 失败/解析失败）
    latency_ms    INTEGER NOT NULL DEFAULT 0,
    error         TEXT,                               -- 异常类型/摘要（成功为空）
    created_at    TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_traces_scene ON llm_traces(scene);
CREATE INDEX IF NOT EXISTS idx_traces_created ON llm_traces(created_at);

-- 个人记忆（Batch L · 环 A 的"记忆"：让系统越用越懂你）
-- instance_id 为空 = 跨学习空间的全局记忆（偏好/风格）；非空 = 某空间内的记忆（弱项/强项/进度）
-- 派生记忆（source=derived）用 (user_id,instance_id,kind,mkey) 幂等 upsert；手写记忆 mkey 为空。
CREATE TABLE IF NOT EXISTS user_memory (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    instance_id  TEXT,
    kind         TEXT    NOT NULL DEFAULT 'fact',  -- struggle/strength/preference/goal/pace/fact
    mkey         TEXT,                              -- 派生记忆的去重键（如 struggle:某主题）
    content      TEXT    NOT NULL,                  -- 一句话记忆
    weight       REAL    NOT NULL DEFAULT 1.0,      -- 注入排序权重
    source       TEXT    NOT NULL DEFAULT 'derived',-- derived/user/feedback
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memory_user ON user_memory(user_id, instance_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_dedup ON user_memory(user_id, instance_id, kind, mkey);

-- Web Push 订阅（轨道 A 公共地基）：一个用户多设备；endpoint 唯一让重复订阅幂等 upsert
CREATE TABLE IF NOT EXISTS push_subs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    instance_id     TEXT,
    endpoint        TEXT NOT NULL,
    p256dh          TEXT NOT NULL,
    auth            TEXT NOT NULL,
    expiration_time INTEGER,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_push_subs_user ON push_subs(user_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_push_subs_endpoint ON push_subs(endpoint);

-- 推送发送流水：一用户一天一条的去重键（防同日重发 / 多 worker 重复执行）
CREATE TABLE IF NOT EXISTS push_send_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    instance_id  TEXT,
    day          TEXT NOT NULL,            -- YYYY-MM-DD
    sent_count   INTEGER NOT NULL DEFAULT 0,
    skipped      TEXT,                     -- no_vapid / no_sub / ...
    created_at   TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_push_log_user_day ON push_send_log(user_id, day);

-- 外部消息通道身份绑定：一个飞书发送者只能归属一个 LearnBuddy 用户。
CREATE TABLE IF NOT EXISTS channel_bindings (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider         TEXT    NOT NULL DEFAULT 'feishu',
    account_id       TEXT    NOT NULL DEFAULT 'default',
    tenant_key       TEXT,
    external_user_id TEXT    NOT NULL,
    status           TEXT    NOT NULL DEFAULT 'active', -- active / revoked
    created_at       TEXT    NOT NULL,
    updated_at       TEXT    NOT NULL,
    last_seen_at     TEXT,
    revoked_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_channel_bindings_user
    ON channel_bindings(user_id, provider, account_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_channel_active_identity
    ON channel_bindings(provider, account_id, external_user_id)
    WHERE status = 'active';
CREATE UNIQUE INDEX IF NOT EXISTS idx_channel_active_user
    ON channel_bindings(user_id, provider, account_id)
    WHERE status = 'active';

-- 网页→机器人的一次性绑定码；库内只保存 HMAC 摘要，不保存明文。
CREATE TABLE IF NOT EXISTS channel_binding_codes (
    id          TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider    TEXT    NOT NULL DEFAULT 'feishu',
    account_id  TEXT    NOT NULL DEFAULT 'default',
    code_digest TEXT    NOT NULL UNIQUE,
    status      TEXT    NOT NULL DEFAULT 'pending', -- pending / consumed / expired
    expires_at  TEXT    NOT NULL,
    consumed_at TEXT,
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_binding_codes_user
    ON channel_binding_codes(user_id, provider, account_id, status);

-- 用户级提醒规则；无有效通道绑定时保存但不发送。
CREATE TABLE IF NOT EXISTS reminder_policies (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    instance_id   TEXT,
    timezone      TEXT    NOT NULL DEFAULT 'Asia/Shanghai',
    local_time    TEXT    NOT NULL DEFAULT '08:00',
    weekdays_json TEXT    NOT NULL DEFAULT '[1,2,3,4,5,6,7]',
    quiet_start   TEXT,
    quiet_end     TEXT,
    enabled       INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reminder_policies_enabled
    ON reminder_policies(enabled, local_time);

-- 提醒发送事实表；幂等键防止多 worker / 重试重复发送。
CREATE TABLE IF NOT EXISTS notification_deliveries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    policy_id       INTEGER NOT NULL REFERENCES reminder_policies(id) ON DELETE CASCADE,
    binding_id      INTEGER NOT NULL REFERENCES channel_bindings(id) ON DELETE CASCADE,
    scheduled_for   TEXT    NOT NULL,
    idempotency_key TEXT    NOT NULL UNIQUE,
    status          TEXT    NOT NULL DEFAULT 'pending', -- pending / sent / failed / skipped
    payload_json    TEXT    NOT NULL DEFAULT '{}',
    attempts        INTEGER NOT NULL DEFAULT 0,
    claim_token     TEXT,
    claimed_until   TEXT,
    error           TEXT,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    sent_at         TEXT
);
CREATE INDEX IF NOT EXISTS idx_notification_pending
    ON notification_deliveries(status, created_at);
CREATE INDEX IF NOT EXISTS idx_notification_user
    ON notification_deliveries(user_id, created_at);

-- 积分账本：正数=赠送/充值，负数=LLM 调用消耗。只追加流水，不回写历史 usage。
CREATE TABLE IF NOT EXISTS credit_ledger (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    delta      INTEGER NOT NULL,
    reason     TEXT    NOT NULL DEFAULT 'adjust',
    detail     TEXT,
    admin_id   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    card_code  TEXT,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_credit_ledger_user ON credit_ledger(user_id);
CREATE INDEX IF NOT EXISTS idx_credit_ledger_created ON credit_ledger(created_at);

-- 积分卡：管理员生成，用户兑换后入账。code 不含敏感信息，可复制给用户。
CREATE TABLE IF NOT EXISTS credit_cards (
    code        TEXT PRIMARY KEY,
    points      INTEGER NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'unused',
    note        TEXT,
    created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    redeemed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    redeemed_at TEXT,
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_credit_cards_status ON credit_cards(status);

-- 对话会话归属：session_id 必须绑定用户，防止同浏览器切号或伪造 id 串话。
CREATE TABLE IF NOT EXISTS conversation_sessions (
    session_id TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversation_sessions_user
    ON conversation_sessions(user_id, updated_at);

-- 对话消息流水：既用于产品分析，也作为服务重启后的运行时上下文事实源。
CREATE TABLE IF NOT EXISTS conversation_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    session_id  TEXT,
    instance_id TEXT,
    scene       TEXT    NOT NULL DEFAULT 'chat', -- chat / coach / ...
    role        TEXT    NOT NULL,                -- user / assistant / system
    content     TEXT    NOT NULL,
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversation_user ON conversation_messages(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_conversation_session ON conversation_messages(session_id);
CREATE INDEX IF NOT EXISTS idx_conversation_owner_session
    ON conversation_messages(user_id, session_id, id);

-- 长任务事实表：路径生成与学练生成的状态不再只存在进程内存中。
CREATE TABLE IF NOT EXISTS background_jobs (
    id           TEXT PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind         TEXT    NOT NULL, -- generate / lesson
    instance_id  TEXT,
    status       TEXT    NOT NULL,
    stage_index  INTEGER NOT NULL DEFAULT 0,
    stage_id     TEXT,
    payload_json TEXT    NOT NULL DEFAULT '{}',
    result_json  TEXT    NOT NULL DEFAULT '{}',
    error        TEXT,
    started_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL,
    finished_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_background_jobs_user
    ON background_jobs(user_id, kind, updated_at);
CREATE INDEX IF NOT EXISTS idx_background_jobs_status
    ON background_jobs(status, updated_at);

-- 关键接口限流窗口（actor 只保存不可逆摘要，不落 IP/邮箱明文）。
CREATE TABLE IF NOT EXISTS rate_limit_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    actor      TEXT NOT NULL,
    scope      TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rate_limit_window
    ON rate_limit_events(actor, scope, created_at);

-- 入学基线评估：题目快照、原始回答和双坐标都以 assessment_id 为事实源。
CREATE TABLE IF NOT EXISTS baseline_assessments (
    id            TEXT PRIMARY KEY,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id    TEXT    NOT NULL,
    instance_id   TEXT,
    domain        TEXT    NOT NULL,
    baseline_hint TEXT,
    status        TEXT    NOT NULL DEFAULT 'draft', -- draft / submitted
    questions     TEXT    NOT NULL,                 -- EvalQuestion[] JSON
    summary       TEXT,                            -- BaselineProfile JSON
    contract_version TEXT NOT NULL DEFAULT 'baseline.v0.53',
    mode           TEXT NOT NULL DEFAULT 'quick',
    stage          TEXT NOT NULL DEFAULT 'independent',
    blueprint_json TEXT NOT NULL DEFAULT '{}',
    profile_confidence REAL,
    error          TEXT,
    superseded_by  TEXT,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL,
    submitted_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_baseline_user_session ON baseline_assessments(user_id, session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_baseline_user_instance ON baseline_assessments(user_id, instance_id);

CREATE TABLE IF NOT EXISTS baseline_responses (
    assessment_id    TEXT    NOT NULL REFERENCES baseline_assessments(id) ON DELETE CASCADE,
    question_id      TEXT    NOT NULL,
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    dimension        TEXT    NOT NULL,
    answer           TEXT    NOT NULL,
    independent_score INTEGER NOT NULL CHECK(independent_score BETWEEN 0 AND 5),
    with_ai_score     INTEGER NOT NULL CHECK(with_ai_score BETWEEN 0 AND 5),
    phase             TEXT NOT NULL DEFAULT 'legacy',
    self_confidence   INTEGER CHECK(self_confidence BETWEEN 0 AND 5),
    verification_notes TEXT,
    assist_trace      TEXT NOT NULL DEFAULT '{}',
    duration_ms       INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT    NOT NULL,
    updated_at        TEXT,
    PRIMARY KEY (assessment_id, question_id)
);
CREATE INDEX IF NOT EXISTS idx_baseline_responses_user ON baseline_responses(user_id, assessment_id);

-- V0.53 单题评分证据：能力画像不得再由用户自评分直接生成。
CREATE TABLE IF NOT EXISTS baseline_score_evidence (
    id                TEXT PRIMARY KEY,
    assessment_id     TEXT NOT NULL REFERENCES baseline_assessments(id) ON DELETE CASCADE,
    question_id       TEXT NOT NULL,
    user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    phase             TEXT NOT NULL,
    capability        TEXT NOT NULL,
    observed_score    REAL NOT NULL CHECK(observed_score BETWEEN 0 AND 100),
    confidence        REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
    grading_method    TEXT NOT NULL,
    reason            TEXT NOT NULL,
    rubric_scores_json TEXT NOT NULL DEFAULT '{}',
    flags_json        TEXT NOT NULL DEFAULT '[]',
    evaluator_version TEXT NOT NULL,
    prompt_version    TEXT,
    model             TEXT,
    latency_ms        INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    UNIQUE (assessment_id, question_id)
);
CREATE INDEX IF NOT EXISTS idx_baseline_evidence_user ON baseline_score_evidence(user_id, assessment_id);

CREATE TABLE IF NOT EXISTS baseline_profiles (
    id             TEXT PRIMARY KEY,
    assessment_id  TEXT NOT NULL UNIQUE REFERENCES baseline_assessments(id) ON DELETE CASCADE,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    version        TEXT NOT NULL,
    status         TEXT NOT NULL,
    confidence     REAL NOT NULL,
    profile_json   TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_baseline_profiles_user ON baseline_profiles(user_id, assessment_id);

CREATE TABLE IF NOT EXISTS baseline_assist_messages (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    assessment_id  TEXT NOT NULL REFERENCES baseline_assessments(id) ON DELETE CASCADE,
    question_id    TEXT NOT NULL,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role           TEXT NOT NULL,
    content        TEXT NOT NULL,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_baseline_assist ON baseline_assist_messages(user_id, assessment_id, question_id, id);

CREATE TABLE IF NOT EXISTS baseline_disputes (
    id             TEXT PRIMARY KEY,
    assessment_id  TEXT NOT NULL REFERENCES baseline_assessments(id) ON DELETE CASCADE,
    evidence_id    TEXT,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    reason         TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'open',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_baseline_disputes_user ON baseline_disputes(user_id, assessment_id);

-- 动态重规划提案：检测与应用分离，只有用户确认才修改 master。
CREATE TABLE IF NOT EXISTS replan_proposals (
    id              TEXT PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    instance_id     TEXT    NOT NULL,
    trigger_kind    TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'proposed', -- proposed/applied/rejected/undone
    proposal_json   TEXT    NOT NULL,
    before_snapshot TEXT    NOT NULL,
    after_snapshot  TEXT,
    before_hash     TEXT    NOT NULL,
    after_hash      TEXT,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    applied_at      TEXT,
    decided_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_replan_user_inst ON replan_proposals(user_id, instance_id, created_at);

-- 每日运营分析快照：从对话/反馈/tracing/积分等事实表汇总，不覆盖原始数据。
CREATE TABLE IF NOT EXISTS daily_analysis_reports (
    day         TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    metrics     TEXT NOT NULL,
    issues      TEXT NOT NULL,
    suggestions TEXT NOT NULL
);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """对已存在的库做轻量列补齐（SQLite 支持 ADD COLUMN 带默认值）。"""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    if "is_admin" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
    if "token_limit" not in cols:
        # 每月 token 上限；NULL = 用环境默认；0 = 无限
        conn.execute("ALTER TABLE users ADD COLUMN token_limit INTEGER")

    def add_columns(table: str, definitions: dict[str, str]) -> None:
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, definition in definitions.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    add_columns(
        "baseline_assessments",
        {
            "contract_version": "TEXT NOT NULL DEFAULT 'baseline.v0.50.2'",
            "mode": "TEXT NOT NULL DEFAULT 'quick'",
            "stage": "TEXT NOT NULL DEFAULT 'legacy'",
            "blueprint_json": "TEXT NOT NULL DEFAULT '{}'",
            "profile_confidence": "REAL",
            "error": "TEXT",
            "superseded_by": "TEXT",
        },
    )
    add_columns(
        "baseline_responses",
        {
            "phase": "TEXT NOT NULL DEFAULT 'legacy'",
            "self_confidence": "INTEGER",
            "verification_notes": "TEXT",
            "assist_trace": "TEXT NOT NULL DEFAULT '{}'",
            "duration_ms": "INTEGER NOT NULL DEFAULT 0",
            "updated_at": "TEXT",
        },
    )


def init_db() -> None:
    """建表（幂等）+ 迁移。服务启动时调用一次。"""
    conn = get_conn()
    try:
        conn.executescript(_SCHEMA)
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


__all__ = ["get_conn", "init_db", "DEFAULT_DB_PATH"]
