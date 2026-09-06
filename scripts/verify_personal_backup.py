#!/usr/bin/env python3
"""为 LearnBuddy Personal 备份生成校验和，并在覆盖卷前完成安全预检。"""

from __future__ import annotations

import argparse
import hashlib
import posixpath
import re
import tarfile
from pathlib import Path, PurePosixPath


ARCHIVES = {
    "learnbuddy_data.tar.gz",
    "openclaw_state.tar.gz",
    "openclaw_workspace.tar.gz",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _archives(folder: Path) -> list[Path]:
    paths = sorted(path for path in folder.glob("*.tar.gz") if path.name in ARCHIVES)
    if not any(path.name == "learnbuddy_data.tar.gz" for path in paths):
        raise ValueError("备份缺少主数据卷 learnbuddy_data.tar.gz")
    return paths


def write_checksums(folder: Path) -> None:
    paths = _archives(folder)
    lines = [f"{_digest(path)}  {path.name}" for path in paths]
    (folder / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _parse_manifest(folder: Path, project: str, allow_mismatch: bool) -> None:
    path = folder / "manifest.txt"
    if not path.is_file():
        raise ValueError("备份缺少 manifest.txt")
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip()
    if values.get("format_version") != "1":
        raise ValueError("不支持的备份格式版本")
    source_project = values.get("compose_project")
    if not source_project:
        raise ValueError("manifest 缺少 compose_project")
    if source_project != project and not allow_mismatch:
        raise ValueError(
            f"备份属于 Compose project {source_project!r}，当前为 {project!r}；"
            "确认跨项目恢复后设置 LEARNBUDDY_RESTORE_ALLOW_PROJECT_MISMATCH=1"
        )


def _verify_checksums(folder: Path, paths: list[Path]) -> None:
    checksum_file = folder / "SHA256SUMS"
    if not checksum_file.is_file():
        raise ValueError("备份缺少 SHA256SUMS")
    expected = {}
    for line in checksum_file.read_text(encoding="utf-8").splitlines():
        digest, sep, filename = line.partition("  ")
        if not sep or not SHA256_RE.fullmatch(digest) or filename not in ARCHIVES:
            raise ValueError("SHA256SUMS 格式或文件名不合法")
        expected[filename] = digest
    for path in paths:
        if expected.get(path.name) != _digest(path):
            raise ValueError(f"校验和不匹配：{path.name}")


def _safe_member(member: tarfile.TarInfo) -> bool:
    name = member.name.replace("\\", "/")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or member.isdev() or member.isfifo():
        return False
    if member.issym() or member.islnk():
        target = member.linkname.replace("\\", "/")
        if PurePosixPath(target).is_absolute():
            return False
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
        if resolved == ".." or resolved.startswith("../"):
            return False
    return True


def _verify_tar(path: Path) -> None:
    try:
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()
            if not members:
                raise ValueError(f"空备份包：{path.name}")
            unsafe = next((member.name for member in members if not _safe_member(member)), None)
            if unsafe:
                raise ValueError(f"备份含不安全路径或设备节点：{path.name}:{unsafe}")
    except (tarfile.TarError, OSError) as exc:
        raise ValueError(f"无法完整读取备份包 {path.name}") from exc


def verify(folder: Path, project: str, allow_mismatch: bool = False) -> None:
    paths = _archives(folder)
    _parse_manifest(folder, project, allow_mismatch)
    _verify_checksums(folder, paths)
    for path in paths:
        _verify_tar(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    checksum = subparsers.add_parser("checksum")
    checksum.add_argument("folder", type=Path)
    check = subparsers.add_parser("verify")
    check.add_argument("folder", type=Path)
    check.add_argument("--project", required=True)
    check.add_argument("--allow-project-mismatch", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "checksum":
            write_checksums(args.folder.resolve())
        else:
            verify(args.folder.resolve(), args.project, args.allow_project_mismatch)
    except ValueError as exc:
        parser.error(str(exc))
    print("✓ 备份校验通过" if args.command == "verify" else "✓ 已生成 SHA256SUMS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
