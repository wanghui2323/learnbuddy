"""极简 LLM Tracing（Batch L · 环 B 的"眼睛"）。

记录每次"生成型" LLM 调用的 延迟 / 成败 / 是否走兜底，补 usage 表（只在成功时记
token）缺的质量维度。归属 (user_id / scene / instance) 复用 usage 的 contextvars——
server 在 `with usage_context(...)` 内调用生成函数即可，无需改这些函数的签名。

设计同 usage 观察者：默认关闭（单测不写库），server 启动时 enable() 打开；任何采集异常
都吞掉，绝不影响主流程；生成函数在兜底分支调用 mark_fallback() 标记本次降级。
"""

from __future__ import annotations

import contextvars
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator, Optional

from core.db import get_conn
from core.usage import current_instance, current_scene, current_user_id

_enabled = False
_ctx_fallback: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "itutor_trace_fallback", default=False
)


def enable() -> None:
    """打开 tracing（server 启动时调用）。"""
    global _enabled
    _enabled = True


def is_enabled() -> bool:
    return _enabled


def mark_fallback() -> None:
    """在生成函数的兜底分支调用：标记本次调用走了兜底/降级。"""
    try:
        _ctx_fallback.set(True)
    except Exception:  # noqa: BLE001
        pass


def _record(*, user_id, instance_id, scene, ok, fallback, latency_ms, error) -> None:
    try:
        conn = get_conn()
        try:
            conn.execute(
                """INSERT INTO llm_traces
                     (user_id, instance_id, scene, ok, fallback, latency_ms, error, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    user_id,
                    instance_id,
                    scene,
                    1 if ok else 0,
                    1 if fallback else 0,
                    int(latency_ms),
                    ((error or "")[:300] or None),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — 采集失败绝不影响主流程
        pass


@contextmanager
def trace_llm(scene: Optional[str] = None, instance_id: Optional[str] = None) -> Iterator[None]:
    """包住一次生成型 LLM 调用，出口落一条 trace（延迟/成败/兜底）。

    scene/instance 不传则取 usage contextvars 的当前值。
    """
    if not _enabled:
        yield
        return
    token = _ctx_fallback.set(False)
    start = time.monotonic()
    ok, err = True, None
    try:
        yield
    except Exception as e:  # noqa: BLE001
        ok, err = False, f"{type(e).__name__}: {e}"
        raise
    finally:
        latency = (time.monotonic() - start) * 1000
        _record(
            user_id=current_user_id(),
            instance_id=instance_id or current_instance(),
            scene=scene or current_scene(),
            ok=ok,
            fallback=_ctx_fallback.get(),
            latency_ms=latency,
            error=err,
        )
        try:
            _ctx_fallback.reset(token)
        except Exception:  # noqa: BLE001
            pass


__all__ = ["enable", "is_enabled", "mark_fallback", "trace_llm"]
