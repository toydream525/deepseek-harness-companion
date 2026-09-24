"""Opt-in, pinned component updates in separate, replaceable directories.

Only the application's selected path changes at activation. External installations,
model weights, DSH profiles, credentials and running processes are never modified.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from uuid import uuid4

from app_paths import DATA_ROOT, INSTALL_ROOT


RESOURCE_ROOT = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else INSTALL_ROOT
MANIFEST = RESOURCE_ROOT / "resources" / "component-update-manifest.json"
DSH_LOCK_DIR = RESOURCE_ROOT / "resources" / "dsh-install"
VERSIONS = DATA_ROOT / "runtime" / "versions"
JOURNAL = DATA_ROOT / "runtime" / "component-updates.json"
_LOCK_PATH = DATA_ROOT / "runtime" / "component-updates.lock"
_THREAD_LOCK = threading.RLock()
_ID = re.compile(r"[0-9a-f]{32}\Z")
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _result(ok: bool, message: str, code: str, **data) -> dict:
    return {"success": ok, "message": message, "code": code, "data": data}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree_sha256(directory: Path) -> str:
    """Bind a prepared tree to activation without recording user state."""
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*")):
        if path == directory / ".component-update.json":
            continue
        if path.is_symlink():
            raise ValueError("prepared tree contains a link")
        if not path.is_file():
            continue
        relative = path.relative_to(directory).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(_sha256(path)))
    return digest.hexdigest()


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(value, out, ensure_ascii=False, indent=2)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _manifest() -> dict:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("unsupported update manifest")
    return data


def _journal() -> dict:
    try:
        data = json.loads(JOURNAL.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


@contextmanager
def _change_lock():
    """Cross-process lock for activation and rollback, also on Windows."""
    with _THREAD_LOCK:
        _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK_PATH.open("a+b") as stream:
            if os.name == "nt":
                import msvcrt
                stream.seek(0)
                if stream.read(1) == b"":
                    stream.write(b"0")
                    stream.flush()
                deadline = time.monotonic() + 10
                while True:
                    try:
                        stream.seek(0)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("component update is busy")
                        time.sleep(0.1)
            try:
                yield
            finally:
                if os.name == "nt":
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


@contextmanager
def _lifecycle_lock(component: str):
    if component == "engine":
        import backend
        with backend._lifecycle_guard():
            yield
        return
    from dsh_service import _kernel
    mutex = _kernel.CreateMutexW(None, False, "Local\\DeepSeekHarness-3080-launch")
    if not mutex:
        raise OSError("DSH lifecycle mutex unavailable")
    acquired = False
    try:
        acquired = _kernel.WaitForSingleObject(mutex, 30000) in (0, 0x80)
        if not acquired:
            raise TimeoutError("DSH lifecycle mutex busy")
        yield
    finally:
        if acquired:
            _kernel.ReleaseMutex(mutex)
        _kernel.CloseHandle(mutex)


def _managed_dir(component: str, prepared_id: str) -> Path | None:
    if component not in {"engine", "dsh"} or not isinstance(prepared_id, str) or not _ID.fullmatch(prepared_id):
        return None
    base = (VERSIONS / component).resolve()
    candidate = base / prepared_id
    return candidate if candidate.is_dir() and candidate.resolve().parent == base else None


def _metadata(directory: Path, component: str) -> dict | None:
    try:
        data = json.loads((directory / ".component-update.json").read_text(encoding="utf-8"))
        entry = data.get("entry", "")
        if (data.get("component") != component or not isinstance(entry, str)
                or Path(entry).is_absolute() or ".." in Path(entry).parts):
            return None
        target = (directory / entry).resolve()
        if not target.is_file() or not target.is_relative_to(directory.resolve()):
            return None
        if data.get("tree_sha256") != _tree_sha256(directory):
            return None
        return data
    except (OSError, ValueError, TypeError):
        return None


def _extract_zip(archive: Path, destination: Path, seen: set[str]) -> None:
    with zipfile.ZipFile(archive) as zf:
        members = zf.infolist()
        if len(members) > 10000 or sum(x.file_size for x in members) > 3_000_000_000:
            raise ValueError("archive exceeds extraction limits")
        for info in members:
            name = info.filename.replace("\\", "/")
            parts = PurePosixPath(name).parts
            reserved = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                        *(f"lpt{i}" for i in range(1, 10))}
            if (not parts or name.startswith("/") or ":" in name or
                    any(part in {"", ".", ".."} or part.rstrip(" .") != part or
                        part.split(".")[0].casefold() in reserved for part in parts) or
                    (info.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError("unsafe archive path")
            target = destination.joinpath(*parts)
            if not target.resolve().is_relative_to(destination.resolve()):
                raise ValueError("archive path escapes staging directory")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            key = "/".join(parts).casefold()
            if key in seen or info.file_size > 1_000_000_000:
                raise ValueError("duplicate or oversized archive item")
            seen.add(key)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output, 1024 * 1024)


class ComponentUpdateService:
    """Use the UI's existing DSHIntegration instance to keep its path in sync."""

    def __init__(self, integration=None):
        self.integration = integration

    def _current(self, component: str) -> str:
        if component == "engine":
            import backend
            raw = backend.load_config().get("server_executable") or ""
            return str((INSTALL_ROOT / raw).resolve()) if raw else ""
        if self.integration is not None:
            return str(self.integration.dsh.root)
        from dsh_service import _discovered_dsh_root
        return str(_discovered_dsh_root()[0])

    def check_updates(self) -> dict:
        """Local manifest check only: target means reviewed, not newest online."""
        try:
            manifest = _manifest()
            import backend
            engine_path = self._current("engine")
            engine_version = backend.get_runtime_version() if engine_path else None
            dsh_path = Path(self._current("dsh"))
            from dsh_integration import DSHIntegration
            dsh_version = DSHIntegration._installation_details(dsh_path)[0]
            node_ready = bool(self.integration and self.integration._node_command()[1])
            return _result(True, "已读取内置已审核版本清单；不会自动下载安装。", "ready",
                engine={"current": engine_version, "path": engine_path,
                        "target": manifest["engine"]["version"],
                        "target_is_selected": bool(engine_version and
                                                   "build " + manifest["engine"]["version"][1:]
                                                   in engine_version),
                        "channels": [{"id": name, "assets": assets,
                                      "size_bytes": sum(item["size_bytes"] for item in assets)}
                                     for name, assets in manifest["engine"]["channels"].items()],
                        "managed": bool(engine_path and Path(engine_path).resolve().is_relative_to((VERSIONS / "engine").resolve())),
                        "release_url": manifest["engine"]["release_url"]},
                dsh={"current": dsh_version, "path": str(dsh_path),
                     "target": manifest["dsh"]["version"], "node_ready": node_ready,
                     "target_is_selected": dsh_version == manifest["dsh"]["version"],
                     "managed": dsh_path.resolve().is_relative_to((VERSIONS / "dsh").resolve()),
                     "project_url": manifest["dsh"]["project_url"]},
                node={"url": manifest["node_url"]}, drivers={"urls": manifest["driver_urls"]},
                note="目标版是随本应用审核固定的版本，可能并非上游最新版本。")
        except (OSError, ValueError, TypeError, KeyError):
            return _result(False, "内置更新清单不可用。", "manifest-invalid")

    def prepare(self, component: str, channel: str = "cpu") -> dict:
        """User-triggered download/install into a fresh managed version directory."""
        if component not in {"engine", "dsh"}:
            return _result(False, "未知组件。", "invalid-component")
        try:
            manifest = _manifest()
            if component == "engine" and channel not in manifest["engine"]["channels"]:
                return _result(False, "未知引擎构建类型。", "invalid-channel")
            target = VERSIONS / component
            target.mkdir(parents=True, exist_ok=True)
            prepared_id = uuid4().hex
            stage = target / (".pending-" + prepared_id)
            stage.mkdir()
            try:
                if component == "engine":
                    entry = self._prepare_engine(stage, manifest["engine"], channel)
                    version = manifest["engine"]["version"]
                else:
                    entry = self._prepare_dsh(stage, manifest["dsh"])
                    version = manifest["dsh"]["version"]
                _atomic_json(stage / ".component-update.json",
                             {"component": component, "version": version,
                              "channel": channel if component == "engine" else None,
                              "entry": entry.as_posix(),
                              "tree_sha256": _tree_sha256(stage)})
                final = target / prepared_id
                os.replace(stage, final)
                return _result(True, "新版本已准备并校验；旧版本及当前路径未改变。", "prepared",
                               component=component, version=version, channel=channel,
                               prepared_id=prepared_id, path=str(final))
            finally:
                if stage.exists() and stage.resolve().parent == target.resolve():
                    shutil.rmtree(stage)
        except Exception:
            return _result(False, "更新准备失败；当前设置和旧组件未改变。请检查网络、代理、磁盘空间或组件版本。",
                           "prepare-failed")

    def _prepare_engine(self, stage: Path, manifest: dict, channel: str) -> Path:
        import network_proxy
        import runtime_manager
        assets = manifest["channels"][channel]
        seen: set[str] = set()
        for item in assets:
            name = item["name"]
            if not re.fullmatch(r"[A-Za-z0-9._-]+\.zip", name):
                raise ValueError("invalid asset name")
            url = ("https://github.com/ggml-org/llama.cpp/releases/download/"
                   + manifest["version"] + "/" + name)
            temporary = stage / name
            digest = hashlib.sha256()
            size = 0
            with network_proxy.request_lease():
                with network_proxy.create_client(timeout=120) as client:
                    with client.stream("GET", url) as response:
                        response.raise_for_status()
                        final_url = response.url
                        host = (final_url.host or "").lower()
                        if final_url.scheme != "https" or not (host == "github.com" or
                                                               host.endswith(".githubusercontent.com")):
                            raise ValueError("release asset redirected outside GitHub")
                        with temporary.open("xb") as out:
                            for chunk in response.iter_bytes(1024 * 1024):
                                size += len(chunk)
                                if size > item["size_bytes"]:
                                    raise ValueError("asset exceeds pinned size")
                                digest.update(chunk)
                                out.write(chunk)
            if size != item["size_bytes"] or digest.hexdigest() != item["sha256"]:
                raise ValueError("asset checksum mismatch")
            _extract_zip(temporary, stage, seen)
            temporary.unlink()
        executable, choices = runtime_manager._resolved_candidate(stage)
        if executable is None or choices:
            raise ValueError("engine entry point ambiguous")
        probe = runtime_manager.probe_executable(executable)
        if not probe.get("success") or not probe.get("build_matches_reviewed_release"):
            raise ValueError("engine health probe failed")
        return executable.relative_to(stage)

    def _prepare_dsh(self, stage: Path, manifest: dict) -> Path:
        if self.integration is None:
            raise ValueError("DSH integration is required")
        node, npm = self.integration._node_command()
        if not node or not npm:
            raise ValueError("Node.js and npm are required")
        lock = DSH_LOCK_DIR / "package-lock.json"
        package = DSH_LOCK_DIR / "package.json"
        locked = json.loads(lock.read_text(encoding="utf-8"))
        packages = locked.get("packages", {})
        if (_sha256(lock) != manifest["lock_sha256"] or
                json.loads(package.read_text(encoding="utf-8")).get("dependencies", {}).get("@deepseek-ai/dsh") != manifest["version"] or
                not isinstance(packages, dict) or
                any(not item.get("resolved", "").startswith("https://registry.npmjs.org/") or
                    not item.get("integrity") for item in packages.values() if item.get("resolved"))):
            raise ValueError("pinned DSH lock does not match manifest")
        shutil.copy2(lock, stage / lock.name)
        shutil.copy2(package, stage / package.name)
        import network_proxy
        base_environment = {key: value for key, value in os.environ.items()
                            if not key.lower().startswith("npm_config_") and
                            key.lower() not in {"npm_token", "node_auth_token"}}
        environment = network_proxy.child_environment(base_environment)
        environment["PATH"] = str(Path(node).parent) + os.pathsep + environment.get("PATH", "")
        environment["npm_config_registry"] = manifest["registry_url"]
        environment["npm_config_audit"] = "false"
        environment["npm_config_fund"] = "false"
        install_home = stage / ".install-home"
        install_home.mkdir()
        environment["HOME"] = str(install_home)
        environment["USERPROFILE"] = str(install_home)
        environment["APPDATA"] = str(install_home / "AppData" / "Roaming")
        environment["LOCALAPPDATA"] = str(install_home / "AppData" / "Local")
        environment["DSH_HOME"] = str(install_home / ".dsh")
        for config_name in (".npmrc", ".global-npmrc"):
            (stage / config_name).write_text("", encoding="utf-8")
        environment["npm_config_userconfig"] = str(stage / ".npmrc")
        environment["npm_config_globalconfig"] = str(stage / ".global-npmrc")
        environment["npm_config_cache"] = str(stage / ".npm-cache")
        result = subprocess.run([npm, "ci", "--no-audit", "--no-fund",
                                 "--registry=" + manifest["registry_url"]], cwd=stage,
                                env=environment, capture_output=True, timeout=600,
                                creationflags=_CREATE_NO_WINDOW)
        if result.returncode:
            raise RuntimeError("npm ci failed")
        if (stage / ".npm-cache").exists():
            shutil.rmtree(stage / ".npm-cache")
        shutil.rmtree(install_home)
        (stage / ".npmrc").unlink(missing_ok=True)
        (stage / ".global-npmrc").unlink(missing_ok=True)
        from dsh_integration import DSHIntegration
        if not DSHIntegration._installation_details(stage)[2]:
            raise ValueError("DSH plugin validation failed")
        entry = Path("node_modules/@deepseek-ai/dsh/lib/bin.js")
        health_env = {key: value for key, value in environment.items()
                      if not key.lower().startswith("npm_config_")}
        health_home = stage / ".health-home"
        health_home.mkdir()
        health_env["HOME"] = str(health_home)
        health_env["USERPROFILE"] = str(health_home)
        health_env["APPDATA"] = str(health_home / "AppData" / "Roaming")
        health_env["LOCALAPPDATA"] = str(health_home / "AppData" / "Local")
        health_env["DSH_HOME"] = str(health_home / ".dsh")
        result = subprocess.run([node, str(stage / entry), "--help"], cwd=stage,
                                env=health_env, capture_output=True, timeout=20,
                                creationflags=_CREATE_NO_WINDOW)
        if result.returncode:
            raise ValueError("DSH help probe failed")
        shutil.rmtree(health_home)
        return entry

    def _busy(self, component: str) -> str | None:
        if component == "engine":
            import backend
            if backend._scan_model_instances(force=True) or backend.is_port_listening():
                return "模型服务或本地端口仍在使用；请先自行停止后重试。"
        else:
            if self.integration is None:
                return "缺少当前 DSH 控制器。"
            state = self.integration.dsh.status()
            if state.get("error") or state.get("running") or state.get("instances") or state.get("port_occupied"):
                return "DSH 或其端口仍在使用；请先自行停止后重试。"
        return None

    def _select(self, component: str, path: str) -> dict:
        if component == "engine":
            if not path:
                import backend
                config = backend.load_config()
                config["server_executable"] = ""
                return {"success": bool(backend.save_config(config))}
            import runtime_manager
            return runtime_manager.select_executable(path)
        if self.integration is None:
            return {"success": False}
        return (self.integration.configure_components(dsh_root=path) if path else
                self.integration.configure_components(clear_dsh=True))

    def activate(self, component: str, prepared_id: str) -> dict:
        directory = _managed_dir(component, prepared_id)
        if directory is None or (meta := _metadata(directory, component)) is None:
            return _result(False, "准备好的版本不存在或已损坏。", "invalid-prepared")
        target = str((directory / meta["entry"]).resolve() if component == "engine" else directory)
        try:
            with _change_lock():
                with _lifecycle_lock(component):
                    if reason := self._busy(component):
                        return _result(False, reason, "busy")
                    prior = (self._current(component) if component == "engine" or
                             getattr(self.integration.dsh, "root_source", "") == "selected" else "")
                    journal = _journal()
                    previous_record = journal.get(component)
                    journal[component] = {"previous": prior, "target": target, "phase": "pending"}
                    _atomic_json(JOURNAL, journal)
                    selected = self._select(component, target)
                    if not selected.get("success"):
                        journal[component] = previous_record if previous_record else {}
                        _atomic_json(JOURNAL, journal)
                        return _result(False, "新版本未能激活；原路径仍保留。", "activate-failed")
                    journal[component]["phase"] = "active"
                    _atomic_json(JOURNAL, journal)
                    return _result(True, "已切换到准备好的版本；旧文件仍保留，可回滚。", "activated",
                                   component=component, version=meta["version"], path=target,
                                   previous=prior)
        except Exception:
            return _result(False, "激活未完成；请检查当前组件路径及重试或回滚。", "activate-failed")

    def rollback(self, component: str) -> dict:
        if component not in {"engine", "dsh"}:
            return _result(False, "未知组件。", "invalid-component")
        try:
            with _change_lock():
                with _lifecycle_lock(component):
                    journal = _journal()
                    record = journal.get(component)
                    if not isinstance(record, dict) or "previous" not in record:
                        return _result(False, "没有可回滚的上一版本路径。", "no-rollback")
                    if reason := self._busy(component):
                        return _result(False, reason, "busy")
                    current = self._current(component)
                    previous = record["previous"]
                    if current == previous:
                        return _result(True, "当前已经是上一版本路径。", "already-rolled-back", path=previous)
                    if record.get("phase") == "rolled_back":
                        return _result(False, "当前路径已由其他操作更改；请手动检查后选择组件。", "path-changed")
                    if current != record.get("target"):
                        return _result(False, "当前路径已由其他操作更改；请手动检查后选择组件。", "path-changed")
                    selected = self._select(component, previous)
                    if not selected.get("success"):
                        return _result(False, "上一版本不可用；当前路径未改动。", "rollback-failed")
                    record["phase"] = "rolled_back"
                    _atomic_json(JOURNAL, journal)
                    return _result(True, "已恢复上一版本路径；新版本文件仍保留。", "rolled-back",
                                   component=component, path=previous)
        except Exception:
            return _result(False, "回滚未完成；当前组件路径请在设置中核对。", "rollback-failed")
