#!/usr/bin/env python3
"""在部署机本地把一个已验证的 Cloud 账号初始化为管理员。"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from core.auth import verify_password  # noqa: E402
from core.db import get_conn, init_db  # noqa: E402
from core.runtime import runtime_edition  # noqa: E402


class AdminInitializationError(ValueError):
    """管理员初始化条件不满足。"""


def initialize_cloud_admin(email: str, password: str) -> dict:
    """验证目标账号密码后晋升；同一账号重复执行不会产生额外变更。"""
    if runtime_edition() != "cloud":
        raise AdminInitializationError("只允许在 LEARNBUDDY_EDITION=cloud 时初始化管理员")

    target_email = (email or "").strip().lower()
    if not target_email or not password:
        raise AdminInitializationError("必须显式提供目标邮箱并输入该账号当前密码")

    init_db()
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT id,email,name,password_hash,is_admin FROM users WHERE email = ?",
            (target_email,),
        ).fetchone()
        if row is None or not verify_password(password, row["password_hash"]):
            raise AdminInitializationError("目标账号不存在或密码不正确")

        changed = not bool(row["is_admin"])
        if changed:
            conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (row["id"],))
        conn.commit()
        return {
            "id": int(row["id"]),
            "email": row["email"],
            "name": row["name"] or "",
            "is_admin": True,
            "changed": changed,
        }
    except AdminInitializationError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="在部署机本地把一个已存在且密码验证通过的 Cloud 账号设为管理员"
    )
    parser.add_argument("email", help="要初始化的明确目标账号邮箱")
    args = parser.parse_args()
    password = getpass.getpass("目标账号当前密码：")

    try:
        result = initialize_cloud_admin(args.email, password)
    except AdminInitializationError as exc:
        print(f"初始化失败：{exc}。", file=sys.stderr)
        return 1

    if result["changed"]:
        print(f"✓ Cloud 管理员已初始化：{result['email']}")
    else:
        print(f"✓ 目标账号已经是 Cloud 管理员：{result['email']}")
    print("密码只用于本次本机校验，未写入命令行、环境变量或日志。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
