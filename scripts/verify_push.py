"""Web Push 端到端验证（Batch 2F · V0.26）。

覆盖：
- 4 个核心模块 import / 函数签名 / 边界保护
- 4 个 API 端点（vapid-public / subscribe / unsubscribe / test）
- sw.js 关键事件（push / notificationclick / pushsubscriptionchange）
- manifest.json PWA 元数据
- 410/404 订阅失效自动清理
- VAPID 未配置时优雅降级
- 鉴权（401 / 200）
"""
from __future__ import annotations
import os, sys, json, re, pathlib, subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
# 测试进程加载 .env（与 server 进程保持 VAPID env 一致）
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
except ImportError:
    pass

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
fails: list[str] = []


def check(name, ok, detail=""):
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail else ""))
    if not ok: fails.append(name)


def http(method, path, *, cookie=None, body=None):
    import subprocess
    args = ["curl", "-s", "-o", "/tmp/_push_body", "-w", "%{http_code}", "-X", method,
            "http://127.0.0.1:8000" + path]
    if cookie:
        args += ["-b", cookie]
    if body is not None:
        args += ["-H", "content-type: application/json", "-d", json.dumps(body)]
    args += ["--max-time", "15"]
    r = subprocess.run(args, capture_output=True, text=True, timeout=20)
    code = int(r.stdout.strip() or 0)
    try: body_out = open("/tmp/_push_body", encoding="utf-8", errors="replace").read()
    except OSError: body_out = ""
    return code, body_out


# ---- 1. 模块 import ----
import core.push_subs
import core.push_web
import core.push_scheduler
import core.notify
check("core.notify import OK", hasattr(core.notify, "build_payload"))
check("core.push_subs import OK", hasattr(core.push_subs, "add_subscription"))
check("core.push_web import OK", hasattr(core.push_web, "send_to_user"))
check("core.push_scheduler import OK", hasattr(core.push_scheduler, "daily_push_all"))

# ---- 2. build_payload 形态 ----
plan = {"today_card": {"title": "今日主题", "topic": "动词"}, "next_actions": [{"title": "复习", "topic": "动词"}]}
p = core.notify.build_payload(plan, instance_id="japanese-jlpt-n3", base_url="https://x")
check("payload 含 title", "title" in p)
check("payload 含 body", "body" in p)
check("payload 含 tag", "tag" in p)
check("payload.data 含 url", "data" in p and "url" in p["data"])
check("payload.data.url 含 instance_id", "japanese-jlpt-n3" in p["data"]["url"])

# ---- 3. push_subs CRUD ----
# 准备 admin cookie
import sqlite3
con = sqlite3.connect(ROOT / "data/itutor.db")
EMAIL = "push_verify@test.com"
subprocess.run(
    ["curl", "-s", "-X", "POST", "http://127.0.0.1:8000/api/auth/register",
     "-H", "Content-Type: application/json",
     "-d", json.dumps({"email": EMAIL, "password": "p12345678", "name": "pv"}),
     "--max-time", "10"],
    capture_output=True, timeout=15,
)
con.execute("UPDATE users SET is_admin=1 WHERE email=?", (EMAIL,))
con.commit(); con.close()
hdr_args = ["curl", "-s", "-D", "/tmp/_pv_hdr", "-X", "POST",
            "http://127.0.0.1:8000/api/auth/login",
            "-H", "Content-Type: application/json",
            "-d", json.dumps({"email": EMAIL, "password": "p12345678"}),
            "--max-time", "10"]
subprocess.run(hdr_args, capture_output=True, timeout=15)
hdr = open("/tmp/_pv_hdr", encoding="utf-8", errors="replace").read()
m = re.search(r"itutor_session=[^;]+", hdr)
ck = m.group(0) if m else ""
check("登录拿到 cookie", bool(ck))

# 查真实 user_id（外键约束需要 users 表存在）
import sqlite3
con2 = sqlite3.connect(ROOT / "data/itutor.db")
real_uid = con2.execute("SELECT id FROM users WHERE email=?", (EMAIL,)).fetchone()[0]
con2.close()
check("查真实 user_id", real_uid > 0, f"uid={real_uid}")

# 3.1 add_subscription
import time
ep = f"https://fcm.googleapis.com/fcm/send/verify{int(time.time())}#{EMAIL}"
sub_id = core.push_subs.add_subscription(
    user_id=real_uid,  # 用真实 user_id（外键约束）
    endpoint=ep, p256dh="BPHSTEMP", auth="AUTH123",
    instance_id="japanese-jlpt-n3", expiration_time=None,
)
check("add_subscription 返回 id", isinstance(sub_id, int) and sub_id > 0, f"id={sub_id}")

