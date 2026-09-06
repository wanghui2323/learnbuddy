"""Personal 发布脚本中的安全合同。"""

from __future__ import annotations

import importlib.util
import io
import tarfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent


def _load_owner_script():
    path = ROOT / "scripts/init_personal_owner.py"
    spec = importlib.util.spec_from_file_location("init_personal_owner", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_backup_script():
    path = ROOT / "scripts/verify_personal_backup.py"
    spec = importlib.util.spec_from_file_location("verify_personal_backup", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_archive(path: Path, *, linkname: str | None = None) -> None:
    with tarfile.open(path, "w:gz") as archive:
        if linkname is not None:
            info = tarfile.TarInfo("state/escape")
            info.type = tarfile.SYMTYPE
            info.linkname = linkname
            archive.addfile(info)
            return
        payload = b"learnbuddy"
        info = tarfile.TarInfo("itutor.db")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))


def _write_manifest(folder: Path, project: str = "learnbuddy") -> None:
    (folder / "manifest.txt").write_text(
        "format_version=1\n"
        "created_at=20260906T000000Z\n"
        f"compose_project={project}\n"
        "sensitive_data=yes\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://127.0.0.1:8000/", "http://127.0.0.1:8000"),
        ("http://localhost:8000", "http://localhost:8000"),
        ("http://[::1]:8000", "http://[::1]:8000"),
        ("https://learn.example.com/", "https://learn.example.com"),
    ],
)
def test_owner_initializer_accepts_https_or_exact_loopback(value, expected):
    assert _load_owner_script().validate_base_url(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "http://localhost.evil.example",
        "http://127.0.0.1.evil.example",
        "http://learn.example.com",
        "https://user:password@learn.example.com",
        "https://learn.example.com/base/path",
        "file:///tmp/learnbuddy",
    ],
)
def test_owner_initializer_rejects_lookalikes_and_unsafe_urls(value):
    with pytest.raises(ValueError):
        _load_owner_script().validate_base_url(value)


def test_backup_verifier_accepts_complete_safe_backup(tmp_path):
    verifier = _load_backup_script()
    _write_archive(tmp_path / "learnbuddy_data.tar.gz")
    _write_manifest(tmp_path)

    verifier.write_checksums(tmp_path)
    verifier.verify(tmp_path, "learnbuddy")


def test_backup_verifier_rejects_tampered_archive(tmp_path):
    verifier = _load_backup_script()
    archive = tmp_path / "learnbuddy_data.tar.gz"
    _write_archive(archive)
    _write_manifest(tmp_path)
    verifier.write_checksums(tmp_path)
    archive.write_bytes(archive.read_bytes() + b"tampered")

    with pytest.raises(ValueError, match="校验和不匹配"):
        verifier.verify(tmp_path, "learnbuddy")


def test_backup_verifier_rejects_project_mismatch(tmp_path):
    verifier = _load_backup_script()
    _write_archive(tmp_path / "learnbuddy_data.tar.gz")
    _write_manifest(tmp_path, "another-project")
    verifier.write_checksums(tmp_path)

    with pytest.raises(ValueError, match="备份属于 Compose project"):
        verifier.verify(tmp_path, "learnbuddy")


def test_backup_verifier_rejects_escaping_link(tmp_path):
    verifier = _load_backup_script()
    _write_archive(tmp_path / "learnbuddy_data.tar.gz", linkname="../../../outside")
    _write_manifest(tmp_path, "another-project")
    verifier.write_checksums(tmp_path)

    with pytest.raises(ValueError, match="不安全路径"):
        verifier.verify(tmp_path, "learnbuddy", allow_mismatch=True)
