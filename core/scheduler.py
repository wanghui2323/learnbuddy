"""SM-2 + FSRS 双调度（Batch 2E · V0.25）。

提供单一入口 `next_interval(ratings, elapsed_days_list=None, algorithm=None)`：
- algorithm="sm2"  → SM-2（Anki 早期，3 档自评，简单可靠，默认）
- algorithm="fsrs" → FSRS-4.5（DSR 模型，17 权重默认，10% 更准的间隔）
- algorithm=None   → 按环境变量 ITUTOR_SCHEDULER 选，缺省 "sm2"

环境变量：
- ITUTOR_SCHEDULER=sm2|fsrs     默认调度算法
- ITUTOR_RETENTION=0.9          FSRS 期望回忆率（默认 0.9）

接口兼容：两个算法都返回 {interval, ease, reps, ...} 字典，
旧代码 (core.learning.py) 用 `sm2(ratings)` 直接调用不会被破坏。
"""

from __future__ import annotations

import os
from typing import Optional

EF_START = 2.5     # 初始难度系数
EF_MIN = 1.3       # 难度系数下限
MAX_INTERVAL = 180  # 间隔封顶（天）

# 自评 → SM-2 质量分 q(0–5)；q<3 视为未通过 → 重学
_GRADE = {"cant": 2, "with_help": 3, "independent": 5}


def grade(self_rating: Optional[str]) -> int:
    return _GRADE.get(self_rating or "with_help", 3)


def sm2(ratings: list[Optional[str]]) -> dict:
    """按时间正序回放自评序列，返回 {interval, ease, reps}。

    interval：距上次复习的下次间隔天数（≥1）；ease：当前难度系数；reps：连续通过次数。
    """
    ef = EF_START
    reps = 0
    interval = 0
    for r in ratings:
        q = grade(r)
        if q < 3:  # 没通过 → 重置，明天再来
            reps = 0
            interval = 1
        else:
            if reps == 0:
                interval = 1
            elif reps == 1:
                interval = 6
            else:
                interval = round(interval * ef)
            reps += 1
        ef = ef + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))
        ef = max(EF_MIN, ef)
        interval = min(interval, MAX_INTERVAL)
    return {"interval": max(1, interval), "ease": round(ef, 2), "reps": reps}


# ============================================================================
# 统一入口：next_interval()
# ============================================================================

def _default_algorithm() -> str:
    return (os.getenv("ITUTOR_SCHEDULER") or "sm2").strip().lower()


def next_interval(
    ratings: list[Optional[str]],
    elapsed_days_list: Optional[list[float]] = None,
    algorithm: Optional[str] = None,
) -> dict:
    """统一调度入口。

    行为：
    - algorithm ∈ {"sm2", "fsrs", None}  None → env ITUTOR_SCHEDULER，缺省 sm2
    - 始终返回 {interval, ease, reps, algorithm} 字典

    elapsed_days_list 仅 FSRS 使用（按 i 索引，第 0 项通常为 0）。
    """
    algo = (algorithm or _default_algorithm()).strip().lower()
    if algo == "fsrs":
        from core.fsrs import fsrs
        out = fsrs(ratings, elapsed_days_list)
        out.setdefault("algorithm", "fsrs-4.5")
        return out
    # 默认 SM-2（向后兼容）
    out = sm2(ratings)
    out["algorithm"] = "sm2"
    return out


__all__ = [
    "sm2", "grade", "next_interval",
    "EF_START", "EF_MIN", "MAX_INTERVAL",
]