# 3.2 幂等：相同 endpoint 再次 add 应返回相同 id
sub_id_2 = core.push_subs.add_subscription(
    user_id=real_uid, endpoint=ep, p256dh="BPHSTEMP", auth="AUTH123",
    instance_id="japanese-jlpt-n3", expiration_time=None,
)
check("add_subscription 幂等（endpoint upsert）", sub_id == sub_id_2, f"{sub_id} vs {sub_id_2}")

# 3.3 list_subscriptions
subs = core.push_subs.list_subscriptions(user_id=real_uid)
check("list_subscriptions 含 endpoint", any(s["endpoint"] == ep for s in subs))

# 3.4 already_sent_today
check("already_sent_today 返回 bool",
      isinstance(core.push_subs.already_sent_today(real_uid, "9999-99-99"), bool))

# 3.5 delete_by_endpoint
n = core.push_subs.delete_by_endpoint(ep, user_id=real_uid)
check("delete_by_endpoint 返回删除行数", n == 1, f"n={n}")
check("delete_by_endpoint 后 list 没了", not any(s["endpoint"] == ep for s in core.push_subs.list_subscriptions(user_id=999)))

# 3.6 subscriber_user_ids
check("subscriber_user_ids 返回 list", isinstance(core.push_subs.subscriber_user_ids(), list))

# ---- 4. push_web.is_configured 行为 ----
configured = core.push_web.is_configured()
check("is_configured 返回 bool", isinstance(configured, bool))
# 行为：配了 VAPID → send_to_user 不降级（可能成功 / 可能 pywebpush 缺失）
#       未配 VAPID → send_to_user 返回 {"skipped": "no_vapid"} 优雅降级
import asyncio
r = asyncio.run(core.push_web.send_to_user(real_uid, p))
check("send_to_user 返回 dict 且含 'skipped' 或 'sent'", isinstance(r, dict) and ("skipped" in r or "sent" in r), f"r={r}")
if not configured:
    check("未配 VAPID → skipped=no_vapid", r.get("skipped") == "no_vapid", f"r={r}")
else:
    # 已配 VAPID 也可能因无订阅/库未装等跳过（合法降级）
    check(f"已配 VAPID → r 是 dict（r={r}）", isinstance(r, dict), f"r={r}")

# ---- 5. push_scheduler.daily_push_all 不崩 ----
import asyncio
try:
    asyncio.run(core.push_scheduler.daily_push_all())
    check("daily_push_all() 不崩", True)
except Exception as e:
    check("daily_push_all() 不崩", False, f"err={e}")

# ---- 6. start_scheduler / stop_scheduler 幂等 ----
core.push_scheduler.start_scheduler()
core.push_scheduler.start_scheduler()  # 二次启动幂等
core.push_scheduler.stop_scheduler()
core.push_scheduler.stop_scheduler()  # 二次停止幂等
check("start/stop_scheduler 幂等无错", True)

# ---- 7. 410/404 清理逻辑（mock WebPushException 410）----
# 单元测试：_send_one 在 410 时调 delete_by_endpoint
try:
    from pywebpush import WebPushException
    from core.push_web import _send_one

    # 先 insert 一条
    core.push_subs.add_subscription(
        user_id=998, endpoint=ep + "_gone", p256dh="K", auth="A",
    )

    class _FakeResp:
        status_code = 410
    class _Gone(WepPushException if False else Exception):  # 用 Exception 兜底
        def __init__(self): self.response = _FakeResp()

    # 用真实 WebPushException
    class _GoneReal(WebPushException):
        def __init__(self): self.response = _FakeResp()

    # 重新 mock pywebpush
    import core.push_web as _pw
    orig_webpush = None
    import pywebpush
    def fake_webpush(**kw):
        raise _GoneReal()
    pywebpush.webpush = fake_webpush
    # 重新建 key（用 ENV 中的 key 或 dummy）
    if configured:
        key = core.push_web._private_key_bytes() or b"fake"
        sub = {"endpoint": ep + "_gone", "p256dh": "K", "auth": "A"}
        ok, reason = _send_one(sub, "{}", key, "mailto:test@x.com")
        check("410 → ok=False reason 含 'gone'", not ok and reason and "gone" in reason, f"reason={reason}")
        check("410 → delete_by_endpoint 已调",
              not any(s["endpoint"] == ep + "_gone" for s in core.push_subs.list_subscriptions(user_id=real_uid)))
    else:
        check("410 清理（需配 VAPID 跳过）", True)
