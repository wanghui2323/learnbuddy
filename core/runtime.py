"""LearnBuddy 运行版本与能力边界的唯一真相源。"""

from __future__ import annotations

import os
import sqlite3
from typing import Literal

from core.db import get_conn

Edition = Literal["cloud", "personal"]


class RuntimeConfigurationError(ValueError):
    """运行版本配置不可用。"""


def runtime_edition() -> Edition:
    """读取运行版本；默认 cloud 以兼容现有部署。"""
    value = (os.getenv("LEARNBUDDY_EDITION") or "cloud").strip().lower()
    if value not in {"cloud", "personal"}:
        raise RuntimeConfigurationError(
            "LEARNBUDDY_EDITION 必须是 cloud 或 personal，"
            f"当前值为 {value!r}"
        )
    return value  # type: ignore[return-value]


def user_count() -> int:
    """返回当前数据库用户数；建表前视为尚未初始化。"""
    conn = get_conn()
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        return int(row["n"] if row else 0)
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return 0
        raise
    finally:
        conn.close()


def _personal_owner_error(count: int) -> str:
    return (
        "Personal 版本只允许一个 owner，当前数据库存在"
        f" {count} 个用户。为防止跨账号数据暴露，业务访问已停止；"
        "请切回 LEARNBUDDY_EDITION=cloud，或先迁移到仅含目标 owner 的新数据库。"
    )


def assert_runtime_boundary() -> None:
    """发现 Personal 复用了多用户库时立即失败，不能只依赖 readiness。"""
    if runtime_edition() != "personal":
        return
    count = user_count()
    if count > 1:
        raise RuntimeConfigurationError(_personal_owner_error(count))


def runtime_capabilities() -> dict:
    """生成前后端共用的平铺能力合同，不暴露密钥。"""
    edition = runtime_edition()
    from core.llm_client import llm_runtime_status

    llm = llm_runtime_status()
    if edition == "cloud":
        result = {
            "edition": "cloud",
            "registration": "enabled",
            "quota_mode": "platform",
            "llm_credentials": "platform",
            "feishu": False,
            "openclaw": False,
            "backup_restore": False,
            "admin_console": True,
            "setup_required": False,
            "llm_configured": bool(llm.get("configured")),
        }
    else:
        count = user_count()
        result = {
            "edition": "personal",
            "registration": "setup_only" if count == 0 else "disabled",
            "quota_mode": "provider",
            "llm_credentials": "owner_managed",
            "feishu": True,
            "openclaw": True,
            "backup_restore": True,
            "admin_console": False,
            "setup_required": count == 0,
            "llm_configured": bool(llm.get("configured")),
            "user_count": count,
            "owner_count": count,
        }
        if count > 1:
            result["configuration_error"] = _personal_owner_error(count)

    links = {
        key: value
        for key, env_name in (
            ("github", "LEARNBUDDY_GITHUB_URL"),
            ("deploy", "LEARNBUDDY_DEPLOY_URL"),
        )
        if (value := (os.getenv(env_name) or "").strip())
    }
    if links:
        result["links"] = links
    return result


__all__ = [
    "Edition",
    "RuntimeConfigurationError",
    "assert_runtime_boundary",
    "runtime_capabilities",
    "runtime_edition",
    "user_count",
]
