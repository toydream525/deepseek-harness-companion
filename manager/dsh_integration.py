"""DSH installation, provider settings and combined work flow.

The running DSH Host is the settings source of truth. This module holds no
provider secrets or token links on disk and never rewrites whole namespaces.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4

from dsh_service import (COMPONENT_CONFIG, DSHController, _CREATE_NO_WINDOW,
                         _dsh_bin, _discovered_dsh_root, _discovered_node,
                         _verified_node, _kernel)
from app_paths import DATA_ROOT, INSTALL_ROOT


APP_ROOT = INSTALL_ROOT
RESOURCE_ROOT = (Path(sys._MEIPASS) if getattr(sys, "frozen", False)
                 else APP_ROOT)
INSTALL_FILES = RESOURCE_ROOT / "resources" / "dsh-install"
EXPECTED_VERSION = "0.1.5-rc.3"
LLM_NS = "llm-pi-ai"
DEFAULT_NS = "agent-default-model"
ROUTE_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
REF_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PROTOCOLS = {"openai-completions", "openai-responses", "anthropic-messages"}
_PLUGINS = ("dsh-api-settings-controller", "dsh-llm-pi-ai",
            "dsh-agent-default-model", "dsh-client-connection",
            "dsh-session-title-llm")


def _ok(message: str, data=None, revision=None, **more) -> dict:
    return {"success": True, "message": message, "data": data,
            "revision": revision, **more}


def _fail(message: str, code="failed", data=None, revision=None) -> dict:
    return {"success": False, "message": message, "code": code,
            "data": data, "revision": revision}


def _kind(route: str) -> str:
    if route.startswith("companion-local-"):
        return "local"
    if route == "companion-deepseek-cloud":
        return "deepseek_cloud"
    return "custom"


def _model_view(model: dict) -> dict:
    return {"id": model.get("id"), "name": model.get("name"),
            "context_window": model.get("contextWindow"),
            "max_tokens": model.get("maxTokens"),
            "reasoning_efforts": copy.deepcopy(model.get("reasoningEfforts")),
            "compat": copy.deepcopy(model.get("compat"))}


def _model_wire(model: dict, prior: dict | None = None) -> dict:
    if not isinstance(model, dict) or not isinstance(model.get("id"), str) or not model["id"]:
        raise ValueError("模型必须有非空 ID")
    value = copy.deepcopy(prior) if isinstance(prior, dict) else {"id": model["id"]}
    value["id"] = model["id"]
    mapping = {"name": "name", "context_window": "contextWindow",
               "max_tokens": "maxTokens", "reasoning_efforts": "reasoningEfforts",
               "compat": "compat"}
    for source, target in mapping.items():
        if source in model:
            if model[source] is None:
                value.pop(target, None)
            else:
                value[target] = copy.deepcopy(model[source])
    for key in ("contextWindow", "maxTokens"):
        if key in value and (not isinstance(value[key], int) or value[key] <= 0):
            raise ValueError("上下文和输出上限必须为正整数")
    return value


def _provider_view(route: str, profile: dict, user_profiles: dict,
                   credentials: dict) -> dict:
    ref = profile.get("apiKeyEnv")
    info = credentials.get(ref, {}) if isinstance(ref, str) else {}
    return {"id": route, "kind": _kind(route),
            "display_name": profile.get("displayName") or route,
            "base_url": profile.get("baseURL"), "api": profile.get("api"),
            "models": [_model_view(x) for x in profile.get("models", [])
                       if isinstance(x, dict)], "credential_ref": ref,
            "credential_configured": info.get("configured") if info else None,
            "editable": route in user_profiles,
            "settings_ns": LLM_NS,
            "error": profile.get("error")}


def _native_deepseek_view(view: dict, credentials: dict) -> dict:
    profile = view.get("value") or {}
    ref = profile.get("apiKeyEnv")
    info = credentials.get(ref, {}) if isinstance(ref, str) else {}
    models = []
    for item in profile.get("models") or []:
        if isinstance(item, dict):
            models.append({"id": item.get("id"), "name": item.get("name"),
                           "context_window": item.get("contextWindow"),
                           "max_tokens": profile.get("maxTokens")})
    return {"id": "deepseek-official", "kind": "deepseek_cloud",
            "display_name": "DeepSeek 官方", "base_url": profile.get("baseURL"),
            "api": "deepseek-native", "models": models,
            "credential_ref": ref,
            "credential_configured": info.get("configured") if info else None,
            "editable": True, "settings_ns": "llm-deepseek",
            "revision": view["revision"], "error": None}


class DSHIntegration:
    def __init__(self, dsh: DSHController | None = None, model_backend=None):
        self.dsh = dsh or DSHController()
        self.model_backend = model_backend
        self._lock = threading.RLock()

    def _backend(self):
        if self.model_backend is not None:
            return self.model_backend
        import backend
        return backend

    def _node_command(self) -> tuple[str | None, str | None]:
        node = self.dsh.node_executable
        if not _verified_node(node):
            return None, None
        npm = Path(node).parent / "npm.cmd"
        return str(node), str(npm) if npm.is_file() else None

    @staticmethod
    def _installation_details(root: Path) -> tuple[str | None, dict, bool]:
        package = _dsh_bin(root).parent.parent / "package.json"
        try:
            parsed = json.loads(package.read_text(encoding="utf-8"))
            version = parsed.get("version") if isinstance(parsed, dict) else None
        except (OSError, ValueError):
            version = None
        packages = package.parent.parent
        versions = {}
        for name in _PLUGINS:
            path = packages / name / "package.json"
            try:
                parsed = json.loads(path.read_text(encoding="utf-8"))
                versions[name] = parsed.get("version") if isinstance(parsed, dict) else None
            except (OSError, ValueError):
                versions[name] = None
        installed = (version == EXPECTED_VERSION and _dsh_bin(root).is_file()
                     and all(value == EXPECTED_VERSION for value in versions.values()))
        return version, versions, installed

    def component_status(self) -> dict:
        """Read-only, non-secret description for the component setup UI."""
        with self._lock:
            root = self.dsh.root
            version, versions, installed = self._installation_details(root)
            node, npm = self._node_command()
            node_version = _verified_node(node)
            running = self.dsh.status()
            api_ready = False
            api_check = "not_running"
            if running.get("running"):
                api_check = "authentication_required"
                if running.get("can_open"):
                    try:
                        self._describe()
                        api_ready = True
                        api_check = "verified"
                    except (RuntimeError, _RemoteFailure):
                        api_check = "unavailable"
            missing = []
            if not node:
                missing.append("node")
            if not installed:
                missing.append("dsh")
            data = {
                "dsh_root": str(root), "dsh_source": self.dsh.root_source,
                "dsh_installed": installed, "dsh_version": version,
                "dsh_present": bool(version),
                "dsh_compatibility": ("verified" if installed else
                                      "unverified_version" if version and version != EXPECTED_VERSION else
                                      "incomplete" if version else "missing"),
                "expected_version": EXPECTED_VERSION,
                "node_executable": str(node or self.dsh.node_executable or ""),
                "node_source": self.dsh.node_source,
                "node_version": node_version, "node_available": bool(node),
                "npm_available": bool(npm),
                "plugins": {name: value == EXPECTED_VERSION for name, value in versions.items()},
                "plugin_versions": versions,
                "running": bool(running.get("running")),
                "authenticated": bool(running.get("can_open")),
                "pid": running.get("pid"),
                "api_ready": api_ready, "api_check": api_check,
                "missing": missing,
                "downloads": {"dsh": "https://github.com/deepseek-ai/deepseek-harness",
                              "node": "https://nodejs.org/en/download"},
                "suggested_dsh_root": str(INSTALL_ROOT / "components" / "dsh"),
                "instructions": ("先从 Node.js 官网安装 Node.js 20 或更新版本。DSH 官网建议安装 Node.js 后使用 npx @deepseek-ai/dsh web；"
                                 "本控制台目前只验证 DSH 0.1.5-rc.3 与对应插件。若手动安装，请在命令行运行："
                                 "npm install --prefix \"<DSH目录>\" "
                                 "@deepseek-ai/dsh@0.1.5-rc.3 "
                                 "@deepseek-ai/dsh-api-settings-controller@0.1.5-rc.3 "
                                 "@deepseek-ai/dsh-llm-pi-ai@0.1.5-rc.3 "
                                 "@deepseek-ai/dsh-agent-default-model@0.1.5-rc.3 "
                                 "@deepseek-ai/dsh-client-connection@0.1.5-rc.3 "
                                 "@deepseek-ai/dsh-session-title-llm@0.1.5-rc.3。"
                                 "安装后在此选择 DSH 目录和 node.exe；不会自动下载或修改系统环境。"),
            }
            if version and version != EXPECTED_VERSION:
                message = f"找到 DSH {version}，此版本尚未验证；当前支持 {EXPECTED_VERSION}"
            elif missing:
                message = "请配置并验证 Node.js 与 DeepSeek Harness 后再启动工作流程"
            elif running.get("running") and not api_ready:
                message = "DSH 已运行；请连接当前实例的认证链接以验证 API"
            else:
                message = "DSH 与 Node.js 已就绪" if api_ready else "DSH 与 Node.js 安装已验证，可启动 DSH"
            return _ok(message, data)

    def configure_components(self, dsh_root: str | Path | None = None,
                             node_executable: str | Path | None = None,
                             *, clear_dsh: bool = False, clear_node: bool = False) -> dict:
        """Persist explicit paths only after checking exact executable/package identity."""
        with self._lock:
            try:
                saved = json.loads(COMPONENT_CONFIG.read_text(encoding="utf-8"))
                if not isinstance(saved, dict):
                    saved = {}
            except (OSError, ValueError):
                saved = {}
            next_settings = {k: v for k, v in saved.items()
                             if k in ("dsh_root", "node_executable") and isinstance(v, str)}
            if clear_dsh:
                next_settings.pop("dsh_root", None)
            elif dsh_root is not None:
                candidate = Path(dsh_root).expanduser().absolute()
                _, _, valid = self._installation_details(candidate)
                if not valid:
                    return _fail("所选 DSH 目录缺少已验证版本或所需插件，未保存路径", "invalid-dsh")
                next_settings["dsh_root"] = str(candidate)
            if clear_node:
                next_settings.pop("node_executable", None)
            elif node_executable is not None:
                candidate = Path(node_executable).expanduser().absolute()
                if not _verified_node(candidate):
                    return _fail("所选 node.exe 无效或版本低于 Node.js 20，未保存路径", "invalid-node")
                next_settings["node_executable"] = str(candidate)
            new_root, root_source = (_discovered_dsh_root(ignore_preferences=True) if "dsh_root" not in next_settings
                                     else (Path(next_settings["dsh_root"]), "selected"))
            new_node, node_source = (_discovered_node(ignore_preferences=True) if "node_executable" not in next_settings
                                     else (next_settings["node_executable"], "selected"))
            old_paths = (self.dsh.root, self.dsh.node_executable,
                         self.dsh.root_source, self.dsh.node_source)
            if not self.dsh.configure_paths(new_root, new_node, root_source=root_source,
                                            node_source=node_source):
                return _fail("本窗口启动的 DSH 仍在运行，请停止后再切换组件目录", "owned-running")
            try:
                COMPONENT_CONFIG.parent.mkdir(parents=True, exist_ok=True)
                temporary = COMPONENT_CONFIG.with_name(COMPONENT_CONFIG.name + "." + uuid4().hex + ".tmp")
                temporary.write_text(json.dumps(next_settings, indent=2), encoding="utf-8")
                os.replace(temporary, COMPONENT_CONFIG)
            except OSError:
                self.dsh.configure_paths(old_paths[0], old_paths[1],
                                         root_source=old_paths[2], node_source=old_paths[3])
                return _fail("无法保存组件路径", "config-write")
            return self.component_status()

    def detect(self) -> dict:
        """Backward-compatible component scan used by the existing UI."""
        report = self.component_status()
        facts = dict(report["data"])
        facts.update({"installed": facts["dsh_installed"],
                      "compatible": facts["dsh_version"] in (None, EXPECTED_VERSION),
                      "version": facts["dsh_version"], "root": facts["dsh_root"]})
        return _ok(report["message"], facts)

    def install(self) -> dict:
        """Explicit user action to install rc3 with an existing Node/npm."""
        with self._lock:
            before = self.detect()
            facts = before["data"]
            if facts["installed"]:
                node, _ = self._node_command()
                if not node:
                    return _fail("DSH 已安装；请从 Node.js 官方网站安装并选择 node.exe", "node-missing", facts)
                return _ok("已复用现有 DSH 0.1.5-rc.3 安装", facts)
            if not facts["compatible"]:
                return _fail("现有 DSH 版本不兼容；请先检查该安装，未执行覆盖", "incompatible", facts)
            root = self.dsh.root
            if root.resolve() not in ((INSTALL_ROOT / "components" / "dsh").resolve(),
                                      (DATA_ROOT / "dsh").resolve()):
                return _fail("不会覆盖所选外部 DSH 目录；请使用官方安装说明或应用组件目录", "external-install", facts)
            if facts["running"]:
                return _fail("DSH 正在运行，不能修复其安装文件", "running", facts)
            lock = INSTALL_FILES / "package-lock.json"
            manifest = INSTALL_FILES / "package.json"
            if not lock.is_file() or not manifest.is_file():
                return _fail("缺少随应用提供的 DSH 固定依赖清单", "missing-lock")
            if hashlib.sha256(lock.read_bytes()).hexdigest().upper() != \
                    "E9D0E3D2BF1798B168A5DEF012FB15FDE8EABBF163C9E964EBBE3A7F87C80CDA":
                return _fail("DSH 固定依赖清单校验失败", "lock-mismatch")
            node, npm = self._node_command()
            if not node or not npm:
                return _fail("安装 DSH 需要已安装的 Node.js 与 npm.cmd；请按官方说明安装并选择 node.exe", "node-missing", facts)
            if (root / "package.json").is_file():
                try:
                    existing = json.loads((root / "package.json").read_text(encoding="utf-8"))
                    bundled = json.loads(manifest.read_text(encoding="utf-8"))
                    if existing.get("dependencies") != bundled.get("dependencies"):
                        return _fail("现有 DSH 清单含不同依赖，未覆盖用户插件", "incompatible")
                except (OSError, json.JSONDecodeError):
                    return _fail("现有 DSH 清单不可识别，未覆盖", "incompatible")
            existing_lock = root / "package-lock.json"
            if existing_lock.is_file() and hashlib.sha256(existing_lock.read_bytes()).digest() != \
                    hashlib.sha256(lock.read_bytes()).digest():
                return _fail("现有 DSH 锁文件不同，未覆盖用户依赖", "incompatible")
            runtime = DATA_ROOT / "runtime"
            runtime.mkdir(parents=True, exist_ok=True)
            stage = Path(tempfile.mkdtemp(prefix="dsh-install-", dir=runtime))
            backup = None
            target = root / "node_modules"
            moved_new = False
            committed = False
            restore_issue = False
            try:
                shutil.copy2(manifest, stage / "package.json")
                shutil.copy2(lock, stage / "package-lock.json")
                node_dir = str(Path(npm).parent)
                environment = {**os.environ, "PATH": node_dir + os.pathsep + os.environ.get("PATH", "")}
                process = subprocess.run([npm, "ci", "--no-audit", "--no-fund"], cwd=stage,
                                         env=environment, capture_output=True,
                                         creationflags=_CREATE_NO_WINDOW, timeout=300)
                if process.returncode != 0:
                    return _fail("DSH 固定版本安装失败；现有安装未改动", "npm-failed")
                staged_bin = stage / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
                if not staged_bin.is_file():
                    return _fail("安装包缺少 DSH 启动文件；现有安装未改动", "invalid-install")
                mutex = _kernel.CreateMutexW(None, False, "Local\\DeepSeekHarness-3080-launch")
                if not mutex:
                    return _fail("无法取得 DSH 安装互斥锁；现有安装未改动", "lock-failed")
                acquired = _kernel.WaitForSingleObject(mutex, 0) in (0, 0x80)
                if not acquired:
                    _kernel.CloseHandle(mutex)
                    return _fail("DSH 正在启动；安装暂不提交", "running")
                try:
                    current = self.dsh.status()
                    if current.get("error") or current.get("running") or current.get("instances"):
                        return _fail("DSH 已在安装期间启动；未替换运行文件", "running")
                    root.mkdir(parents=True, exist_ok=True)
                    if target.exists():
                        backup = root / ("node_modules.backup-" + uuid4().hex[:8])
                        os.replace(target, backup)
                    try:
                        os.replace(stage / "node_modules", target)
                        moved_new = True
                    except OSError:
                        if backup:
                            os.replace(backup, target)
                        raise
                    if not (root / "package.json").exists():
                        shutil.copy2(manifest, root / "package.json")
                    if not (root / "package-lock.json").exists():
                        shutil.copy2(lock, root / "package-lock.json")
                    result = self.detect()
                    if not result["data"]["installed"]:
                        return _fail("DSH 文件已安装但插件检查未通过，请检查后重试", "invalid-install",
                                     result["data"])
                    committed = True
                    return _ok("DSH 已安装并完成插件检查", result["data"])
                finally:
                    _kernel.ReleaseMutex(mutex)
                    _kernel.CloseHandle(mutex)
            except (OSError, subprocess.SubprocessError) as exc:
                return _fail("DSH 安装未完成；原安装已保留：" + type(exc).__name__, "install-failed")
            finally:
                if not committed and moved_new and target.exists():
                    try:
                        os.replace(target, stage / "failed-node-modules")
                    except OSError:
                        restore_issue = True
                if not committed and backup and backup.exists() and not target.exists():
                    try:
                        os.replace(backup, target)
                    except OSError:
                        restore_issue = True
                if stage.resolve().is_relative_to(runtime.resolve()):
                    shutil.rmtree(stage, ignore_errors=True)
                if restore_issue:
                    return _fail("DSH 安装失败且旧依赖目录未能恢复；备份仍保留在 DSH 目录，需人工核查",
                                 "rollback-failed")
                # A replaced installation is retained for manual recovery;
                # never delete a user's prior tree as an install side effect.

    def _rpc(self, endpoint: str, args: dict):
        result = self.dsh.call_remote(endpoint, args)
        if not isinstance(result, dict) or result.get("ok") not in (True, False):
            raise RuntimeError("DSH 返回了无效的操作结果")
        if result["ok"]:
            return result.get("value")
        error = result.get("error") or {}
        code = str(error.get("code") or "remote-error")
        if code == "settings/conflict":
            raise _Conflict()
        if code in ("gateway/unauthorized", "gateway/auth-required"):
            raise RuntimeError("DSH 认证已失效，请重新连接原启动链接")
        if endpoint.startswith("credentials/"):
            raise _RemoteFailure(code, "DSH 凭据操作被拒绝；请检查引用或设置权限")
        if endpoint == "settings/mutate":
            raise _RemoteFailure(code, "DSH 拒绝了设置变更；请检查字段格式并刷新状态")
        message = str(error.get("message") or "DSH 拒绝了操作")[:300]
        for secret in (args.get("value"), (args.get("request") or {}).get("apiKey")):
            if isinstance(secret, str) and secret:
                message = message.replace(secret, "[redacted]")
        message = re.sub(r"(?i)([?&]token=)[^&\s]+", r"\1[redacted]", message)
        raise _RemoteFailure(code, message)

    def _describe(self) -> dict:
        value = self._rpc("settings/describe", {})
        if not isinstance(value, dict) or not isinstance(value.get("namespaces"), list):
            raise RuntimeError("DSH 设置结构不可识别")
        return value

    @staticmethod
    def _view(describe: dict, ns: str) -> dict:
        view = next((x for x in describe["namespaces"]
                     if isinstance(x, dict) and x.get("ns") == ns), None)
        if not isinstance(view, dict) or not isinstance(view.get("revision"), int):
            raise RuntimeError(f"DSH 未提供 {ns} 设置分节")
        return view

    def _mutate(self, ns: str, ops: list[dict], revision: int) -> dict:
        value = self._rpc("settings/mutate", {"ns": ns, "ops": ops,
                                               "expectedRevision": revision})
        if not isinstance(value, dict) or value.get("ns") != ns:
            raise RuntimeError("DSH 未确认设置写入")
        return value

    def list_providers(self) -> dict:
        with self._lock:
            try:
                doc = self._describe()
                view = self._view(doc, LLM_NS)
                providers = (view.get("value") or {}).get("providers") or {}
                user_profiles = (view.get("user") or {}).get("providers") or {}
                if not isinstance(providers, dict) or not isinstance(user_profiles, dict):
                    raise RuntimeError("DSH provider 列表结构不可识别")
                native = next((x for x in doc["namespaces"]
                               if isinstance(x, dict) and x.get("ns") == "llm-deepseek"), None)
                refs = {x.get("apiKeyEnv") for x in providers.values()
                        if isinstance(x, dict) and isinstance(x.get("apiKeyEnv"), str)}
                if native and isinstance((native.get("value") or {}).get("apiKeyEnv"), str):
                    refs.add(native["value"]["apiKeyEnv"])
                sorted_refs = sorted(refs)
                credentials = {}
                for index in range(0, len(sorted_refs), 64):
                    batch = self._rpc("credentials/describe", {"refs": sorted_refs[index:index + 64]})
                    if not isinstance(batch, dict):
                        raise RuntimeError("DSH 凭据状态结构不可识别")
                    credentials.update(batch)
                rows = [_provider_view(key, profile, user_profiles, credentials)
                        for key, profile in providers.items() if isinstance(profile, dict)]
                for row in rows:
                    row["revision"] = view["revision"]
                if native and isinstance(native.get("value"), dict):
                    rows.insert(0, _native_deepseek_view(native, credentials))
                return _ok("已读取 DSH 提供方", {"providers": rows,
                                             "writable": bool(doc.get("writable"))},
                           view["revision"])
            except (RuntimeError, _RemoteFailure) as exc:
                return _fail(str(exc), getattr(exc, "code", "unavailable"))

    def upsert_provider(self, data: dict, expected_revision: int | None = None) -> dict:
        with self._lock:
            try:
                if not isinstance(data, dict):
                    raise ValueError("提供方数据必须为对象")
                route = data.get("id")
                if not isinstance(route, str) or not ROUTE_RE.fullmatch(route):
                    raise ValueError("提供方 ID 须为小写字母、数字与连字符")
                if route == "deepseek-official":
                    return self._upsert_native_deepseek(data, expected_revision)
                doc = self._describe()
                view = self._view(doc, LLM_NS)
                if not doc.get("writable"):
                    return _fail("DSH 设置当前不可写", "readonly", revision=view["revision"])
                revision = view["revision"]
                if expected_revision is not None and expected_revision != revision:
                    return _fail("提供方已被其他窗口修改，请刷新后重试", "conflict", revision=revision)
                existing = ((view.get("value") or {}).get("providers") or {}).get(route)
                profile = copy.deepcopy(existing) if isinstance(existing, dict) else {}
                for source, target in (("display_name", "displayName"),
                                       ("base_url", "baseURL"), ("api", "api"),
                                       ("credential_ref", "apiKeyEnv")):
                    if source in data:
                        if data[source] is None or data[source] == "":
                            profile.pop(target, None)
                        else:
                            profile[target] = data[source]
                if "models" in data:
                    prior = {x.get("id"): x for x in profile.get("models", [])
                             if isinstance(x, dict)}
                    if not isinstance(data["models"], list):
                        raise ValueError("模型列表必须为数组")
                    if not all(isinstance(x, dict) for x in data["models"]):
                        raise ValueError("每个模型必须为对象")
                    profile["models"] = [_model_wire(x, prior.get(x.get("id")))
                                         for x in data["models"]]
                if "compat" in data:
                    profile["compat"] = copy.deepcopy(data["compat"])
                if "api" in profile and profile["api"] not in PROTOCOLS:
                    raise ValueError("请选择受支持的 API 协议")
                if "baseURL" in profile:
                    endpoint = urlsplit(str(profile["baseURL"]))
                    if endpoint.scheme not in ("http", "https") or not endpoint.netloc:
                        raise ValueError("请填写有效的 HTTP(S) API 地址")
                    if data.get("kind") == "local" and endpoint.hostname not in ("127.0.0.1", "localhost", "::1"):
                        raise ValueError("本地提供方只能指向本机回环地址")
                if "models" in profile and not profile["models"]:
                    raise ValueError("模型列表不能为空；省略列表可继承目录")
                key = data.get("api_key")
                if key is not None:
                    if not isinstance(key, str) or not key:
                        raise ValueError("API 密钥不能为空")
                    if not profile.get("apiKeyEnv"):
                        profile["apiKeyEnv"] = "COMPANION_" + re.sub(r"[^A-Z0-9]", "_", route.upper()) + "_API_KEY"
                ref = profile.get("apiKeyEnv")
                if ref is not None and (not isinstance(ref, str) or not REF_RE.fullmatch(ref)):
                    raise ValueError("凭据引用格式无效")
                old_user = ((view.get("user") or {}).get("providers") or {}).get(route)
                if profile != existing:
                    written = self._mutate(LLM_NS, [{"op": "set", "path": ["providers", route],
                                                   "value": profile}], revision)
                    revision = written["revision"]
                if key is not None:
                    try:
                        self._rpc("credentials/set", {"ref": ref, "value": key})
                    except Exception:
                        if profile != existing:
                            undo = ({"op": "set", "path": ["providers", route], "value": old_user}
                                    if isinstance(old_user, dict) else
                                    {"op": "unset", "path": ["providers", route]})
                            try:
                                self._mutate(LLM_NS, [undo], revision)
                            except Exception:
                                return _fail("凭据写入失败且配置回滚失败，请刷新核查", "partial")
                        raise RuntimeError("凭据写入失败；配置已回滚")
                return _ok("提供方已保存", {"id": route}, revision)
            except _Conflict:
                latest = self.list_providers()
                return _fail("提供方已被其他窗口修改，请刷新后重试", "conflict",
                             revision=latest.get("revision"))
            except (ValueError, RuntimeError, _RemoteFailure) as exc:
                return _fail(str(exc), getattr(exc, "code", "invalid"))

    def _upsert_native_deepseek(self, data: dict, expected_revision: int | None) -> dict:
        """Edit only fields the native DeepSeek settings namespace owns."""
        try:
            doc = self._describe()
            view = self._view(doc, "llm-deepseek")
            revision = view["revision"]
            if expected_revision is not None and expected_revision != revision:
                return _fail("DeepSeek 设置已变化，请刷新", "conflict", revision=revision)
            if not doc.get("writable"):
                return _fail("DSH 设置当前不可写", "readonly", revision=revision)
            ops = []
            if "base_url" in data:
                value = data["base_url"]
                if value:
                    parsed = urlsplit(value)
                    if parsed.scheme not in ("http", "https") or not parsed.netloc:
                        raise ValueError("DeepSeek API 地址无效")
                    ops.append({"op": "set", "path": ["baseURL"], "value": value})
                else:
                    ops.append({"op": "unset", "path": ["baseURL"]})
            if "credential_ref" in data:
                ref = data["credential_ref"]
                if ref and not REF_RE.fullmatch(ref):
                    raise ValueError("凭据引用格式无效")
                ops.append({"op": "set", "path": ["apiKeyEnv"], "value": ref}
                           if ref else {"op": "unset", "path": ["apiKeyEnv"]})
            if "models" in data:
                old_models = {x.get("id"): x for x in (view.get("value") or {}).get("models", [])
                              if isinstance(x, dict)}
                if not isinstance(data["models"], list) or not all(
                        isinstance(x, dict) for x in data["models"]):
                    raise ValueError("模型列表格式无效")
                # The native adapter has a distinct schema; preserve all unknown
                # model fields, and only accept capacities/name that it exposes.
                models = []
                for item in data["models"]:
                    base = copy.deepcopy(old_models.get(item.get("id"), {}))
                    model = {**base, "id": item.get("id")}
                    if not isinstance(model["id"], str) or not model["id"]:
                        raise ValueError("模型 ID 无效")
                    for source, target in (("name", "name"),
                                           ("context_window", "contextWindow")):
                        if source in item:
                            model[target] = item[source]
                    models.append(model)
                if not models:
                    raise ValueError("模型列表不能为空")
                ops.append({"op": "set", "path": ["models"], "value": models})
            key = data.get("api_key")
            if key is not None and (not isinstance(key, str) or not key):
                raise ValueError("API 密钥不能为空")
            ref = data.get("credential_ref") or (view.get("value") or {}).get("apiKeyEnv")
            if key is not None and not ref:
                ref = "DEEPSEEK_API_KEY"
                ops.append({"op": "set", "path": ["apiKeyEnv"], "value": ref})
            old_user = view.get("user") or {}
            if ops:
                written = self._mutate("llm-deepseek", ops, revision)
                revision = written["revision"]
            if key is not None:
                try:
                    self._rpc("credentials/set", {"ref": ref, "value": key})
                except Exception:
                    if ops:
                        undo = []
                        for op in ops:
                            field = op["path"][0]
                            undo.append({"op": "set", "path": [field], "value": old_user[field]}
                                        if field in old_user else
                                        {"op": "unset", "path": [field]})
                        try:
                            self._mutate("llm-deepseek", undo, revision)
                        except Exception:
                            return _fail("凭据写入失败且设置回滚失败，请刷新核查", "partial")
                    raise RuntimeError("凭据写入失败；设置已回滚")
            return _ok("DeepSeek 官方提供方已更新", {"id": "deepseek-official"}, revision)
        except _Conflict:
            latest = self._view(self._describe(), "llm-deepseek")
            return _fail("DeepSeek 设置已变化，请刷新", "conflict",
                         revision=latest["revision"])
        except (ValueError, RuntimeError, _RemoteFailure) as exc:
            return _fail(str(exc), getattr(exc, "code", "invalid"))

    def delete_provider(self, provider_id: str, expected_revision: int | None = None) -> dict:
        with self._lock:
            try:
                doc = self._describe()
                view = self._view(doc, LLM_NS)
                revision = view["revision"]
                if expected_revision is not None and expected_revision != revision:
                    return _fail("提供方已变化，请刷新", "conflict", revision=revision)
                user_profiles = ((view.get("user") or {}).get("providers") or {})
                if provider_id not in user_profiles:
                    return _fail("只能删除用户添加的提供方", "readonly", revision=revision)
                written = self._mutate(LLM_NS, [{"op": "unset", "path": ["providers", provider_id]}], revision)
                return _ok("提供方已删除；原凭据引用保持可恢复", {"id": provider_id},
                           written["revision"])
            except _Conflict:
                return _fail("提供方已变化，请刷新", "conflict",
                             revision=self.list_providers().get("revision"))
            except (RuntimeError, _RemoteFailure) as exc:
                return _fail(str(exc), getattr(exc, "code", "unavailable"))

    def discover_models(self, provider_id: str | None = None, *,
                        base_url: str | None = None, api: str | None = None,
                        api_key: str | None = None) -> dict:
        """Read catalog/endpoint model listing; a draft key is never stored."""
        with self._lock:
            try:
                request = {}
                if provider_id:
                    request["provider"] = provider_id
                if base_url:
                    request["baseURL"] = base_url
                if api:
                    request["api"] = api
                if api_key:
                    request["apiKey"] = api_key
                models = self._rpc("llm/discoverModels", {"settingsNs": LLM_NS,
                                                            "request": request})
                if not isinstance(models, list):
                    raise RuntimeError("DSH 模型发现结果不可识别")
                clean = [{"id": x.get("id"), "name": x.get("name"),
                          "context_window": x.get("contextWindow"),
                          "max_tokens": x.get("maxTokens")}
                         for x in models if isinstance(x, dict) and isinstance(x.get("id"), str)]
                return _ok("已获取可选模型", {"models": clean,
                                         "connection_verified": bool(base_url and not provider_id)})
            except (RuntimeError, _RemoteFailure) as exc:
                return _fail(str(exc), getattr(exc, "code", "unavailable"))

    def test_provider(self, provider_id: str, generate: bool = False) -> dict:
        if generate:
            return _fail("生成测试会产生实际请求，请在 DSH 会话中明确发起；当前仅支持模型发现测试",
                         "unsupported")
        providers = self.list_providers()
        if not providers["success"]:
            return providers
        row = next((x for x in providers["data"]["providers"] if x["id"] == provider_id), None)
        if row is None:
            return _fail("未找到提供方", "not-found")
        result = self.discover_models(provider_id)
        if result["success"]:
            result["data"]["connection_verified"] = False
            result["message"] = "模型目录可读取；目录型路由不代表已发送付费请求"
        return result

    def get_new_session_default(self) -> dict:
        with self._lock:
            try:
                doc = self._describe()
                view = self._view(doc, DEFAULT_NS)
                value = view.get("value") or {}
                if not isinstance(value, dict):
                    raise RuntimeError("DSH 默认模型设置不可识别")
                return _ok("已读取新会话默认模型",
                           {"provider_id": value.get("provider"),
                            "model_id": value.get("model"),
                            "reasoning_effort": value.get("reasoningEffort")},
                           view["revision"])
            except (RuntimeError, _RemoteFailure) as exc:
                return _fail(str(exc), getattr(exc, "code", "unavailable"))

    def set_new_session_default(self, provider_id: str, model_id: str,
                                reasoning_effort: str | None = None,
                                expected_revision: int | None = None) -> dict:
        with self._lock:
            try:
                if not isinstance(provider_id, str) or not provider_id or not isinstance(model_id, str) or not model_id:
                    raise ValueError("请选择提供方与模型")
                doc = self._describe()
                view = self._view(doc, DEFAULT_NS)
                revision = view["revision"]
                if expected_revision is not None and revision != expected_revision:
                    return _fail("默认模型已变化，请刷新", "conflict", revision=revision)
                value = view.get("value") or {}
                if (value.get("provider"), value.get("model"), value.get("reasoningEffort")) == (
                        provider_id, model_id, reasoning_effort):
                    return _ok("新会话默认模型已是所选项", {"provider_id": provider_id,
                                                       "model_id": model_id,
                                                       "reasoning_effort": reasoning_effort}, revision)
                ops = [{"op": "set", "path": ["provider"], "value": provider_id},
                       {"op": "set", "path": ["model"], "value": model_id},
                       ({"op": "set", "path": ["reasoningEffort"], "value": reasoning_effort}
                        if reasoning_effort else {"op": "unset", "path": ["reasoningEffort"]})]
                written = self._mutate(DEFAULT_NS, ops, revision)
                return _ok("已设为后续新会话默认模型",
                           {"provider_id": provider_id, "model_id": model_id,
                            "reasoning_effort": reasoning_effort}, written["revision"])
            except _Conflict:
                return _fail("默认模型已变化，请刷新", "conflict",
                             revision=self.get_new_session_default().get("revision"))
            except (ValueError, RuntimeError, _RemoteFailure) as exc:
                return _fail(str(exc), getattr(exc, "code", "unavailable"))

    @staticmethod
    def local_provider_id(model_id: str) -> str:
        digest = hashlib.sha256(model_id.encode("utf-8")).hexdigest()[:12]
        return "companion-local-" + digest

    def register_local_model(self, expected_revision: int | None = None, *,
                             output_limit: int | None = None) -> dict:
        """Publish only facts from the currently running backend endpoint."""
        try:
            backend = self._backend()
            status = backend.get_status()
            if not status.get("api_online"):
                return _fail("本地模型 API 尚未就绪", "model-offline")
            endpoint = backend.get_active_endpoint()
            if not isinstance(endpoint, dict) or not endpoint.get("success"):
                return _fail("无法读取运行中模型的实际端点", "model-unknown")
            base_url = endpoint.get("base_url")
            model_id = endpoint.get("model_id")
            context = endpoint.get("context_size")
            runtime_cap = endpoint.get("max_output_tokens")
            if (not isinstance(base_url, str) or not isinstance(model_id, str) or not model_id
                    or not isinstance(context, int) or context <= 0):
                return _fail("运行模型的端点或上下文尚未确认", "model-metadata")
            if output_limit is not None and (type(output_limit) is not int
                                             or output_limit <= 0 or output_limit > context):
                return _fail("DSH 请求输出上限须为不超过当前上下文的正整数", "invalid-output-limit")
            requested = output_limit if output_limit is not None else min(4096, max(1, context // 4))
            source = "user_request_limit" if output_limit is not None else "companion_request_policy"
            if isinstance(runtime_cap, int) and runtime_cap > 0 and requested > runtime_cap:
                requested = runtime_cap
                source = "runtime_cap"
            key = backend.get_active_api_key()
            route = self.local_provider_id(model_id)
            secret = key if isinstance(key, str) and key else "local"
            model_digest = hashlib.sha256(model_id.encode("utf-8")).hexdigest()[:12].upper()
            secret_digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()[:16].upper()
            ref_prefix = f"COMPANION_LOCAL_{model_digest}_{secret_digest}_"
            doc = self._describe()
            llm_view = self._view(doc, LLM_NS)
            profile = ((llm_view.get("value") or {}).get("providers") or {}).get(route) or {}
            old_ref = profile.get("apiKeyEnv") if isinstance(profile, dict) else None
            reuse_ref = (isinstance(old_ref, str) and old_ref.startswith(ref_prefix)
                         and re.fullmatch(re.escape(ref_prefix) + r"[A-F0-9]{8}_API_KEY", old_ref))
            if reuse_ref:
                info = self._rpc("credentials/describe", {"refs": [old_ref]})
                reuse_ref = bool(isinstance(info, dict) and
                                 isinstance(info.get(old_ref), dict) and
                                 info[old_ref].get("configured") is True)
            if reuse_ref:
                credential_ref = old_ref
                created_ref = None
            else:
                # A fresh app-owned address avoids overwriting an unreadable
                # credential from another installation or an older session.
                credential_ref = None
                for _ in range(5):
                    candidate = ref_prefix + uuid4().hex[:8].upper() + "_API_KEY"
                    info = self._rpc("credentials/describe", {"refs": [candidate]})
                    if not (isinstance(info, dict) and isinstance(info.get(candidate), dict)
                            and info[candidate].get("configured") is True):
                        credential_ref = candidate
                        break
                if credential_ref is None:
                    return _fail("无法分配独立的本地凭据引用", "credential-collision")
                created_ref = credential_ref
            model = {"id": model_id, "name": model_id,
                     "context_window": context, "max_tokens": requested}
            data = {"id": route, "kind": "local", "display_name": f"本地 {model_id}",
                    "base_url": base_url, "api": "openai-completions", "models": [model],
                    "credential_ref": credential_ref}
            if created_ref:
                data["api_key"] = secret
            result = self.upsert_provider(data, expected_revision)
            if result["success"]:
                result["data"]["model_id"] = model_id
                result["data"]["output_limit"] = requested
                result["data"]["output_limit_source"] = source
                result["data"]["runtime_max_output_tokens"] = runtime_cap
                result["data"]["credential_created_ref"] = created_ref
            return result
        except (AttributeError, RuntimeError, ValueError, _RemoteFailure, _Conflict) as exc:
            return _fail("本地模型元数据不可用：" + type(exc).__name__, "model-metadata")

    def start_work(self, provider_id: str, model_id: str,
                   progress=None, cancel=None, *, reasoning_effort: str | None = None,
                   local_model_id: str | None = None,
                   preset_id: str | None = None,
                   output_limit: int | None = None) -> dict:
        """Start only what this call needs; rollback only its own changes."""
        with self._lock:
            model_started = False
            dsh_started = False
            provider_undo = None
            default_undo = None
            rollback_messages = []
            local_limit_details = None
            created_credential_ref = None

            def emit(stage, message):
                if callable(progress):
                    progress({"stage": stage, "message": message})

            def cancelled():
                return bool(cancel.is_set()) if hasattr(cancel, "is_set") else bool(cancel()) if callable(cancel) else False

            try:
                if cancelled():
                    raise RuntimeError("操作已取消")
                emit("detect", "检查 DSH 安装")
                components = self.component_status()["data"]
                if components["missing"]:
                    return _fail("请先配置并验证缺少的组件：" + ", ".join(components["missing"]),
                                 "components-missing", components)
                local = provider_id == "local" or provider_id.startswith("companion-local-")
                if local:
                    emit("model", "检查本地模型 API")
                    backend = self._backend()
                    state = backend.get_status()
                    if state.get("control_read_only") or state.get("state") == "EXTERNAL":
                        raise RuntimeError(str(state.get("read_only_reason") or
                                               "Legacy manager controls this model; close it before starting local DSH work"))
                    selected_local = local_model_id or None
                    if state.get("api_online") or state.get("state") == "LOADING":
                        if selected_local:
                            active = state.get("active_profile")
                            endpoint_now = backend.get_active_endpoint()
                            catalog_id = endpoint_now.get("catalog_model_id") if isinstance(endpoint_now, dict) else None
                            if selected_local not in (active, catalog_id):
                                raise RuntimeError("当前运行的是另一模型，请先在模型页切换后再开始 DSH 工作")
                    else:
                        model_started = True  # stop_model is owner-only, including partial starts
                        start = backend.start_model(profile_id=selected_local,
                                                    preset_id=preset_id)
                        if not start.get("success"):
                            raise RuntimeError(str(start.get("message") or "本地模型启动失败"))
                    deadline = time.monotonic() + 240
                    while time.monotonic() < deadline:
                        if cancelled():
                            raise RuntimeError("操作已取消")
                        state = backend.get_status()
                        if state.get("control_read_only") or state.get("state") == "EXTERNAL":
                            raise RuntimeError(str(state.get("read_only_reason") or
                                                   "Legacy manager controls this model"))
                        if state.get("api_online") and state.get("state") == "RUNNING":
                            break
                        if state.get("state") == "ERROR":
                            raise RuntimeError(str(state.get("state_text") or "本地模型加载失败"))
                        emit("model", "等待本地模型 API 就绪")
                        time.sleep(1)
                    else:
                        raise RuntimeError("本地模型未在四分钟内就绪")
                if cancelled():
                    raise RuntimeError("操作已取消")
                emit("dsh", "检查 DSH 服务")
                before_dsh = self.dsh.status()
                if not before_dsh["running"]:
                    dsh_started = True  # cleanup is owner-only, including partial starts
                    started = self.dsh.start()
                    if not started.get("success"):
                        raise RuntimeError(started.get("message") or "DSH 启动失败")
                # A pre-existing DSH must be attached with a verified original
                # link; no restart or naked 401 page is used as a shortcut.
                self._describe()
                if cancelled():
                    raise RuntimeError("操作已取消")
                if local:
                    emit("provider", "接入当前本地模型")
                    prior_doc = self._describe()
                    prior_view = self._view(prior_doc, LLM_NS)
                    route = self.local_provider_id(self._backend().get_active_endpoint()["model_id"])
                    old = ((prior_view.get("user") or {}).get("providers") or {}).get(route)
                    registered = self.register_local_model(prior_view["revision"],
                                                           output_limit=output_limit)
                    if not registered["success"]:
                        raise RuntimeError(registered["message"])
                    if registered["revision"] != prior_view["revision"]:
                        provider_undo = (route, copy.deepcopy(old), registered["revision"])
                    created_credential_ref = registered["data"].get("credential_created_ref")
                    provider_id = route
                    model_id = registered["data"]["model_id"]
                    local_limit_details = {key: registered["data"].get(key)
                                           for key in ("output_limit", "output_limit_source",
                                                       "runtime_max_output_tokens")}
                else:
                    providers = self.list_providers()
                    if not providers["success"] or not any(
                            x["id"] == provider_id for x in providers["data"]["providers"]):
                        raise RuntimeError("所选云端提供方尚未配置")
                if cancelled():
                    raise RuntimeError("操作已取消")
                emit("default", "设置后续新会话默认模型")
                old_default = self._view(self._describe(), DEFAULT_NS)
                selected = self.set_new_session_default(provider_id, model_id,
                                                        reasoning_effort=reasoning_effort,
                                                        expected_revision=old_default["revision"])
                if not selected["success"]:
                    raise RuntimeError(selected["message"])
                if selected["revision"] != old_default["revision"]:
                    default_undo = (copy.deepcopy(old_default.get("user") or {}), selected["revision"])
                if cancelled():
                    raise RuntimeError("操作已取消")
                emit("open", "打开 DSH 认证页面")
                opened = self.dsh.open_web()
                if not opened.get("success"):
                    raise RuntimeError(opened.get("message") or "无法打开 DSH")
                emit("ready", "DSH 工作环境已就绪")
                return _ok("DSH 工作环境已就绪", {"provider_id": provider_id,
                                                "model_id": model_id,
                                                **(local_limit_details or {})})
            except Exception as exc:
                provider_restored = provider_undo is None
                if default_undo:
                    old, revision = default_undo
                    ops = [{"op": "set", "path": [key], "value": old[key]}
                           if key in old else {"op": "unset", "path": [key]}
                           for key in ("provider", "model", "reasoningEffort")]
                    try:
                        self._mutate(DEFAULT_NS, ops, revision)
                    except Exception:
                        rollback_messages.append("默认模型回滚未完成，请刷新核查")
                if provider_undo:
                    route, old, revision = provider_undo
                    op = ({"op": "set", "path": ["providers", route], "value": old}
                          if isinstance(old, dict) else
                          {"op": "unset", "path": ["providers", route]})
                    try:
                        self._mutate(LLM_NS, [op], revision)
                        provider_restored = True
                    except Exception:
                        rollback_messages.append("提供方回滚未完成，请刷新核查")
                if created_credential_ref:
                    if provider_restored:
                        try:
                            doc = self._describe()
                            view = self._view(doc, LLM_NS)
                            refs = {profile.get("apiKeyEnv") for profile in
                                    ((view.get("value") or {}).get("providers") or {}).values()
                                    if isinstance(profile, dict)}
                            native = next((x for x in doc["namespaces"] if isinstance(x, dict)
                                           and x.get("ns") == "llm-deepseek"), None)
                            if native and isinstance(native.get("value"), dict):
                                refs.add(native["value"].get("apiKeyEnv"))
                            if created_credential_ref not in refs:
                                self._rpc("credentials/unset", {"ref": created_credential_ref})
                            else:
                                rollback_messages.append("本次创建的凭据仍被引用，已保留")
                        except Exception:
                            rollback_messages.append("本次创建的未引用凭据未能清理，已保留待核查")
                    else:
                        rollback_messages.append("本次创建的凭据随未完成的提供方回滚保留")
                if dsh_started and not self.dsh.cleanup().get("success"):
                    rollback_messages.append("本次启动的 DSH 未能自动收尾")
                if model_started:
                    try:
                        if not self._backend().stop_model().get("success"):
                            rollback_messages.append("本次启动的模型未能自动收尾")
                    except Exception:
                        rollback_messages.append("本次启动的模型未能自动收尾")
                message = (str(exc) if isinstance(exc, (RuntimeError, _RemoteFailure, _Conflict))
                           else "工作流程发生异常：" + type(exc).__name__)
                if rollback_messages:
                    message += "；" + "；".join(rollback_messages)
                emit("failed", message)
                return _fail(message, "workflow-failed")


class _Conflict(Exception):
    pass


class _RemoteFailure(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