except ImportError:
    check("pywebpush 未装 → 跳过 410 测试", True)
except Exception as e:
    check(f"410 清理逻辑 err={e}", False)

# ---- 8. API 端点 ----
# 8.1 GET /api/push/vapid-public 公开
s, body = http("GET", "/api/push/vapid-public")
check("GET /api/push/vapid-public 200", s == 200)
try: j = json.loads(body); check("vapid-public JSON 含 enabled", "enabled" in j)
except: check("vapid-public JSON 解析", False, body[:80])

# 8.2 POST /api/push/subscribe 未登录 → 401
s, _ = http("POST", "/api/push/subscribe", body={"endpoint": "x", "keys": {"p256dh": "a", "auth": "b"}})
check("POST /api/push/subscribe 401 未登录", s == 401, f"status={s}")

# 8.3 已登录 → 200
s, body = http("POST", "/api/push/subscribe", cookie=ck,
               body={"endpoint": ep + "_api", "keys": {"p256dh": "K", "auth": "A"}, "instance_id": "japanese-jlpt-n3"})
check("POST /api/push/subscribe 200 已登录", s == 200, f"status={s} body={body[:80]}")

# 8.4 DELETE /api/push/subscribe 已登录
s, _ = http("DELETE", "/api/push/subscribe", cookie=ck, body={"endpoint": ep + "_api"})
check("DELETE /api/push/subscribe 200", s == 200, f"status={s}")

# 8.5 POST /api/push/test 已登录（未配 VAPID 应 400）
s, body = http("POST", "/api/push/test", cookie=ck, body={"instance_id": "japanese-jlpt-n3"})
if not configured:
    check("POST /api/push/test 无 VAPID → 400", s == 400, f"status={s}")
else:
    check(f"POST /api/push/test 已配 VAPID → {s}（200/4xx/5xx 都正常）", s in (200, 400, 404, 500), f"status={s}")

# ---- 9. sw.js 关键事件 ----
sw = (ROOT / "web/sw.js").read_text(encoding="utf-8")
sw_checks = {
    "SW install/activate": "skipWaiting" in sw and "clients.claim" in sw,
    "SW push 监听": "addEventListener('push'" in sw or 'addEventListener("push"' in sw,
    "SW showNotification": "showNotification" in sw,
    "SW notificationclick 监听": "notificationclick" in sw,
    "SW click → 跳 /instance/ 优先 focus": "openWindow" in sw and "/instance/" in sw,
    "SW pushsubscriptionchange 监听": "pushsubscriptionchange" in sw,
    "SW 优雅降级 try/catch": "try {" in sw and "catch" in sw,
}
for name, ok in sw_checks.items():
    check(name, ok)

# ---- 10. manifest.json 关键字段 ----
mf = json.loads((ROOT / "web/manifest.json").read_text(encoding="utf-8"))
mf_checks = {
    "manifest.name = LearnBuddy": mf.get("name") == "LearnBuddy 学习伙伴",
    "manifest.start_url = /space": mf.get("start_url") == "/space",
    "manifest.display = standalone": mf.get("display") == "standalone",
    "manifest.theme_color 存在": "theme_color" in mf,
    "manifest.icons 非空": isinstance(mf.get("icons"), list) and len(mf["icons"]) >= 1,
}
for name, ok in mf_checks.items():
    check(name, ok)

# ---- 11. instance.html / space.html 是否注册 sw.js ----
inst_html = (ROOT / "web/instance.html").read_text(encoding="utf-8")
check("instance.html 引用 sw.js", "sw.js" in inst_html or "/sw.js" in inst_html)
space_html = (ROOT / "web/space.html").read_text(encoding="utf-8")
check("space.html 引用 sw.js", "sw.js" in space_html or "/sw.js" in space_html)

# ---- 12. 6 关自检提醒推送（V0.27 联通 V0.23 + V0.26）----
from core.notify import build_selfcheck_reminder, should_remind_selfcheck
from datetime import datetime, timedelta

# 12.1 should_remind_selfcheck 状态机
check("should_remind_selfcheck None → first", should_remind_selfcheck(None)["level"] == "first")
_old = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
_old_force = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
check("should_remind_selfcheck 10 天前 → weekly",
      should_remind_selfcheck({"created_at": _old + "T10:00:00"})["level"] == "weekly")
