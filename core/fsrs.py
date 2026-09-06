"""FSRS-4.5 间隔重复调度（DSR 模型 · 17 权重默认 · Batch 2E）。

替代 SM-2 的"次数×ease factor"启发式，引入：
- D（Difficulty，难度，1-10）：单卡的内在难度
- S（Stability，稳定性，天数）：在 R=90% 时对应的间隔
- R（Retrievability，检索率，0-1）：在 t 天后还能回忆起的概率

按 [open-spaced-repetition/awesome-fsrs](https://github.com/open-spaced-repetition/awesome-fsrs/wiki/The-Algorithm)
FSRS-4.5 实现（17 权重），与现有 SM-2 输入接口兼容：
- 输入：ratings = ["cant" | "with_help" | "independent" | None, ...]
- 输出：{interval, ease, reps, d, s, algorithm}

注意：本项目用户场景下"自评历史"的 elapsed_days 不可知，回放时默认 elapsed=0
（视为紧跟复习），与 SM-2 默认行为类似；真实生产应从 events 表算出 elapsed_days。
如需启用，可调 `fsrs(ratings, elapsed_days_list)` 传真实天数。
"""

from __future__ import annotations

import math
import os
from typing import Optional

# FSRS-4.5 默认权重（open-spaced-repetition/awesome-fsrs 推荐）
W = [0.4872, 1.4003, 3.7145, 13.8206, 5.1618, 1.2298, 0.8975,
     0.031, 1.6474, 0.1367, 1.0461, 2.1072, 0.0793, 0.3246,
     1.587, 0.2272, 2.8755]

# FSRS-4.5 公式常数
DECAY = -0.5
FACTOR = 19 / 81   # 0.234567901...
DEFAULT_RETENTION = 0.9
MAX_INTERVAL = 180
MIN_INTERVAL = 1

# LearnBuddy 3 档自评 → FSRS 4 档评分
# - cant        → 1 (again)   完全不会
# - with_help   → 3 (good)    提示后能想
# - independent → 4 (easy)    完全独立
_GRADE_MAP = {"cant": 1, "with_help": 3, "independent": 4}
_DEFAULT_GRADE = 3  # with_help 兜底

# 内部常量：算法识别
ALG_NAME = "fsrs-4.5"


def grade(self_rating: Optional[str]) -> int:
    """LearnBuddy 自评字符串 → FSRS 评分 1-4。"""
    return _GRADE_MAP.get(self_rating or "with_help", _DEFAULT_GRADE)


def initial_state(g: int) -> dict:
    """首次评分 g∈[1,4] → 初始 {d, s}。
    - S0(G) = w[G-1]
    - D0(G) = w4 - (G-3)·w5      # FSRS v4 线性公式（与 17 权重版 FSRS-4.5 匹配）
    """
    g = max(1, min(4, g))
    s = W[g - 1]
    d = W[4] - (g - 3) * W[5]
    d = max(1.0, min(10.0, d))
    return {"d": d, "s": s}


def retrievability(t_days: float, s: float) -> float:
    """t 天后的检索率。R(t, S) = (1 + FACTOR·t/S)^DECAY"""
    if s <= 0:
        return 1.0
    if t_days <= 0:
        return 1.0
    return (1.0 + FACTOR * (t_days / s)) ** DECAY


def interval_from_stability(s: float, desired_retention: float = DEFAULT_RETENTION) -> int:
    """由稳定性 s 反推下次间隔：I(r, S) = (S/FACTOR) · (r^(1/DECAY) - 1)
    对 r=0.9, S 任意 → I = S（与 SM-2 的"答得好 → 间隔长"语义一致）。"""
    r = max(0.5, min(0.99, desired_retention))
    i = (s / FACTOR) * (r ** (1.0 / DECAY) - 1.0)
    if i != i or i < 0:  # NaN / 负数兜底
        return MIN_INTERVAL
    return max(MIN_INTERVAL, min(MAX_INTERVAL, round(i)))


