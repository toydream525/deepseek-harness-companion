"""Manage optional Pi WebUI and DeepSeek Harness Desktop instances.

Pi is exposed here only through the community pi-web-ui browser interface;
this module never starts the Pi CLI/TUI and never edits Pi credentials.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import urllib.request
import webbrowser
from datetime import datetime

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ROOT = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "DSH-Companion"
CONFIG = ROOT / "companion-apps.json"
PI_URL = "http://127.0.0.1:8787/"
PI_PACKAGE = "https://github.com/xing-shuyin/pi-web-ui"
PI_DOCS = "https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/models.md"
DSH_WEB = "https://deepseek.com/harness/"
LOCK = threading.RLock()
OWNED: dict[str, subprocess.Popen] = {}
_PI_API_KEY = ""
APPS = ("pi", "dsh_desktop")


def _result(ok, message, data=None):
    return {"success": ok, "message": message, "data": data}


def _config():
    try:
        value = json.loads(CONFIG.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _pi_pkg():
    root = Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / "pi-web-ui"
    try:
        data = json.loads((root / "package.json").read_text(encoding="utf-8"))
        if data.get("name") == "pi-web-ui":
            return root, str(data.get("version"))
    except (OSError, ValueError):
        pass
    return None, None


def _configured_local_model():
    path = Path(os.environ.get("PI_CODING_AGENT_DIR") or Path.home() / ".pi" / "agent").expanduser() / "models.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        provider = raw.get("providers", {}).get("dsh_companion_local", {})
        models = provider.get("models", [])
        local = next((m for m in models if isinstance(m, dict)), None)
        if (provider.get("api") == "openai-completions"
                and isinstance(provider.get("baseUrl"), str)
                and provider["baseUrl"].startswith("http://127.0.0.1:") and local):
            return {"configured": True, "provider_id": "dsh_companion_local",
                    "model_id": local.get("id"), "display_name": local.get("name"),
                    "base_url": provider["baseUrl"]}
    except (OSError, ValueError, AttributeError):
        pass
    return {"configured": False}


def _dsh_exe():
    cfg = _config().get("dsh_desktop", {})
    candidates = [Path(cfg["executable"])] if isinstance(cfg, dict) and cfg.get("executable") else []
    candidates += [Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "DeepSeek Harness" / "DeepSeek Harness.exe"]
    for p in candidates:
        if p.is_file() and p.name.casefold() == "deepseek harness.exe":
            return p.resolve()
    return None


def _processes(app_id, path=None):
    if os.name != "nt":
        return []
    names = ["DeepSeek Harness.exe"] if app_id == "dsh_desktop" else ["node.exe"]
    try:
        script = "$p=Get-CimInstance Win32_Process | Where-Object { $_.Name -in @(" + ",".join("'"+n+"'" for n in names) + ") }; "
        script += "$p | Select-Object ProcessId,Name,ExecutablePath,CreationDate,CommandLine | ConvertTo-Json -Compress"
        raw = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                             capture_output=True, text=True, timeout=7, creationflags=NO_WINDOW)
        rows = json.loads(raw.stdout) if raw.stdout.strip() else []
        if isinstance(rows, dict): rows = [rows]
        result = []
        for row in rows:
            pid = int(row.get("ProcessId", 0))
            exe = str(row.get("ExecutablePath") or "")
            command = str(row.get("CommandLine") or "")
            if app_id == "dsh_desktop" and path and exe.casefold() != str(path).casefold():
                continue
            if app_id == "pi" and "pi-web-ui" not in command.casefold():
                continue
            proc = OWNED.get(app_id)
            result.append({"pid": pid, "name": row.get("Name"), "executable_path": exe,
                           "creation_time": str(row.get("CreationDate") or ""),
                           "owned": bool(proc and proc.poll() is None and proc.pid == pid)})
        return result
    except (OSError, ValueError, subprocess.SubprocessError, TypeError):
        return []


def _healthy(url=PI_URL):
    try:
        with urllib.request.urlopen(url + "api/health", timeout=1.5) as response:
            return response.status == 200
    except Exception:
        return False


def _ensure_local_provider_locked():
    """Merge the verified active Companion endpoint into Pi's models.json.

    Only the companion-owned provider/model entry is changed. The previous file
    is backed up before an atomic replace. API credentials never enter results
    or logs; when needed Pi reads them from the WebUI process environment.
    """
    global _PI_API_KEY
    try:
        import sys
        manager_dir = str(Path(__file__).resolve().parent)
        if manager_dir not in sys.path:
            sys.path.insert(0, manager_dir)
        import backend
        endpoint = backend.get_active_endpoint()
        if not isinstance(endpoint, dict) or not endpoint.get("success"):
            return _result(False, "The Companion local model API is not ready; Pi settings were not changed.",
                           {"connection": "not_ready"})
        base_url = endpoint.get("base_url")
        model_id = endpoint.get("model_id")
        context = endpoint.get("context_size")
        display_name = None
        try:
            import model_catalog
            catalog_id = endpoint.get("catalog_model_id")
            catalog_row = next((row for row in model_catalog.list_models()
                                if row.get("id") == catalog_id), None)
            if catalog_row:
                display_name = catalog_row.get("display_name") or catalog_row.get("name")
        except (ImportError, OSError, ValueError):
            pass
        display_name = (str(display_name).strip() if display_name else "") or model_id
        if (not isinstance(base_url, str) or not base_url.startswith("http://127.0.0.1:")
                or not isinstance(model_id, str) or not model_id.strip()
                or type(context) is not int or context <= 0):
            return _result(False, "The active endpoint did not provide a verified loopback URL and model descriptor.")
        api_key = backend.get_active_api_key()
        # Confirm that the current model remains served by the endpoint.
        request = urllib.request.Request(base_url.rstrip("/") + "/models",
                                         headers={"Authorization": "Bearer " + api_key} if api_key else {})
        with urllib.request.urlopen(request, timeout=4) as response:
            models_payload = json.loads(response.read().decode("utf-8"))
        available = {row.get("id") for row in models_payload.get("data", []) if isinstance(row, dict)}
        if model_id not in available:
            return _result(False, "The active model was not advertised by its local API; Pi settings were not changed.")

        agent_dir = Path(os.environ.get("PI_CODING_AGENT_DIR") or Path.home() / ".pi" / "agent").expanduser()
        agent_dir.mkdir(parents=True, exist_ok=True)
        models_path = agent_dir / "models.json"
        existing = {}
        if models_path.exists():
            try:
                existing = json.loads(models_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return _result(False, "Pi models.json is not valid JSON; it was left untouched.",
                               {"path": str(models_path)})
            if not isinstance(existing, dict):
                return _result(False, "Pi models.json has an unsupported root value; it was left untouched.")
        providers = existing.setdefault("providers", {})
        if not isinstance(providers, dict):
            return _result(False, "Pi models.json providers value is invalid; it was left untouched.")
        provider_id = "dsh_companion_local"
        prior = providers.get(provider_id, {})
        if not isinstance(prior, dict):
            return _result(False, "The existing companion provider entry is invalid; it was left untouched.")
        models = prior.get("models", [])
        if not isinstance(models, list):
            return _result(False, "The existing companion model list is invalid; it was left untouched.")
        # Keep any other models already placed in this companion-owned provider.
        models = [m for m in models if not (isinstance(m, dict) and m.get("id") == model_id)]
        model = {"id": model_id, "name": display_name,
                 "contextWindow": context, "maxTokens": min(context, 8192), "input": ["text"]}
        models.append(model)
        provider = {**prior, "baseUrl": base_url, "api": "openai-completions",
                    "apiKey": "$DSH_COMPANION_PI_API_KEY" if api_key else "local-no-auth",
                    "models": models}
        providers[provider_id] = provider
        if api_key and _healthy() and _PI_API_KEY != api_key:
            return _result(False, "Pi WebUI is already running without the current local API credential; settings were not changed. Restart it explicitly, then retry.")
        previous_value = json.loads(models_path.read_text(encoding="utf-8")) if models_path.exists() else None
        changed = existing != previous_value
        backup = None
        if not changed:
            return _result(True, "Local model provider is already configured for Pi.",
                           {"provider_id": provider_id, "model_id": model_id,
                            "display_name": display_name,
                            "base_url": base_url, "context_size": context,
                            "models_path": str(models_path), "backup_path": None,
                            "requires_webui_reload": False, "api_key_configured": bool(api_key)})
        _PI_API_KEY = api_key
        if models_path.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            backup = models_path.with_name(f"models.json.dsh-companion-{stamp}.bak")
            shutil.copy2(models_path, backup)
        temporary = models_path.with_suffix(".json.dsh-companion.tmp")
        temporary.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, models_path)
        return _result(True, "Local model provider is configured for Pi.",
                       {"provider_id": provider_id, "model_id": model_id,
                        "display_name": display_name,
                        "base_url": base_url, "context_size": context,
                        "models_path": str(models_path), "backup_path": str(backup) if backup else None,
                        "requires_webui_reload": True, "api_key_configured": bool(api_key)})
    except Exception as exc:
        _PI_API_KEY = ""
        return _result(False, "Could not safely configure the local provider: " + type(exc).__name__)


def ensure_local_provider():
    """Public, idempotent model-connection API for the explicit Pi action."""
    with LOCK:
        return _ensure_local_provider_locked()


def connect_model():
    """Backward-compatible action name for ensuring the local provider."""
    return ensure_local_provider()


def status():
    pi_root, pi_version = _pi_pkg()
    pi_ok = bool(pi_root and _healthy())
    model_connection = _configured_local_model()
    desktop = _dsh_exe()
    dsh_instances = _processes("dsh_desktop", desktop)
    pi_instances = _processes("pi")
    if pi_ok and not pi_instances:
        proc = OWNED.get("pi")
        if proc and proc.poll() is None:
            pi_instances = [{"pid": proc.pid, "name": "node.exe", "executable_path": str(Path(shutil.which("node.exe") or "node.exe").resolve()), "creation_time": "", "owned": True}]
    data = {
        "pi": {"app_id": "pi", "name": "Pi WebUI (community)", "installed": pi_root is not None,
               "version": pi_version, "running": pi_ok, "owned": any(x["owned"] for x in pi_instances),
               "instances": pi_instances, "capabilities": {"webui": pi_ok, "openai_compatible_provider": True},
               "webui_url": PI_URL if pi_ok else None, "url": PI_URL, "source_url": PI_PACKAGE,
               "provider_docs": PI_DOCS,
               "connection": "configured" if model_connection["configured"] else "not_configured",
               "model_connection": model_connection},
        "dsh_desktop": {"app_id": "dsh_desktop", "name": "DeepSeek Harness Desktop", "installed": desktop is not None,
               "path": str(desktop) if desktop else None, "version": None, "running": bool(dsh_instances),
               "owned": any(x["owned"] for x in dsh_instances), "instances": dsh_instances,
               "capabilities": {"official_desktop_gui": True}, "url": str(desktop) if desktop else None,
               "source_url": "https://github.com/deepseek-ai/deepseek-harness/tree/master/apps/desktop",
               "provider_docs": "https://github.com/deepseek-ai/deepseek-harness",
               "connection": "manual provider configuration required"},
    }
    return _result(True, "Optional application status", data)


def configure(app_id, executable=None):
    if app_id != "dsh_desktop" or not executable:
        return _result(False, "Pi WebUI is located from its npm installation; only the Desktop executable path can be configured.")
    path = Path(executable).expanduser().resolve()
    if not path.is_file() or path.name.casefold() != "deepseek harness.exe":
        return _result(False, "Executable is not a DeepSeek Harness Desktop application.")
    cfg = _config(); cfg[app_id] = {"executable": str(path)}
    ROOT.mkdir(parents=True, exist_ok=True); CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return _result(True, "Saved executable path", {"app_id": app_id, "path": str(path)})


def start(app_id):
    if app_id == "dsh_web":
        return _result(False, "Harness Web is managed through the existing DSHController integration in the main window.")
    current = status()["data"].get(app_id)
    if not current:
        return _result(False, "Unknown optional application", {"app_id": app_id})
    if app_id == "pi":
        root, _ = _pi_pkg()
        if not root:
            return _result(False, "Community Pi WebUI npm package is not installed.", current)
        connection = ensure_local_provider()
        if not connection.get("success"):
            return connection
        if _healthy():
            webbrowser.open(PI_URL)
            return _result(True, "Opened existing Pi WebUI; provider is available after the WebUI reloads its model list.",
                           {**current, "connection": "configured", "model": connection.get("data")})
        node = shutil.which("node.exe") or shutil.which("node")
        if not node:
            return _result(False, "Node.js was not found.", current)
        entry = root / "bin" / "pi-web-ui.mjs"
        env = dict(os.environ, PI_WEB_HOST="127.0.0.1", PI_WEB_PORT="8787")
        if _PI_API_KEY:
            env["DSH_COMPANION_PI_API_KEY"] = _PI_API_KEY
        log = ROOT / "logs" / "pi-web-ui.log"; log.parent.mkdir(parents=True, exist_ok=True)
        stream = log.open("ab")
        try:
            proc = subprocess.Popen([node, str(entry), "--host", "127.0.0.1", "--port", "8787", "--no-browser"],
                                    cwd=str(Path.cwd()), env=env, stdin=subprocess.DEVNULL,
                                    stdout=stream, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
        except OSError as exc:
            stream.close(); return _result(False, "Unable to launch Pi WebUI: " + type(exc).__name__)
        OWNED["pi"] = proc
        deadline = time.time() + 25
        while time.time() < deadline and proc.poll() is None and not _healthy(): time.sleep(.25)
        stream.close()
        if not _healthy(): return _result(False, "Pi WebUI did not become healthy; see its local log.", {"pid": proc.pid})
        webbrowser.open(PI_URL)
        return _result(True, "Started Pi WebUI, configured the local model, and opened its browser page",
                       {**status()["data"]["pi"], "connection": "configured", "model": connection.get("data")})
    if app_id == "dsh_desktop":
        if current["running"]:
            for item in current["instances"]:
                _activate(int(item["pid"]))
            return _result(True, "Activated existing DeepSeek Harness Desktop", current)
        path = current.get("path")
        if not path: return _result(False, "DeepSeek Harness Desktop is not installed.", current)
        proc = subprocess.Popen([path], cwd=str(Path(path).parent), creationflags=NO_WINDOW)
        OWNED[app_id] = proc
        return _result(True, "Started DeepSeek Harness Desktop", {"pid": proc.pid, "owned": True})
    return _result(False, "Unknown optional application", {"app_id": app_id})


def open_app(app_id):
    if app_id == "pi":
        if not _healthy(): return start(app_id)
        webbrowser.open(PI_URL); return _result(True, "Opened Pi WebUI", {"webui_url": PI_URL})
    return start(app_id)


def _activate(pid):
    if os.name != "nt": return False
    try:
        import ctypes
        user = ctypes.windll.user32; target = [None]
        callback = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def visit(hwnd, _):
            owner = ctypes.c_ulong(); user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid and user.IsWindowVisible(hwnd): target[0] = hwnd; return False
            return True
        user.EnumWindows(callback(visit), 0)
        return bool(target[0] and (user.ShowWindowAsync(target[0], 9) or user.SetForegroundWindow(target[0])))
    except Exception: return False


def force_stop(app_id, expected_instance=None):
    if app_id == "dsh_web":
        return _result(False, "Harness Web lifecycle is controlled by the existing DSHController integration.")
    if not isinstance(expected_instance, dict):
        return _result(False, "Select an instance from status and confirm before force-closing.", status()["data"].get(app_id))
    pid = int(expected_instance.get("pid", 0)); path = str(expected_instance.get("executable_path", ""))
    created = str(expected_instance.get("creation_time", ""))
    item = next((x for x in status()["data"].get(app_id, {}).get("instances", []) if x["pid"] == pid), None)
    if not item or item["executable_path"].casefold() != path.casefold() or item["creation_time"] != created or not created:
        return _result(False, "Process identity changed or could not be verified; nothing was closed.", status()["data"].get(app_id))
    created_ms = int(created.split("(", 1)[1].split(")", 1)[0]) if created.startswith("/Date(") else -1
    escaped_path = path.replace("'", "''")
    check = (f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId={pid}'; "
             f"if ($p -and $p.ExecutablePath -ieq '{escaped_path}') {{ "
             f"$ms=[DateTimeOffset]([Management.ManagementDateTimeConverter]::ToDateTime($p.CreationDate).ToUniversalTime()).ToUnixTimeMilliseconds(); "
             f"if ($ms -eq {created_ms}) {{ Stop-Process -Id {pid} -Force; exit 0 }} }}; exit 2")
    run = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", check], capture_output=True, timeout=8, creationflags=NO_WINDOW)
    if run.returncode: return _result(False, "Identity check failed; nothing was closed.", {"pid": pid})
    return _result(True, "Force-closed the selected verified instance", {"app_id": app_id, "pid": pid})


def stop(app_id, expected_instance=None):
    return force_stop(app_id, expected_instance)