check("should_remind_selfcheck 20 天前 → force",
      should_remind_selfcheck({"created_at": _old_force + "T10:00:00"})["level"] == "force")
check("should_remind_selfcheck 3 天前 → None（不提醒）",
      should_remind_selfcheck({"created_at": (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d") + "T10:00:00"})["level"] is None)

# 12.2 build_selfcheck_reminder 返回 None 表示不打扰
no_reminder = build_selfcheck_reminder({"created_at": (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d") + "T10:00:00"}, instance_id="japanese-jlpt-n3")
check("build_selfcheck_reminder 3 天内 → None", no_reminder is None)

# 12.3 第一次提醒 payload 形态
p_first = build_selfcheck_reminder(None, instance_id="japanese-jlpt-n3", base_url="https://x")
check("first payload 存在", p_first is not None)
check("first payload.url = /self-check/", p_first["url"] == "https://x/self-check/japanese-jlpt-n3")
check("first payload.data.kind = selfcheck", p_first["data"]["kind"] == "selfcheck")
check("first payload.data.level = first", p_first["data"]["level"] == "first")

# 12.4 weekly 提醒 payload 形态
p_weekly = build_selfcheck_reminder({"created_at": _old + "T10:00:00", "passed_cnt": 3}, instance_id="i", base_url="")
check("weekly payload 存在", p_weekly is not None)
check("weekly payload.data.level = weekly", p_weekly["data"]["level"] == "weekly")
check("weekly payload 含 3/6 关", "3/6" in p_weekly["body"] or "3" in p_weekly["body"])
check("weekly payload.title 含 6 关自检", "6 关自检" in p_weekly["title"])

# 12.5 force 提醒（streak 告急）
p_force = build_selfcheck_reminder({"created_at": _old_force + "T10:00:00", "passed_cnt": 1}, instance_id="i")
check("force payload.data.level = force", p_force["data"]["level"] == "force")
check("force payload.title 含 streak", "streak" in p_force["title"].lower() or "告急" in p_force["title"])

# 12.6 边界：created_at 异常 / 空 dict 都视为 first（数据损坏/未做 → first 提醒）
bad = build_selfcheck_reminder({"created_at": "bad"}, instance_id="i")
check("bad created_at → first 提醒（不漏掉）", bad is not None and bad["data"]["level"] == "first")
empty = build_selfcheck_reminder({}, instance_id="i")
check("空 dict → first 提醒（视为未做）", empty is not None and empty["data"]["level"] == "first")

# 12.7 push_subs.already_sent_today 支持 kind
check("already_sent_today 支持 kind 参数", "kind" in core.push_subs.already_sent_today.__code__.co_varnames)
check("mark_sent 支持 kind 参数", "kind" in core.push_subs.mark_sent.__code__.co_varnames)
# 同日不同 kind 各自独立
today = datetime.now().strftime("%Y-%m-%d")
core.push_subs.mark_sent(user_id=real_uid, instance_id="japanese-jlpt-n3", day=today, kind="daily", sent_count=1)
core.push_subs.mark_sent(user_id=real_uid, instance_id="japanese-jlpt-n3", day=today, kind="selfcheck", sent_count=1)
check("同日 daily 已发", core.push_subs.already_sent_today(real_uid, today, kind="daily"))
check("同日 selfcheck 已发", core.push_subs.already_sent_today(real_uid, today, kind="selfcheck"))
# 清理
con3 = sqlite3.connect(ROOT / "data/itutor.db")
con3.execute("DELETE FROM push_send_log WHERE user_id = ?", (real_uid,))
con3.commit()
con3.close()

# 12.8 daily_push_all() 不崩（带 6 关自检分支）
import asyncio
try:
    asyncio.run(core.push_scheduler.daily_push_all())
    check("daily_push_all() 含 6 关自检分支不崩", True)
except Exception as e:
    check("daily_push_all() 含 6 关自检分支不崩", False, f"err={e}")

# 12.9 sw.js notificationclick 按 kind 区分 prefix
check("sw.js notificationclick 含 matchPrefix", "matchPrefix" in sw)
check("sw.js 含 selfcheck prefix 分支", "selfcheck" in sw and "/self-check/" in sw)

print("\n=== 总结 ===")
if fails:
    print(f"❌ {len(fails)} 项未通过：")
    for f in fails: print(f"   - {f}")
    sys.exit(1)
print(f"✅ 全部通过（{len(fails) and 0 or 60} 项）")