def review(prev: Optional[dict], g: int, elapsed_days: float = 0.0) -> dict:
    """一次 review。
    prev: 上一 state {d, s}，None 表示新卡
    g: 评分 1-4
    elapsed_days: 距上次复习的天数（默认 0 = 视为紧跟复习）
    返回新 state + 下次间隔。
    """
    g = max(1, min(4, g))
    if prev is None:
        st = initial_state(g)
        return {"d": st["d"], "s": st["s"], "interval": MIN_INTERVAL, "g": g}

    d, s = prev["d"], prev["s"]
    r = retrievability(elapsed_days, s)

    if g == 1:
        # 遗忘：S_new = w11 · D^(-w12) · ((S+1)^w13 - 1) · exp(w14·(1-R))
        new_s = (W[11] * (d ** (-W[12]))
                 * ((s + 1) ** W[13] - 1.0)
                 * math.exp(W[14] * (1.0 - r)))
        new_s = max(0.1, new_s)
        # D 失败：mean-revert 到 D0(3) + 微增
        d0_3 = initial_state(3)["d"]
        d_prime = d + 1.0
        new_d = W[7] * d0_3 + (1 - W[7]) * d_prime
        new_d = max(1.0, min(10.0, new_d))
    else:
        # 成功：D' = D - w6·(G-3); D_new = w7·D0(3) + (1-w7)·D'
        d_prime = d - W[6] * (g - 3)
        d0_3 = initial_state(3)["d"]
        new_d = W[7] * d0_3 + (1 - W[7]) * d_prime
        new_d = max(1.0, min(10.0, new_d))
        # S growth: e^w8 · (11-D) · S^-w9 · (e^(w10·(1-R)) - 1) · h · b
        h = W[15] if g == 2 else 1.0
        b = W[16] if g == 4 else 1.0
        try:
            growth = (math.exp(W[8]) * (11 - new_d) * (s ** (-W[9]))
                      * (math.exp(W[10] * (1.0 - r)) - 1.0) * h * b)
        except (ValueError, OverflowError):
            growth = 0.0
        if growth < 0:  # clamping: 异常情形（S 极小 + R=1）
            growth = 0.0
        new_s = s * (1.0 + growth)
    new_s = max(0.1, new_s)
    return {
        "d": round(new_d, 2),
        "s": round(new_s, 4),
        "interval": interval_from_stability(new_s),
        "g": g,
    }


def fsrs(
    ratings: list[Optional[str]],
    elapsed_days_list: Optional[list[float]] = None,
) -> dict:
    """回放自评序列，输出 {interval, ease, reps, d, s, algorithm}。

    接口与 core.scheduler.sm2 兼容：input ratings（按时间正序）。
    elapsed_days_list 可选：第 i 项是第 i 次复习距"上次"的天数（i=0 通常为 0）。
    """
    state: Optional[dict] = None
    reps = 0
    if elapsed_days_list is None:
        elapsed_days_list = [0.0] * len(ratings)
    if not ratings:
        return {
            "interval": MIN_INTERVAL,
            "ease": round(W[2], 2),  # initial "good" stability
            "reps": 0,
            "d": 0.0, "s": 0.0,
            "algorithm": ALG_NAME,
        }
    for i, r in enumerate(ratings):
        g = grade(r)
        elapsed = float(elapsed_days_list[i] if i < len(elapsed_days_list) else 0)
        state = review(state, g, elapsed)
        if g >= 2:
            reps += 1
        else:
            reps = 0
    return {
        "interval": state["interval"] if state else MIN_INTERVAL,
        # 用 stability 字段替代 ease（对外保持 ease 字段名以兼容 SM-2 消费者）
        "ease": round(state["s"], 2) if state else W[2],
        "reps": reps,
        "d": state["d"] if state else 0.0,
        "s": state["s"] if state else 0.0,
        "algorithm": ALG_NAME,
    }


__all__ = [
    "fsrs", "review", "grade", "initial_state", "retrievability",
    "interval_from_stability", "W", "DEFAULT_RETENTION", "MAX_INTERVAL",
    "MIN_INTERVAL", "ALG_NAME",
]


# ============================================================================
# 内部自测（仅 import 触发；不参与生产）
# ============================================================================
if __name__ == "__main__":  # pragma: no cover
    # 一些手算样例
    print("FSRS-4.5 自测：")
    print(" 新卡 easy:", fsrs(["independent"]))                   # G=4, S=w3=13.82
    print(" 3 good:", fsrs(["with_help"] * 3))                    # 间隔递增
    print(" 4 good + 1 again:", fsrs(["with_help"] * 4 + ["cant"]))  # 答错重置
    print(" 5 easy:", fsrs(["independent"] * 5))
    # 给 elapsed_days 测试：
    print(" 4 good + 真实 elapsed [1,6,15,30]:",
          fsrs(["with_help"] * 4, [0, 1, 6, 15, 30]))
