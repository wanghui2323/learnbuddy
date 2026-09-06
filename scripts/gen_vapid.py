"""一次性生成 VAPID 密钥对（Web Push 轨道 A 用）。

用法：python scripts/gen_vapid.py
把输出的三行写进 .env。依赖 cryptography（装 pywebpush 会自动带上）。
"""

from __future__ import annotations

import base64

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
except ImportError:
    raise SystemExit("缺 cryptography：pip install pywebpush  或  pip install cryptography")


def main() -> None:
    priv = ec.generate_private_key(ec.SECP256R1())

    # 私钥：DER 单行 base64（env 友好，无换行）
    priv_der = priv.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    priv_b64 = base64.b64encode(priv_der).decode()

    # 公钥：未压缩点（04 + 32x + 32y）→ Base64URL，前端 applicationServerKey 直接用
    pub_point = priv.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    pub_b64url = base64.urlsafe_b64encode(pub_point).rstrip(b"=").decode()

    print("# 把下面三行写进 .env：")
    print(f"ITUTOR_VAPID_PRIVATE_KEY={priv_b64}")
    print(f"ITUTOR_VAPID_PUBLIC_KEY={pub_b64url}")
    print("ITUTOR_VAPID_SUBJECT=mailto:you@example.com")
    print()
    print("# 多 worker 部署：web 进程设 ITUTOR_SCHEDULER_RUN=0，独立进程跑 scheduler。")


if __name__ == "__main__":
    main()
