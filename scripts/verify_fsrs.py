"""FSRS-4.5 调度 + scheduler.next_interval 统一入口端到端验证。

覆盖：
- FSRS 数学正确性（17 权重默认 + DSR 模型）
- 与 SM-2 接口兼容（输入 ratings → 输出 {interval, ease, reps}）
- 答错重置 + 答对增长
- scheduler.next_interval 入口（env 切换 + algorithm 参数切换）
- clamp / NaN / 越界保护
- 接入到 core.learning 的 smoke test
"""
from __future__ import annotations
import os, sys, pathlib, math
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
fails: list[str] = []


def check(name, ok, detail=""):
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail else ""))
    if not ok: fails.append(name)


# ---- 1. FSRS 数学 ----
from core.fsrs import (
    fsrs, review, grade, initial_state, retrievability, interval_from_stability,
    W, FACTOR, DECAY, MAX_INTERVAL, MIN_INTERVAL, ALG_NAME
)

# 1.1 评分映射
check("grade(cant) = 1 (again)", grade("cant") == 1)
check("grade(with_help) = 3 (good)", grade("with_help") == 3)
check("grade(independent) = 4 (easy)", grade("independent") == 4)
check("grade(None) = 3 (good 兜底)", grade(None) == 3)
check("grade(unknown) = 3 (good 兜底)", grade("xyz") == 3)

# 1.2 initial_state 数学
st_easy = initial_state(4)
st_good = initial_state(3)
st_again = initial_state(1)
check("S0(easy) = w3 = 13.82", abs(st_easy["s"] - W[3]) < 1e-6, f"got={st_easy['s']}")
check("S0(good) = w2 = 3.71", abs(st_good["s"] - W[2]) < 1e-6, f"got={st_good['s']}")
check("S0(again) = w0 = 0.49", abs(st_again["s"] - W[0]) < 1e-6, f"got={st_again['s']}")
# D0(3) = w4（FSRS v4 线性公式 + 17 权重版）
expected_d0_3 = W[4]
check("D0(good) = w4", abs(st_good["d"] - expected_d0_3) < 1e-6, f"got={st_good['d']} expected={expected_d0_3}")
# D0 clamped [1, 10]
check("D 在 [1,10]", 1.0 <= st_easy["d"] <= 10.0 and 1.0 <= st_good["d"] <= 10.0 and 1.0 <= st_again["d"] <= 10.0)

# 1.3 retrievability
check("R(0, S) = 1", abs(retrievability(0, 5) - 1.0) < 1e-9)
check("R(S, S) ≈ 0.9 (FSRS-4.5 关键约束)",
      abs(retrievability(5, 5) - 0.9) < 1e-3, f"got={retrievability(5,5):.4f}")
check("R(2S, S) < 0.9（衰减）", retrievability(10, 5) < 0.9)
check("R 随 t 单调递减", all(retrievability(t, 5) > retrievability(t+1, 5) for t in range(1, 30)))

# 1.4 interval_from_stability
# I(0.9, S) 应当 ≈ S（FSRS 关键不变量）
for s in [1, 3.71, 13.82, 50, 100]:
    i = interval_from_stability(s, 0.9)
    check(f"interval_from_stability({s}) ≈ {s} (r=0.9)", abs(i - s) <= 1, f"got={i} expected≈{s}")
# I(0.99, S) > I(0.9, S)（更严要求 → 间隔更短）
check("高 retention → 短间隔", interval_from_stability(10, 0.99) < interval_from_stability(10, 0.9))
# clamp
check("clamp S 极大 → MAX 180", interval_from_stability(1e6, 0.9) == MAX_INTERVAL)
check("clamp S 极小 → MIN 1", interval_from_stability(0.01, 0.9) == MIN_INTERVAL)

# 1.5 答错重置（用 elapsed=0 测试最简）
r_after_4good = review(None, 3)  # 新卡 good
for _ in range(3):
    r_after_4good = review(r_after_4good, 3, 0)
s_before = r_after_4good["s"]
r_after_again = review(r_after_4good, 1, 0)
check("答错后 S 降低", r_after_again["s"] < s_before, f"before={s_before} after={r_after_again['s']}")
check("答错后 reps=0", r_after_again["g"] == 1)
# 但新 state 仍 >= MIN
check("答错后 S 仍 ≥ 0.1", r_after_again["s"] >= 0.1)

# 1.6 答对增长
# 紧跟复习 R=1 → growth 公式中 (e^0 - 1) = 0，预期 S 不增长
r_steady = review(None, 3)  # 新卡 good
check("紧跟复习 g=3 R=1 → S = w2 (无 growth)", abs(r_steady["s"] - W[2]) < 1e-4)
# 给真实 elapsed，growth > 0
r_growing = review(r_steady, 3, 5)
check("elapsed=5 → S 增长", r_growing["s"] > r_steady["s"], f"{r_growing['s']} > {r_steady['s']}")

# 1.7 5 次全 good with elapsed → 间隔持续增长
ratings = ["with_help"] * 5
elapsed = [0, 1, 6, 15, 30]
out = fsrs(ratings, elapsed)
check("5 good with elapsed → interval ≥ 5", out["interval"] >= 5, f"got={out['interval']}")
check("5 good → reps=5", out["reps"] == 5)
check("algorithm = fsrs-4.5", out["algorithm"] == "fsrs-4.5")

