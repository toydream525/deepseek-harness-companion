"""Create a reviewable public-source archive from an explicit allowlist.

This script never copies the development directory wholesale. It does not
create a Git repository or publish anything.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PROJECT_NAME = "deepseek-harness-companion"
RELEASE_VERSION = "v0.1.0"
MANAGER_MODULES = {
    "app_paths.py", "autostart.py", "backend.py", "build_exe.py",
    "button_feedback.py",
    "chat_client.py", "component_updates.py", "deploy_config.py",
    "dsh_integration.py", "dsh_service.py",
    "hf_downloads.py", "library_page.py", "lifecycle.py", "main.py",
    "model_capabilities.py", "model_catalog.py", "network_proxy.py",
    "parameter_store.py", "presets_page.py", "provider_page.py",
    "recipe_importer.py", "runtime_manager.py", "styles.py",
}
ASSETS = {
    "checkmark.svg", "chevron-down.svg", "companion.ico",
    "companion-icon.svg", "companion-icon.png", "companion-icon-16.png",
    "companion-icon-20.png", "companion-icon-24.png",
    "companion-icon-32.png", "companion-icon-48.png",
    "companion-icon-64.png", "companion-icon-128.png",
    "companion-icon-256.png", "companion-icon-error.png",
    "companion-icon-loading.png", "companion-icon-running.png",
}
GUIDES = {
    "从零配置指南.html", "从零配置指南.md", "进阶实验路线.md",
    "qwen38-recipe-example.json",
}
LICENSE_TEXTS = {
    "GPL-3.0.txt", "LGPL-3.0.txt", "PyInstaller-COPYING.txt",
    "Python-3.12.txt", "Inno-Setup-LICENSE.txt",
}
STATIC_FILES = {
    ".gitignore", "AGENTS.md", "LICENSE", "README.md", "README.en.md",
    "THIRD-PARTY-NOTICES.md", "requirements-build.txt",
    "requirements-vendor.txt", "docs/OPEN_SOURCE_RELEASE.md",
    "docs/images/app-preview.png", "tools/stage_public_source.py",
    "tools/build_installer.ps1", "installer/DSH-Companion.iss",
    "manager/DSH-Companion.spec", "manager/version_info.txt",
    "resources/component-update-manifest.json",
    "resources/dsh-install/package.json",
    "resources/dsh-install/package-lock.json",
    "resources/parameter-presets/preset-template-example.json",
    "resources/recipes/qwen38-original-v1.json",
    "scripts/deploy/modes.json", "scripts/deploy/Deploy.ps1",
    "scripts/deploy/Uncensored32K.cmd",
    "scripts/deploy/UncensoredDirect32K.cmd",
    "scripts/deploy/Original32K.cmd",
    "scripts/deploy/Writing8K-unverified.cmd",
}
TEXT_SUFFIXES = {".py", ".spec", ".json", ".md", ".html", ".svg", ".txt",
                 ".ps1", ".cmd", ".iss"}
SECRET_MARKERS = {
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "api-token": re.compile(r"(?:sk-[A-Za-z0-9_-]{20,}|hf_[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    "local-user-path": re.compile(r"[A-Za-z]:[/\\](?:Users|用户)[/\\]", re.I),
    "development-drive": re.compile(r"[A-Za-z]:[/\\]AI[/\\]", re.I),
    "original-attachment-fingerprint": re.compile("source_prompt_" + "sha256"),
}
FORBIDDEN_NAMES = {
    "config.json", "config.backup.json", "network-proxy.json",
    "model-library.json", "model-library.backup.json",
    "parameter-presets.json", "parameter-presets.backup.json",
    "hf-download-tasks.json", "ui-owner.json", "runtime-manifest.json",
    "package-lock.json", ".env",
}
FORBIDDEN_SUFFIXES = {".exe", ".dll", ".pyd", ".pyc", ".pyo",
                      ".gguf", ".safetensors", ".pt", ".pth", ".onnx", ".log"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _expect_exact(directory: str, actual: set[str], expected: set[str]) -> None:
    if actual != expected:
        raise RuntimeError(
            f"Review {directory}: missing={sorted(expected - actual)}, "
            f"unreviewed={sorted(actual - expected)}"
        )


def public_files() -> list[Path]:
    _expect_exact("manager/*.py", {p.name for p in (ROOT / "manager").glob("*.py")},
                  MANAGER_MODULES)
    _expect_exact("manager/assets", {p.name for p in (ROOT / "manager/assets").iterdir()
                                     if p.is_file()}, ASSETS)
    _expect_exact("resources/guides", {p.name for p in (ROOT / "resources/guides").iterdir()
                                        if p.is_file()}, GUIDES)
    _expect_exact("release-licenses", {p.name for p in (ROOT / "release-licenses").iterdir()
                                       if p.is_file()}, LICENSE_TEXTS)
    _expect_exact("resources/dsh-install", {p.name for p in (ROOT / "resources/dsh-install").iterdir()
                                             if p.is_file()}, {"package.json", "package-lock.json"})
    _expect_exact("scripts/deploy", {p.name for p in (ROOT / "scripts/deploy").iterdir()
                                      if p.is_file()},
                  {"modes.json", "Deploy.ps1", "Uncensored32K.cmd",
                   "UncensoredDirect32K.cmd", "Original32K.cmd",
                   "Writing8K-unverified.cmd"})
    names = set(STATIC_FILES)
    names.update(f"manager/{name}" for name in MANAGER_MODULES)
    names.update(f"manager/assets/{name}" for name in ASSETS)
    names.update(f"resources/guides/{name}" for name in GUIDES)
    names.update(f"release-licenses/{name}" for name in LICENSE_TEXTS)
    paths = [ROOT / name for name in sorted(names)]
    for path in paths:
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(ROOT.resolve()):
            raise RuntimeError(f"Missing, linked, or external source file: {path}")
        approved_lock = path.relative_to(ROOT).as_posix() == "resources/dsh-install/package-lock.json"
        if (path.name.lower() in FORBIDDEN_NAMES and not approved_lock) or path.suffix.lower() in FORBIDDEN_SUFFIXES:
            raise RuntimeError(f"Forbidden source item: {path.relative_to(ROOT)}")
        if path.suffix.lower() in TEXT_SUFFIXES or path.name in {"LICENSE", ".gitignore"}:
            value = path.read_text(encoding="utf-8")
            normalized_paths = value.replace("\\\\", "\\")
            for kind, pattern in SECRET_MARKERS.items():
                if pattern.search(value) or (kind in {"local-user-path", "development-drive"}
                                             and pattern.search(normalized_paths)):
                    raise RuntimeError(f"{kind} in {path.relative_to(ROOT)}")
    return paths


def stage(destination: Path) -> None:
    paths = public_files()  # Check the full source set before creating outputs.
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(f"Choose a new empty output path: {destination}")
    destination.mkdir(parents=True)
    project = destination / PROJECT_NAME
    project.mkdir()
    manifest_lines = []
    for source in paths:
        relative = source.relative_to(ROOT)
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        manifest_lines.append(f"{sha256(target)}  {relative.as_posix()}")
    (project / "SOURCE-MANIFEST.sha256").write_text(
        "\n".join(manifest_lines) + "\n", encoding="utf-8"
    )
    archive = destination / f"{PROJECT_NAME}-{RELEASE_VERSION}-source.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6) as zf:
        for source in sorted(project.rglob("*")):
            if source.is_file():
                zf.write(source, source.relative_to(destination).as_posix())
    print(f"Source files: {len(paths)}")
    print(f"Source archive: {archive}")
    print(f"SHA-256: {sha256(archive)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path,
                        help="New output directory; must not exist yet")
    stage(parser.parse_args().destination)
