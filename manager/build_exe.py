# -*- coding: utf-8 -*-
"""Build an isolated Windows onedir release and a shareable ZIP."""

from __future__ import annotations

import hashlib
import importlib.util
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MANAGER_DIR = BASE_DIR / "manager"
BUILD_DIR = MANAGER_DIR / "build"
DIST_DIR = BUILD_DIR / "dist"
RELEASE_DIR = BASE_DIR / "release"
STAGED = DIST_DIR / "DSH-Companion"
PUBLISH = RELEASE_DIR / "DSH-Companion"
ZIP_PATH = RELEASE_DIR / "DSH-Companion-v0.2.0-windows-x64.zip"
FINAL_EXE = BASE_DIR / "DSH-Companion.exe"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _clean_publish() -> None:
    if PUBLISH.exists():
        if PUBLISH.resolve().parent != RELEASE_DIR.resolve():
            raise RuntimeError("Release target escaped the release directory")
        shutil.rmtree(PUBLISH)


def _assert_direct_child(path: Path, parent: Path) -> None:
    if path.resolve().parent != parent.resolve():
        raise RuntimeError(f"Unexpected target outside {parent}: {path}")


def _assert_clean_release() -> None:
    forbidden = {"config.json", "config.backup.json", "network-proxy.json",
                 "model-library.json", "parameter-presets.json", "hf-download-tasks.json",
                 "runtime-manifest.json"}
    allowed_install_manifests = {
        "_internal/resources/dsh-install/package.json",
        "_internal/resources/dsh-install/package-lock.json",
    }
    for path in PUBLISH.rglob("*"):
        relative = path.relative_to(PUBLISH).as_posix().lower()
        if (path.name.lower() in forbidden or
                (path.name.lower() == "package-lock.json" and relative not in allowed_install_manifests) or
                path.suffix.lower() in {".pyc", ".pyo"} or
                any(part.lower() in {"runtime", ".venv", "__pycache__"}
                    for part in path.parts) or
                ("dsh-install" in (part.lower() for part in path.parts) and
                 path.is_file() and relative not in allowed_install_manifests) or
                "_vendor/bin" in path.as_posix().lower()):
            raise RuntimeError(f"Personal or third-party item in release: {path}")
    if not (PUBLISH / "DSH-Companion.exe").is_file():
        raise RuntimeError("Release entry point missing")


def _zip_release() -> None:
    pending = ZIP_PATH.with_suffix(".pending.zip")
    with zipfile.ZipFile(pending, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(PUBLISH.rglob("*")):
            if path.is_file():
                archive.write(path, Path("DSH-Companion") / path.relative_to(PUBLISH))
    pending.replace(ZIP_PATH)


def build(*, install_local: bool = False) -> int:
    for required in ("PyInstaller", "PySide6"):
        if importlib.util.find_spec(required) is None:
            raise RuntimeError(f"Build Python is missing {required}: {sys.executable}")
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "PyInstaller", "--distpath", str(DIST_DIR),
               "--workpath", str(BUILD_DIR / "pyinstaller"), "--clean", "--noconfirm",
               str(MANAGER_DIR / "DSH-Companion.spec")]
    result = subprocess.run(command, cwd=BASE_DIR)
    if result.returncode:
        return result.returncode
    if not (STAGED / "DSH-Companion.exe").is_file():
        raise FileNotFoundError(STAGED / "DSH-Companion.exe")
    _clean_publish()
    shutil.copytree(STAGED, PUBLISH)
    for name in ("README.md", "README.en.md", "LICENSE", "THIRD-PARTY-NOTICES.md"):
        source = BASE_DIR / name
        if source.is_file():
            shutil.copy2(source, PUBLISH / name)
    license_dir = BASE_DIR / "release-licenses"
    if license_dir.is_dir():
        shutil.copytree(license_dir, PUBLISH / "licenses")
    deploy_scripts = BASE_DIR / "scripts" / "deploy"
    if deploy_scripts.is_dir():
        shutil.copytree(deploy_scripts, PUBLISH / "scripts" / "deploy")
    _assert_clean_release()
    _zip_release()
    packaged_hash = sha256(PUBLISH / "DSH-Companion.exe")
    print(f"发布目录: {PUBLISH}\nZIP: {ZIP_PATH}\nEXE SHA256: {packaged_hash}\nZIP SHA256: {sha256(ZIP_PATH)}")
    if install_local:
        backup_dir = BASE_DIR / "backups" / ("before-companion-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
        if FINAL_EXE.exists() or (BASE_DIR / "_internal").exists():
            backup_dir.mkdir(parents=True, exist_ok=True)
            if FINAL_EXE.exists():
                shutil.copy2(FINAL_EXE, backup_dir / FINAL_EXE.name)
            if (BASE_DIR / "_internal").exists():
                shutil.copytree(BASE_DIR / "_internal", backup_dir / "_internal")
        pending_exe = BASE_DIR / "DSH-Companion.pending.exe"
        pending_internal = BASE_DIR / "_internal.pending"
        if pending_internal.exists():
            _assert_direct_child(pending_internal, BASE_DIR)
            shutil.rmtree(pending_internal)
        shutil.copytree(PUBLISH / "_internal", pending_internal)
        shutil.copy2(PUBLISH / FINAL_EXE.name, pending_exe)
        old_internal = BASE_DIR / "_internal"
        if old_internal.exists():
            _assert_direct_child(old_internal, BASE_DIR)
            shutil.rmtree(old_internal)
        pending_internal.replace(old_internal)
        pending_exe.replace(FINAL_EXE)
        print(f"本机入口: {FINAL_EXE}; 旧版备份: {backup_dir if backup_dir.exists() else '无'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(build(install_local="--install-local" in sys.argv[1:]))