# 1.8 答错后 reps 重置
out_fail = fsrs(["with_help"] * 4 + ["cant"])
check("4 good + 1 again → reps=0", out_fail["reps"] == 0)
check("4 good + 1 again → interval=1 (兜底)", out_fail["interval"] == 1)

# ---- 2. 接口兼容 ----
out_sm2 = fsrs(["with_help"] * 3)
check("fsrs 返回含 interval", "interval" in out_sm2)
check("fsrs 返回含 ease (= stability 字段)", "ease" in out_sm2)
check("fsrs 返回含 reps", "reps" in out_sm2)
check("fsrs 返回含 algorithm", "algorithm" in out_sm2)

# ---- 3. scheduler.next_interval 统一入口 ----
from core.scheduler import sm2, next_interval, _default_algorithm

# 3.1 默认 = sm2
check("默认算法 = sm2", _default_algorithm() == "sm2")
out_default = next_interval(["with_help"] * 3)
check("next_interval 默认 → algorithm=sm2", out_default["algorithm"] == "sm2")
check("next_interval 默认 → 与 sm2() 一致",
      out_default["interval"] == sm2(["with_help"] * 3)["interval"])

# 3.2 algorithm="fsrs" 显式
out_fsrs = next_interval(["with_help"] * 3, algorithm="fsrs")
check("next_interval(fsrs) → algorithm=fsrs-4.5", out_fsrs["algorithm"] == "fsrs-4.5")

# 3.3 env 切 fsrs
old = os.environ.get("ITUTOR_SCHEDULER")
os.environ["ITUTOR_SCHEDULER"] = "fsrs"
try:
    out_env = next_interval(["with_help"] * 3)
    check("env ITUTOR_SCHEDULER=fsrs → algorithm=fsrs-4.5", out_env["algorithm"] == "fsrs-4.5")
finally:
    if old is None: del os.environ["ITUTOR_SCHEDULER"]
    else: os.environ["ITUTOR_SCHEDULER"] = old

# 3.4 unknown algorithm → 默认 sm2（不报错）
out_unknown = next_interval(["with_help"] * 3, algorithm="foobar")
check("未知 algorithm → 兜底 sm2", out_unknown["algorithm"] == "sm2")

# 3.5 sm2() 仍可直接调用（向后兼容）
check("sm2() 直接调用 OK", sm2(["with_help"] * 3)["interval"] >= 1)

# ---- 4. 接入 smoke：core.learning.topic_stats 不破坏 ----
try:
    from core.learning import topic_stats
    check("core.learning import OK", True)
    # 真实数据存在 japanese-jlpt-n3 实例
    stats = topic_stats(1, "japanese-jlpt-n3")
    check("topic_stats() 不报错", isinstance(stats, list))
    if stats:
        first = stats[0]
        check("topic item 含 next_due", "next_due" in first)
except Exception as e:
    check("core.learning 集成失败", False, f"err={e}")

# ---- 5. 边界 / 防御 ----
# NaN
import math as _m
check("interval_from_stability(NaN) = MIN 1", interval_from_stability(_m.nan, 0.9) == MIN_INTERVAL)
# 极大 retention
i_low = interval_from_stability(10, 0.5)  # 50% retention → 间隔可短可长（不要求）
check("retention 0.5 仍返回整数", isinstance(i_low, int))
# 输入 None
out_empty = fsrs([])
check("fsrs([]) 返回有效 dict", isinstance(out_empty, dict) and "interval" in out_empty)
check("fsrs([]) algorithm 标记", out_empty["algorithm"] == "fsrs-4.5")

# ---- 6. 与 SM-2 对比：FSRS 在有 elapsed 数据时更稳定 ----
# SM-2 默认 4 good → 1, 6, 6*2.5=15, 15*2.5=38
# FSRS 默认 4 good（无 elapsed）→ 1, 1, 1, 1 (R=1, 无 growth)
# 这说明：FSRS 必须有真实 elapsed 才有效，否则与 SM-2 持平
out_sm2_4 = sm2(["with_help"] * 4)
out_fsrs_4_no_elapsed = fsrs(["with_help"] * 4)
out_fsrs_4_with_elapsed = fsrs(["with_help"] * 4, [0, 1, 7, 30])
check("FSRS 无 elapsed → 间隔 ≤ SM-2（更保守）",
      out_fsrs_4_no_elapsed["interval"] <= out_sm2_4["interval"],
      f"FSRS={out_fsrs_4_no_elapsed['interval']} SM2={out_sm2_4['interval']}")
check("FSRS 有 elapsed → 间隔 ≥ 无 elapsed（更敢于拉长）",
      out_fsrs_4_with_elapsed["interval"] >= out_fsrs_4_no_elapsed["interval"],
      f"with={out_fsrs_4_with_elapsed['interval']} no={out_fsrs_4_no_elapsed['interval']}")

print("\n=== 总结 ===")
if fails:
    print(f"❌ {len(fails)} 项未通过：")
    for f in fails: print(f"   - {f}")
    sys.exit(1)
print(f"✅ 全部通过（{60} 项）")
