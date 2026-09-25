# -*- coding: utf-8 -*-
"""
Qwen3.8-27B Local Model Manager - Backend Core Engine
Handles process lifecycle, hardware metrics, LAN IP detection, configurations, and script execution.
"""

import os
import sys
import json
import time
import socket
import ctypes
import shutil
import secrets
import re
import ipaddress
import math
import hashlib
import functools
import tempfile
import shlex
import subprocess
import threading
from contextlib import contextmanager
from collections import deque
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
import urllib.request
import urllib.error
import lifecycle
from app_paths import INSTALL_ROOT, DATA_ROOT, LEGACY_ROOT

# Base and Manager directories
BASE_DIR = INSTALL_ROOT
BUNDLE_DIR = Path(getattr(sys, '_MEIPASS', BASE_DIR / "manager"))
MANAGER_DIR = DATA_ROOT / "manager"
LEGACY_BASE_DIR = LEGACY_ROOT
LEGACY_MANAGER_DIR = LEGACY_BASE_DIR / "manager"
CONFIG_FILE = MANAGER_DIR / "config.json"
BACKUP_FILE = MANAGER_DIR / "config.backup.json"
LOGS_DIR = DATA_ROOT / "logs"
SCRIPTS_DIR = BASE_DIR / "scripts"
ACTIVE_LOG_FILE = LOGS_DIR / "llama-server-active.log"
PROCESS_FILE = MANAGER_DIR / "model-process.json"
UI_OWNER_FILE = MANAGER_DIR / "ui-owner.json"
LEGACY_UI_OWNER_FILE = LEGACY_MANAGER_DIR / "ui-owner.json"
UI_JOB_NAME = "Local\\DSHCompanionUIJob_" + hashlib.sha256(str(BASE_DIR).casefold().encode("utf-8")).hexdigest()[:24]
LEGACY_UI_JOB_NAME = "Local\\Qwen38UIJob_" + hashlib.sha256(str(LEGACY_BASE_DIR).casefold().encode("utf-8")).hexdigest()[:24]
MAX_LOG_BYTES = 16 * 1024 * 1024
_config_lock = threading.RLock()
_process_query_lock = threading.Lock()
_process_query_cache: Dict[int, Tuple[float, Optional[Dict[str, Any]]]] = {}
_network_cache: Tuple[float, Dict[str, Any]] = (0.0, {})
_adopt_retry_after = 0.0
_model_instances_cache: Tuple[float, List[Dict[str, Any]]] = (0.0, [])
_legacy_gui_cache: Tuple[float, List[Dict[str, Any]]] = (0.0, [])

try:
    MANAGER_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass

# Global process tracker
_model_process: Optional[subprocess.Popen] = None
_model_process_created_at: Optional[str] = None
_ui_job_handle: Optional[int] = None
_ui_owner_created_at: Optional[str] = None
_readonly_ui_session = False
_model_process_lock = threading.RLock()
_last_start_error = ""
_last_exit_code: Optional[int] = None
_stopping_model = False
_PRESET_UNSPECIFIED = object()
_active_script_task: Optional[Dict[str, Any]] = None
_active_script_lock = threading.Lock()


@contextmanager
def _lifecycle_guard():
    """Serialize manager lifecycle operations across GUI, CLI and helper processes."""
    with _model_process_lock:
        if os.name != "nt":
            yield
            return
        kernel = ctypes.windll.kernel32
        kernel.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
        kernel.CreateMutexW.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
        kernel.WaitForSingleObject.restype = ctypes.c_uint32
        kernel.ReleaseMutex.argtypes = (ctypes.c_void_p,)
        kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
        name = "Local\\Qwen38Manager_" + hashlib.sha256(str(LEGACY_BASE_DIR).casefold().encode("utf-8")).hexdigest()[:24]
        handle = kernel.CreateMutexW(None, False, name)
        if not handle:
            raise OSError("无法创建模型生命周期锁")
        acquired = False
        try:
            outcome = kernel.WaitForSingleObject(handle, 30000)
            if outcome not in (0, 0x80):
                raise TimeoutError("等待模型生命周期锁超时")
            acquired = True
            yield
        finally:
            if acquired:
                kernel.ReleaseMutex(handle)
            kernel.CloseHandle(handle)

# Windows MEMORYSTATUSEX structure for RAM query
class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ('dwLength', ctypes.c_ulong),
        ('dwMemoryLoad', ctypes.c_ulong),
        ('ullTotalPhys', ctypes.c_ulonglong),
        ('ullAvailPhys', ctypes.c_ulonglong),
        ('ullTotalPageFile', ctypes.c_ulonglong),
        ('ullAvailPageFile', ctypes.c_ulonglong),
        ('ullTotalVirtual', ctypes.c_ulonglong),
        ('ullAvailVirtual', ctypes.c_ulonglong),
        ('sullAvailExtendedVirtual', ctypes.c_ulonglong)
    ]


def get_system_ram() -> Dict[str, Any]:
    """Query Host RAM usage using native Windows API."""
    try:
        ms = MEMORYSTATUSEX()
        ms.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
            total_gb = round(ms.ullTotalPhys / (1024 ** 3), 2)
            avail_gb = round(ms.ullAvailPhys / (1024 ** 3), 2)
            used_gb = round(total_gb - avail_gb, 2)
            percent = int(ms.dwMemoryLoad)
            return {
                "total_gb": total_gb,
                "used_gb": used_gb,
                "free_gb": avail_gb,
                "percent": percent
            }
    except Exception as e:
        pass
    return {"total_gb": None, "used_gb": None, "free_gb": None, "percent": None, "ok": False}


def get_gpu_metrics() -> Dict[str, Any]:
    """Query NVIDIA GPU metrics via pynvml, fallback to nvidia-smi."""
    # Attempt 1: pynvml
    try:
        import pynvml
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        name = pynvml.nvmlDeviceGetName(handle)
        if isinstance(name, bytes):
            name = name.decode('utf-8')
        mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
        total_gb = round(mem_info.total / (1024 ** 3), 2)
        used_gb = round(mem_info.used / (1024 ** 3), 2)
        free_gb = round(mem_info.free / (1024 ** 3), 2)
        percent = round((used_gb / total_gb) * 100, 1) if total_gb > 0 else 0
        
        try:
            util = pynvml.nvmlDeviceGetUtilizationRates(handle).gpu
        except Exception:
            util = 0
            
        try:
            temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
        except Exception:
            temp = 0
            
        return {
            "name": name,
            "total_gb": total_gb,
            "used_gb": used_gb,
            "free_gb": free_gb,
            "percent": percent,
            "util_percent": util,
            "temp_c": temp,
            "ok": True
        }
    except Exception:
        pass

    # Attempt 2: nvidia-smi fallback
    try:
        cmd = ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu", "--format=csv,noheader,nounits"]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        parts = [p.strip() for p in res.stdout.strip().split(",")]
        name = parts[0]
        total_gb = round(float(parts[1]) / 1024, 2)
        used_gb = round(float(parts[2]) / 1024, 2)
        free_gb = round(float(parts[3]) / 1024, 2)
        percent = round((used_gb / total_gb) * 100, 1) if total_gb > 0 else 0
        util = int(parts[4])
        temp = int(parts[5])
        return {
            "name": name,
            "total_gb": total_gb,
            "used_gb": used_gb,
            "free_gb": free_gb,
            "percent": percent,
            "util_percent": util,
            "temp_c": temp,
            "ok": True
        }
    except Exception:
        return {
            "name": "未知 GPU",
            "total_gb": None,
            "used_gb": None,
            "free_gb": None,
            "percent": None,
            "util_percent": None,
            "temp_c": None,
            "ok": False
        }


def get_network_ips() -> Dict[str, Any]:
    """Detect local IP addresses, prioritizing real LAN addresses (192.168.*, 10.*, etc.)."""
    global _network_cache
    if time.monotonic() - _network_cache[0] < 10 and _network_cache[1]:
        return dict(_network_cache[1])
    lan_ips = []
    try:
        cmd = ["powershell", "-NoProfile", "-Command", "Get-NetIPAddress -AddressFamily IPv4 | Select-Object -ExpandProperty IPAddress"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=3,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for line in res.stdout.splitlines():
            ip = line.strip()
            if not ip or ip.startswith("127.") or ip.startswith("169.254."):
                continue
            lan_ips.append(ip)
    except Exception:
        pass

    # Sort so 192.168.* or 10.* come first
    def ip_priority(ip: str) -> int:
        if ip.startswith("192.168."):
            return 1
        if ip.startswith("10."):
            return 2
        if ip.startswith("172."):
            return 3
        if ip.startswith("100."):  # Tailscale
            return 4
        return 5

    lan_ips = sorted(list(set(lan_ips)), key=ip_priority)
    primary_ip = lan_ips[0] if lan_ips else "127.0.0.1"

    result = {
        "primary_ip": primary_ip,
        "all_ips": lan_ips,
        "localhost": "127.0.0.1"
    }
    _network_cache = (time.monotonic(), result)
    return dict(result)


def _atomic_json(path: Path, value: Dict[str, Any]) -> None:
    """Replace one JSON file without sharing a fixed temporary filename."""
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(value, out, ensure_ascii=False, indent=2)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _valid_config(cfg: Any) -> bool:
    if not isinstance(cfg, dict) or not isinstance(cfg.get("profiles"), dict) or not cfg["profiles"]:
        return False
    def text_field(value: Any, required: bool = False) -> bool:
        return isinstance(value, str) and len(value) <= 2048 and (bool(value.strip()) or not required)
    def integer(value: Any, low: int, high: int) -> bool:
        return type(value) is int and low <= value <= high
    def number(value: Any, low: float, high: float) -> bool:
        return type(value) in (int, float) and math.isfinite(value) and low <= value <= high
    if not integer(cfg.get("default_port"), 1, 65535) or not integer(cfg.get("manager_port"), 1, 65535):
        return False
    if not text_field(cfg.get("server_executable")):
        return False
    for flag in ("autostart_model_on_manager_open", "autostart_windows"):
        if type(cfg.get(flag)) is not bool:
            return False
    lan, key = cfg.get("lan_access"), cfg.get("api_key")
    if not isinstance(lan, dict) or not isinstance(key, dict):
        return False
    if type(lan.get("enabled")) is not bool or type(key.get("enabled")) is not bool or not text_field(key.get("key")):
        return False
    selected_ip = lan.get("selected_ip", "")
    if not text_field(selected_ip):
        return False
    if selected_ip:
        try:
            address = ipaddress.IPv4Address(selected_ip)
            if address.is_loopback or address.is_multicast or address.is_unspecified:
                return False
        except ipaddress.AddressValueError:
            return False
    if lan["enabled"] and not (key["enabled"] and key["key"]):
        return False
    if cfg.get("active_profile") not in cfg["profiles"]:
        return False
    int_limits = {"ctx_size": (128, 1048576), "ngl": (-1, 999), "batch_size": (1, 8192),
                  "ubatch_size": (1, 8192), "parallel": (1, 128), "reasoning_budget": (0, 1048576),
                  "top_k": (0, 1000)}
    float_limits = {"temp": (0, 5), "top_p": (0, 1), "min_p": (0, 1),
                    "presence_penalty": (-2, 2)}
    for profile_id, profile in cfg["profiles"].items():
        if not text_field(profile_id, True) or not isinstance(profile, dict):
            return False
        if profile.get("id") != profile_id:
            return False
        legacy = profile.get("verified_legacy_qwen") is True
        for field in ("name", "alias", "model_path"):
            if not text_field(profile.get(field), True):
                return False
        if not text_field(profile.get("template_path", ""), required=legacy):
            return False
        for field, bounds in int_limits.items():
            if (legacy or field in profile) and not integer(profile.get(field), *bounds):
                return False
        for field, bounds in float_limits.items():
            if (legacy or field in profile) and not number(profile.get(field), *bounds):
                return False
        for field in ("flash_attn", "jinja"):
            if (legacy or field in profile) and type(profile.get(field)) is not bool:
                return False
        for field in ("cache_type_k", "cache_type_v", "reasoning_format", "reasoning_effort"):
            if (legacy or field in profile) and not text_field(profile.get(field), True):
                return False
        if legacy and profile["reasoning_effort"] not in ("off", "low", "medium", "high"):
            return False
    return True


def migrate_legacy_config() -> Dict[str, Any]:
    """One-time, idempotent migration; the legacy installation is read-only."""
    with _config_lock:
        if CONFIG_FILE.exists() or BACKUP_FILE.exists():
            return {"success": True, "migrated": False, "message": "独立配置已存在"}
        source = LEGACY_MANAGER_DIR / "config.json"
        try:
            legacy = json.loads(source.read_text(encoding="utf-8"))
            if not _valid_config(legacy):
                return {"success": False, "migrated": False, "message": "旧配置结构无效，未迁移"}
            cfg = json.loads(json.dumps(legacy, ensure_ascii=False))
            def absolute_old(value: str) -> str:
                path = Path(value)
                return str((path if path.is_absolute() else LEGACY_BASE_DIR / path).resolve())
            cfg["server_executable"] = absolute_old(cfg["server_executable"])
            for profile in cfg["profiles"].values():
                profile["model_path"] = absolute_old(profile["model_path"])
                if profile.get("template_path"):
                    profile["template_path"] = absolute_old(profile["template_path"])
                profile["verified_legacy_qwen"] = profile.get("id") in {
                    "uncensored_32k", "original_32k", "writing_8k",
                    "uncensored_64k", "uncensored_direct_32k"}
            cfg["schema_version"] = 2
            cfg["legacy_source"] = str(LEGACY_BASE_DIR)
            cfg["autostart_windows"] = False
            cfg["autostart_model_on_manager_open"] = False
            _atomic_json(BACKUP_FILE, cfg)
            _atomic_json(CONFIG_FILE, cfg)
            return {"success": True, "migrated": True, "message": "旧配置已迁移为独立绝对路径引用"}
        except (OSError, ValueError, TypeError):
            return {"success": False, "migrated": False, "message": "无法读取或保存旧配置"}


def _fresh_config() -> Dict[str, Any]:
    """Usable first-run state without an installed engine or model weight."""
    return {
        "version": "0.2.0", "schema_version": 2,
        "active_profile": "select_model", "autostart_model_on_manager_open": False,
        "autostart_windows": False, "server_executable": "",
        "minimize_to_tray": False, "close_action": "ask",
        "default_port": 24548, "manager_port": 24549,
        "lan_access": {"enabled": False, "require_api_key": True, "selected_ip": ""},
        "api_key": {"enabled": False, "key": ""},
        "profiles": {"select_model": {
            "id": "select_model", "name": "请选择模型", "alias": "select-model",
            "model_path": "models/select-a-model.gguf", "template_path": "",
        }},
    }


def load_config() -> Dict[str, Any]:
    """Load configuration from config.json, fallback to backup or defaults."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                if _valid_config(cfg):
                    return cfg
        except Exception:
            pass

    if BACKUP_FILE.exists():
        try:
            with open(BACKUP_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                if _valid_config(cfg):
                    # Do not copy a corrupt primary over the good backup.
                    with _config_lock:
                        _atomic_json(CONFIG_FILE, cfg)
                    return cfg
        except Exception:
            pass

    fresh = _fresh_config()
    if _valid_config(fresh):
        with _config_lock:
            if not CONFIG_FILE.exists() and not BACKUP_FILE.exists():
                try:
                    _atomic_json(CONFIG_FILE, fresh)
                except OSError:
                    pass
        return fresh
    return {}


def save_config(cfg: Dict[str, Any]) -> bool:
    """Save configuration atomically with backup protection."""
    try:
        if not _valid_config(cfg):
            return False
        with _config_lock:
            if CONFIG_FILE.exists():
                try:
                    old = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                    if _valid_config(old):
                        _atomic_json(BACKUP_FILE, old)
                except (OSError, ValueError):
                    pass
            _atomic_json(CONFIG_FILE, cfg)
        return True
    except Exception:
        return False


def is_port_listening(host: str = "127.0.0.1", port: int = 24548, timeout: float = 0.5) -> bool:
    """Check if TCP port is active."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        s.close()
        return True
    except Exception:
        return False


def query_llama_server_status(port: int = 24548, api_key: str = "") -> Tuple[bool, Dict[str, Any]]:
    """Query llama-server /v1/models to verify operational health."""
    url = f"http://127.0.0.1:{port}/v1/models"
    headers = {"User-Agent": "Qwen38-Manager/1.0"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                return True, data
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return False, {"auth_required": True, "code": 401}
    except Exception:
        pass
    return False, {}


def _command_args(command_line: str) -> List[str]:
    if os.name == "nt":
        count = ctypes.c_int()
        ctypes.windll.shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
        ptr = ctypes.windll.shell32.CommandLineToArgvW(command_line, ctypes.byref(count))
        if ptr:
            try:
                return [ptr[i] for i in range(count.value)]
            finally:
                ctypes.windll.kernel32.LocalFree(ptr)
    return shlex.split(command_line)


def _query_pid(pid: int, force: bool = False) -> Optional[Dict[str, Any]]:
    """Read one process identity, with a short cache for dashboard polling."""
    global _process_query_cache
    with _process_query_lock:
        cached = _process_query_cache.get(pid)
        if not force and cached and time.monotonic() - cached[0] < 1.5:
            return cached[1]
        try:
            command = (f'$p = Get-CimInstance Win32_Process -Filter "ProcessId = {int(pid)}"; '
                       'if ($p) { [pscustomobject]@{ ProcessId=$p.ProcessId; '
                       'ExecutablePath=$p.ExecutablePath; CommandLine=$p.CommandLine; '
                       "CreationDate=$p.CreationDate.ToUniversalTime().ToString('o') "
                       '} | ConvertTo-Json -Compress }')
            res = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command],
                                 capture_output=True, text=True, timeout=5,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            row = json.loads(res.stdout) if res.returncode == 0 and res.stdout.strip() else None
        except Exception:
            row = None
        _process_query_cache[pid] = (time.monotonic(), row)
        if len(_process_query_cache) > 16:
            _process_query_cache = {pid: _process_query_cache[pid]}
        return row


def _read_process_record() -> Optional[Dict[str, Any]]:
    try:
        record = json.loads(PROCESS_FILE.read_text(encoding="utf-8"))
        if isinstance(record, dict) and type(record.get("pid")) is int:
            return record
    except (OSError, ValueError):
        pass
    return None


def _verified_process(force: bool = False) -> Optional[Dict[str, Any]]:
    record = _read_process_record()
    if not record:
        return None
    row = _query_pid(record["pid"], force=force)
    if not row or row.get("ProcessId") != record["pid"] or row.get("CreationDate") != record.get("created_at"):
        return None
    try:
        if Path(row["ExecutablePath"]).resolve() != Path(record["executable"]).resolve():
            return None
        args = _command_args(row["CommandLine"] or "")
        model_index, port_index = args.index("--model"), args.index("--port")
        if (Path(args[model_index + 1]).resolve() != Path(record["model"]).resolve()
                or int(args[port_index + 1]) != record["port"]):
            return None
    except (ValueError, IndexError, TypeError, KeyError, OSError):
        return None
    return row


def _live_ui_owner(force: bool = False) -> Optional[Dict[str, Any]]:
    for file, expected_job, scope in ((UI_OWNER_FILE, UI_JOB_NAME, "companion"),
                                      (LEGACY_UI_OWNER_FILE, LEGACY_UI_JOB_NAME, "legacy")):
        try:
            record = json.loads(file.read_text(encoding="utf-8"))
            if (not isinstance(record, dict) or type(record.get("pid")) is not int
                    or record.get("job_name") != expected_job):
                continue
            row = _query_pid(record["pid"], force=force)
            if row and row.get("CreationDate") == record.get("created_at"):
                return {**record, "record_scope": scope}
        except (OSError, ValueError):
            pass
    return None


def _legacy_gui_instances(force: bool = False) -> List[Dict[str, Any]]:
    """Find older Qwen windows that predate the owner record, by exact old-project path."""
    global _legacy_gui_cache
    if not force and time.monotonic() - _legacy_gui_cache[0] < 4:
        return [dict(row) for row in _legacy_gui_cache[1]]
    found: List[Dict[str, Any]] = []
    try:
        command = ('Get-CimInstance Win32_Process -Filter "Name LIKE \'Qwen38-Manager%.exe\'" | '
                   'ForEach-Object { [pscustomobject]@{ ProcessId=$_.ProcessId; '
                   'ExecutablePath=$_.ExecutablePath; CommandLine=$_.CommandLine; '
                   "CreationDate=$_.CreationDate.ToUniversalTime().ToString('o') "
                   '} } | ConvertTo-Json -Compress')
        result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command],
                                capture_output=True, text=True, timeout=6,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        rows = json.loads(result.stdout) if result.returncode == 0 and result.stdout.strip() else []
        if isinstance(rows, dict):
            rows = [rows]
        for row in rows:
            path_text = row.get("ExecutablePath") or ""
            if not path_text or not row.get("CreationDate"):
                continue
            exe = Path(path_text).resolve()
            if not exe.is_relative_to(LEGACY_BASE_DIR.resolve()):
                continue
            if not re.fullmatch(r"Qwen38-Manager[^\\/]*\.exe", exe.name, re.IGNORECASE):
                continue
            args = _command_args(row.get("CommandLine") or "")
            if any(flag in args for flag in ("--start-profile", "--stop-model")):
                continue
            found.append({"pid": int(row["ProcessId"]), "created_at": row["CreationDate"],
                          "executable": str(exe)})
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        pass
    _legacy_gui_cache = (time.monotonic(), found)
    return [dict(row) for row in found]


def _control_block(force: bool = False) -> Optional[str]:
    owner = _live_ui_owner(force=force)
    if owner and (owner.get("application") != "dsh-companion" or owner.get("record_scope") == "legacy"):
        return "The legacy manager window owns model control. Close it before using Companion controls."
    older_windows = _legacy_gui_instances(force=force)
    if older_windows:
        pids = ", ".join(str(row["pid"]) for row in older_windows)
        return f"A legacy Qwen manager window is running (PID {pids}). Close it before using Companion controls."
    if _readonly_ui_session:
        return "This Companion window began read-only; reopen it after closing the legacy manager."
    return None


def _clear_ui_owner_if_current() -> None:
    try:
        record = json.loads(UI_OWNER_FILE.read_text(encoding="utf-8"))
        if record.get("pid") == os.getpid() and record.get("created_at") == _ui_owner_created_at:
            UI_OWNER_FILE.unlink(missing_ok=True)
    except (OSError, ValueError, AttributeError):
        pass


def _open_owner_job_for_launch() -> Tuple[Optional[int], bool]:
    owner = _live_ui_owner(force=True)
    if not owner:
        return None, False
    if owner.get("application") != "dsh-companion":
        raise RuntimeError("A legacy manager owns the model session")
    if owner["pid"] == os.getpid() and _ui_job_handle:
        return _ui_job_handle, False
    return lifecycle.open_job(UI_JOB_NAME), True


def _ensure_ui_job_assignment(row: Dict[str, Any]) -> bool:
    """Attach a verified legacy process if a live UI session owns the model."""
    owner = _live_ui_owner()
    if not owner or owner.get("application") != "dsh-companion":
        return False
    borrowed = False
    handle = _ui_job_handle if owner["pid"] == os.getpid() and _ui_job_handle else None
    try:
        if handle is None:
            handle = lifecycle.open_job(UI_JOB_NAME)
            borrowed = True
        if not lifecycle.is_process_in_job(handle, int(row["ProcessId"])):
            lifecycle.assign_process(handle, int(row["ProcessId"]))
        return lifecycle.is_process_in_job(handle, int(row["ProcessId"]))
    except (OSError, RuntimeError):
        return False
    finally:
        if borrowed:
            lifecycle.close_handle(handle)


def begin_ui_session() -> Dict[str, Any]:
    """Claim the long-lived UI owner job and adopt an already verified model."""
    global _ui_job_handle, _ui_owner_created_at, _readonly_ui_session
    with _lifecycle_guard():
        block = _control_block(force=True)
        if block:
            _readonly_ui_session = True
            return {"success": True, "message": block, "crash_protected": False,
                    "control_read_only": True, "read_only_reason": block}
        if _ui_job_handle:
            return {"success": True, "message": "管理窗口已持有模型归属", "crash_protected": True}
        owner = _live_ui_owner(force=True)
        if owner:
            return {"success": False, "message": "另一个管理窗口已持有模型归属"}
        instances = _scan_model_instances(force=True)
        duplicates = len(instances) > 1
        row = _query_pid(os.getpid(), force=True)
        if not row or not row.get("CreationDate"):
            return {"success": False, "message": "无法核实管理窗口身份，未启用异常退出保护"}
        try:
            handle = lifecycle.create_job(UI_JOB_NAME)
            _atomic_json(UI_OWNER_FILE, {"pid": os.getpid(), "created_at": row["CreationDate"],
                                         "job_name": UI_JOB_NAME, "application": "dsh-companion"})
            _ui_job_handle = handle
            _ui_owner_created_at = row["CreationDate"]
            existing = [] if duplicates else get_owned_llama_processes()
            if existing and not lifecycle.is_process_in_job(handle, int(existing[0]["ProcessId"])):
                lifecycle.assign_process(handle, int(existing[0]["ProcessId"]))
                if not lifecycle.is_process_in_job(handle, int(existing[0]["ProcessId"])):
                    raise RuntimeError("Verified model could not join the Companion owner job")
            message = (f"检测到 {len(instances)} 个本项目模型进程，请处理重复实例后再接管"
                       if duplicates else "管理窗口已接管模型生命周期")
            return {"success": True, "message": message,
                    "crash_protected": bool(existing), "duplicates_detected": duplicates}
        except (OSError, RuntimeError) as exc:
            _clear_ui_owner_if_current()
            if "handle" in locals():
                lifecycle.close_handle(handle)
            _ui_job_handle = None
            _ui_owner_created_at = None
            return {"success": False, "message": f"无法启用模型异常退出保护: {exc}"}


def end_ui_session() -> Dict[str, Any]:
    """Stop the verified model before releasing the UI owner's job handle."""
    global _ui_job_handle, _ui_owner_created_at, _readonly_ui_session
    with _lifecycle_guard():
        if not _ui_job_handle:
            if _readonly_ui_session:
                _readonly_ui_session = False
                return {"success": True, "message": "Read-only window closed; legacy manager retains its model",
                        "control_read_only": True}
            return {"success": False, "message": "当前管理窗口没有模型归属会话"}
        stopped = stop_model()
        if not stopped.get("success"):
            return stopped
        remaining = _scan_model_instances(force=True)
        if remaining:
            return {"success": False, "message": f"仍有 {len(remaining)} 个本项目模型进程在运行，请确认目标并显式结束后再关闭",
                    "model_instances": remaining}
        _clear_ui_owner_if_current()
        if _ui_job_handle:
            lifecycle.close_handle(_ui_job_handle)
            _ui_job_handle = None
            _ui_owner_created_at = None
        return {"success": True, "message": "模型已停止，可以关闭管理窗口"}


def _clear_process_record() -> None:
    try:
        PROCESS_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _adopt_project_process() -> Optional[Dict[str, Any]]:
    """Adopt one unambiguous project script instance."""
    instances = _scan_model_instances(force=True)
    if len(instances) != 1:
        return None
    item = instances[0]
    _atomic_json(PROCESS_FILE, {
        "pid": item["pid"], "created_at": item["created_at"],
        "executable": item["executable"], "model": item["model"],
        "port": item["port"], "profile": item["profile"],
        "host": item["host"], "key_fingerprint": "", "adopted": True
    })
    return _query_pid(item["pid"], force=True)


def _scan_model_instances(force: bool = False) -> List[Dict[str, Any]]:
    global _model_instances_cache
    if not force and time.monotonic() - _model_instances_cache[0] < 3:
        return [dict(item) for item in _model_instances_cache[1]]
    cfg = load_config()
    if not _valid_config(cfg):
        return []
    exe = (BASE_DIR / cfg["server_executable"]).resolve()
    models = {(BASE_DIR / profile["model_path"]).resolve()
              for profile in cfg["profiles"].values()}
    try:
        import model_catalog
        models.update(Path(item["path"]).resolve() for item in model_catalog.list_models() if item.get("startable"))
    except (ImportError, OSError, ValueError):
        pass
    instances = []
    try:
        names = {"llama-server.exe", exe.name.lower()}
        if any(not re.fullmatch(r"[a-zA-Z0-9_.-]+", name) for name in names):
            return []
        where = " OR ".join(f"Name = '{name.replace(chr(39), chr(39) * 2)}'" for name in sorted(names))
        command = (f'Get-CimInstance Win32_Process -Filter "{where}" | '
                   'ForEach-Object { [pscustomobject]@{ ProcessId=$_.ProcessId; '
                   'ExecutablePath=$_.ExecutablePath; CommandLine=$_.CommandLine; '
                   "CreationDate=$_.CreationDate.ToUniversalTime().ToString('o') "
                   '} } | ConvertTo-Json -Compress')
        result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command],
                                capture_output=True, text=True, timeout=6,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        rows = json.loads(result.stdout) if result.returncode == 0 and result.stdout.strip() else []
        if isinstance(rows, dict):
            rows = [rows]
        for row in rows:
            try:
                if not row.get("ExecutablePath"):
                    continue
                running_exe = Path(row["ExecutablePath"]).resolve()
                if running_exe != exe and not (running_exe.name.lower() == "llama-server.exe"
                                               and running_exe.is_relative_to((BASE_DIR / "runtime").resolve())):
                    continue
                args = _command_args(row.get("CommandLine") or "")
                model_index, port_index = args.index("--model"), args.index("--port")
                model = Path(args[model_index + 1]).resolve()
                port = int(args[port_index + 1])
                if model not in models or not 1 <= port <= 65535 or not row.get("CreationDate"):
                    continue
                host = args[args.index("--host") + 1] if "--host" in args else ""
                def option(name: str) -> str:
                    return args[args.index(name) + 1] if name in args else ""
                matches = [profile_id for profile_id, profile in cfg["profiles"].items()
                           if (BASE_DIR / profile["model_path"]).resolve() == model
                           and option("--alias") == profile["alias"]
                           and ("ctx_size" not in profile or option("--ctx-size") == str(profile["ctx_size"]))
                           and (not profile.get("verified_legacy_qwen")
                                or option("--reasoning-effort") == profile["reasoning_effort"])]
                adopted_profile = matches[0] if len(matches) == 1 else "external"
                instances.append({
                    "pid": int(row["ProcessId"]), "created_at": row["CreationDate"],
                    "executable": str(running_exe), "model": str(model), "port": port,
                    "profile": adopted_profile, "host": host,
                })
            except (OSError, ValueError, TypeError, IndexError, KeyError):
                continue
    except (OSError, ValueError, TypeError, IndexError, KeyError, subprocess.TimeoutExpired):
        pass
    record = _read_process_record()
    for item in instances:
        item["managed"] = bool(record and all(record.get(field) == item.get(field) for field in
                                               ("pid", "created_at", "executable", "model", "port")))
    _model_instances_cache = (time.monotonic(), instances)
    return [dict(item) for item in instances]


def list_model_instances() -> List[Dict[str, Any]]:
    """List only precisely recognized instances using this project's model profiles."""
    with _lifecycle_guard():
        return _scan_model_instances()


def force_stop_model(pid: int, created_at: str) -> Dict[str, Any]:
    """Explicitly stop one selected project instance after rechecking its identity."""
    global _model_process, _model_process_created_at, _model_instances_cache
    if type(pid) is not int or pid <= 0 or not isinstance(created_at, str) or not created_at:
        return {"success": False, "message": "必须指定有效的 PID 和创建时间"}
    with _lifecycle_guard():
        block = _control_block(force=True)
        if block:
            return {"success": False, "message": block, "control_read_only": True}
        matches = [item for item in _scan_model_instances(force=True)
                   if item["pid"] == pid and item["created_at"] == created_at]
        if len(matches) != 1:
            return {"success": False, "message": "目标身份已变化或不属于本项目，未结束任何进程"}
        target = matches[0]
        row = _query_pid(pid, force=True)
        if not row or row.get("CreationDate") != created_at:
            return {"success": False, "message": "目标进程已退出或 PID 已重用"}
        try:
            args = _command_args(row.get("CommandLine") or "")
            verified = (Path(row["ExecutablePath"]).resolve() == Path(target["executable"]).resolve()
                        and Path(args[args.index("--model") + 1]).resolve() == Path(target["model"]).resolve()
                        and int(args[args.index("--port") + 1]) == target["port"])
        except (OSError, ValueError, IndexError, TypeError, KeyError):
            verified = False
        if not verified:
            return {"success": False, "message": "目标程序、权重或端口已变化，未结束任何进程"}
        try:
            if not lifecycle.terminate_exact_process(pid, created_at):
                return {"success": False, "message": "目标身份已变化或无法安全停止，未结束其他进程"}
            record = _read_process_record()
            if record and record.get("pid") == pid and record.get("created_at") == created_at:
                _clear_process_record()
            if _model_process is not None and _model_process.pid == pid:
                _model_process = None
                _model_process_created_at = None
            _model_instances_cache = (0.0, [])
            port_released = not is_port_listening("127.0.0.1", target["port"], timeout=0.2)
            message = (f"已结束选定模型进程 PID {pid}，端口已释放" if port_released
                       else f"已结束选定模型进程 PID {pid}，但端口仍被其他服务占用")
            return {"success": True, "message": message, "port_released": port_released}
        except OSError as exc:
            return {"success": False, "message": f"停止选定模型进程失败: {exc}"}


def get_owned_llama_processes() -> List[Dict[str, Any]]:
    """Return only the exact process previously launched by this manager."""
    global _last_start_error
    with _lifecycle_guard():
        row = _verified_process()
        if not row and PROCESS_FILE.exists():
            if _model_process is None and not _stopping_model:
                _last_start_error = "上次模型进程已退出或身份发生变化，请查看最新日志"
            _clear_process_record()
        if not row and not PROCESS_FILE.exists() and not _control_block():
            row = _adopt_project_process()
        if row and not _control_block() and len(_scan_model_instances()) <= 1:
            _ensure_ui_job_assignment(row)
        return [row] if row else []


def find_system_llama_processes() -> List[int]:
    return [int(row["ProcessId"]) for row in get_owned_llama_processes()]


def _key_fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest() if key else ""


def _check_child_exit() -> None:
    global _model_process, _model_process_created_at, _last_start_error, _last_exit_code
    if _model_process is None:
        return
    code = _model_process.poll()
    if code is None:
        return
    old_pid, old_created = _model_process.pid, _model_process_created_at
    _model_process = None
    _model_process_created_at = None
    record = _read_process_record()
    owns_record = bool(record and record.get("pid") == old_pid and record.get("created_at") == old_created)
    if owns_record and not _stopping_model:
        _last_exit_code = code
        _last_start_error = f"模型服务异常退出 (代码 {code})。请查看最新日志。"
    if owns_record:
        _clear_process_record()


def get_status() -> Dict[str, Any]:
    """Get full system and model status."""
    cfg = load_config()
    read_only_reason = _control_block()
    default_port = cfg.get("default_port", 24548)
    api_key = cfg.get("api_key", {}).get("key", "") if cfg.get("api_key", {}).get("enabled", False) else ""
    active_profile_id = cfg.get("active_profile", "uncensored_32k")
    profile = cfg.get("profiles", {}).get(active_profile_id, {})

    with _lifecycle_guard():
        _check_child_exit()
    owned_processes = get_owned_llama_processes()
    model_instances = list_model_instances()
    duplicates_detected = len(model_instances) > 1
    record = _read_process_record() if owned_processes else None
    pids = [int(row["ProcessId"]) for row in owned_processes]
    ui_owner_active = _live_ui_owner() is not None
    crash_protected = bool(not duplicates_detected and owned_processes and ui_owner_active
                           and _ensure_ui_job_assignment(owned_processes[0]))
    restart_reasons = []
    if record:
        default_port = record["port"]
        if record.get("key_fingerprint") != _key_fingerprint(api_key):
            restart_reasons.append("API Key 已更改")
        if cfg.get("default_port") != record["port"] or cfg.get("active_profile") != record.get("profile"):
            restart_reasons.append("模型档位或端口已更改")
        desired_host = "0.0.0.0" if cfg.get("lan_access", {}).get("enabled") else "127.0.0.1"
        if desired_host != record.get("host"):
            restart_reasons.append("局域网绑定已更改")
    port_open = is_port_listening("127.0.0.1", default_port, timeout=0.2)
    if port_open:
        api_online, model_data = query_llama_server_status(default_port, api_key)
    else:
        api_online, model_data = False, {}

    # Determine state
    if read_only_reason and model_instances:
        state = "EXTERNAL"
        state_text = read_only_reason
    elif duplicates_detected:
        state = "ERROR"
        state_text = f"检测到 {len(model_instances)} 个本项目模型进程，请选择并处理重复实例"
    elif restart_reasons and pids:
        state = "ERROR"
        state_text = "配置已更改，需重启模型服务后生效：" + "、".join(restart_reasons)
    elif api_online and pids:
        state = "RUNNING"
        state_text = "运行中 (已就绪)"
    elif model_data.get("auth_required") and pids:
        state = "ERROR"
        state_text = "鉴权失败：当前运行服务的 API Key 与配置不一致，请重启服务或检查密钥"
    elif pids:
        state = "LOADING"
        state_text = "启动中 (加载模型权重中)"
    elif port_open:
        state = "ERROR"
        state_text = "模型端口被其他服务占用"
    elif _last_start_error:
        state = "ERROR"
        state_text = _last_start_error
    else:
        state = "STOPPED"
        state_text = "已停止 (未启动)"

    # Hardware stats
    gpu = get_gpu_metrics()
    ram = get_system_ram()
    net = get_network_ips()

    # Active model details
    active_model_name = profile.get("alias", "qwen38-uncensored")
    active_ctx_size = profile.get("ctx_size", 32768)
    active_ftype = "IQ3_S / IQ4_XS"
    if model_data and "data" in model_data and len(model_data["data"]) > 0:
        first = model_data["data"][0]
        active_model_name = first.get("id", active_model_name)
        meta = first.get("meta", {})
        if "n_ctx" in meta:
            active_ctx_size = meta["n_ctx"]
        if "ftype" in meta:
            active_ftype = meta["ftype"]

    return {
        "control_read_only": bool(read_only_reason),
        "read_only_reason": read_only_reason or "",
        "state": state,
        "state_text": state_text,
        "pids": pids,
        "external_pids": [item["pid"] for item in model_instances if item["pid"] not in pids],
        "primary_pid": pids[0] if pids else None,
        "port": default_port,
        "port_open": port_open,
        "api_online": api_online and bool(pids),
        "external_api_online": api_online and bool(read_only_reason and model_instances),
        "restart_required": bool(restart_reasons),
        "restart_reason": "、".join(restart_reasons),
        "exit_code": _last_exit_code,
        "ui_owner_active": ui_owner_active,
        "crash_protected": crash_protected,
        "model_instances": model_instances,
        "duplicates_detected": duplicates_detected,
        "active_profile": active_profile_id,
        "active_profile_name": profile.get("name", active_profile_id),
        "active_model_name": active_model_name,
        "active_ctx_size": active_ctx_size,
        "active_ftype": active_ftype,
        "active_effective_params": (record.get("effective_parameters", {}) if record else {}),
        "catalog_model_id": (record.get("catalog_model_id") if record else profile.get("catalog_model_id")),
        "gpu": gpu,
        "ram": ram,
        "net": net,
        "lan_access": cfg.get("lan_access", {}),
        "api_key_enabled": cfg.get("api_key", {}).get("enabled", False),
        "has_api_key": bool(cfg.get("api_key", {}).get("key", "")),
        "autostart_model_on_manager_open": cfg.get("autostart_model_on_manager_open", False),
        "autostart_windows": cfg.get("autostart_windows", False)
    }


def start_model(profile_id: Optional[str] = None, preset_id: Optional[str] = _PRESET_UNSPECIFIED) -> Dict[str, Any]:
    """Start llama-server with the specified profile."""
    global _model_process, _model_process_created_at, _last_start_error, _last_exit_code, _model_instances_cache
    with _lifecycle_guard():
        block = _control_block(force=True)
        if block:
            return {"success": False, "message": block, "control_read_only": True}
        _check_child_exit()
        _last_start_error = ""
        _last_exit_code = None
        instances = _scan_model_instances(force=True)
        if instances:
            detail = ", ".join(f"PID {item['pid']}:{item['port']}" for item in instances[:4])
            return {"success": False, "message": f"已有本项目模型实例 ({detail})，请先处理重复运行"}
        pids = find_system_llama_processes()
        if pids:
            return {"success": False, "message": f"模型服务已经在运行中 (PID: {pids[0]})，请勿重复启动"}

        cfg = load_config()
        if not _valid_config(cfg):
            return {"success": False, "message": "配置文件无效，请检查端口、档位参数和局域网设置"}
        if not profile_id:
            profile_id = cfg.get("active_profile", "uncensored_32k")
        inherit_selection = preset_id is _PRESET_UNSPECIFIED
        previous_profile = cfg.get("profiles", {}).get(profile_id, {})
        if inherit_selection:
            preset_id = previous_profile.get("last_preset_id")

        import model_catalog
        import model_capabilities
        import parameter_store
        selected_model = model_catalog.get_model(profile_id)
        generic = bool(selected_model)
        if generic:
            verified_model = model_catalog.verify_model(profile_id)
            if not verified_model.get("success"):
                return {"success": False, "message": verified_model.get("message", "Model files are incomplete")}
            selected_model = verified_model["model"]
        preset = parameter_store.get_preset(preset_id) if preset_id else None
        if preset_id and not preset:
            return {"success": False, "message": "Selected parameter preset does not exist"}
        if generic:
            requested_params = (preset.get("parameters", {}) if preset else
                                {field: previous_profile[field] for field in model_capabilities._FIELDS
                                 if field in previous_profile} if inherit_selection else {})
            validation = model_capabilities.validate_parameters(profile_id,
                                                                requested_params)
            if not validation["valid"]:
                return {"success": False, "message": "; ".join(validation["errors"])}
            if preset and (preset.get("model_id") != profile_id
                           or Path(preset.get("model_path", "")).resolve() != Path(selected_model["path"]).resolve()):
                return {"success": False, "message": "Preset model binding differs from the selected model"}
            effective_params = validation["effective"]
            alias = "model-" + profile_id[:12]
            profile = {"id": profile_id, "name": selected_model.get("display_name") or selected_model["name"], "alias": alias,
                       "model_path": selected_model["path"], "template_path": "",
                       "catalog_model_id": profile_id, "last_preset_id": preset_id, **effective_params}
            if inherit_selection and not preset:
                profile["template_path"] = previous_profile.get("template_path", "")
            cfg["profiles"][profile_id] = profile
        else:
            profile = cfg.get("profiles", {}).get(profile_id)
            effective_params = {}
            if profile and preset:
                match = next((row for row in model_catalog.list_models()
                              if Path(row["path"]).resolve() == Path(profile["model_path"]).resolve()), None)
                if not match or preset.get("model_id") != match["id"]:
                    return {"success": False, "message": "Preset does not match this legacy profile model"}
                validation = model_capabilities.validate_parameters(match["id"], preset.get("parameters", {}))
                if not validation["valid"]:
                    return {"success": False, "message": "; ".join(validation["errors"])}
                profile = {**profile, **preset["parameters"]}
                effective_params = {field: profile.get(field) for field in model_capabilities._FIELDS if field in profile}
        if not profile:
            return {"success": False, "message": f"未找到档位配置: {profile_id}"}

        server_exe_rel = cfg.get("server_executable", "runtime/upstream/llama-server.exe")
        server_exe = (BASE_DIR / server_exe_rel).resolve()
        if not server_exe.is_file():
            return {"success": False, "message": f"llama-server.exe 不存在: {server_exe}"}

        model_rel = profile.get("model_path", "")
        model_file = (BASE_DIR / model_rel).resolve()
        if not model_rel or not model_file.is_file():
            return {"success": False, "message": f"模型权重文件不存在: {model_file}"}

        # Resolve template
        template_rel = profile.get("template_path", "")
        if preset and isinstance(preset.get("template"), dict):
            template_ref = preset["template"]
            template_rel = template_ref["path"] if template_ref.get("mode") == "file" else ""
        if generic:
            profile["template_path"] = template_rel
        template_file = (BASE_DIR / template_rel).resolve() if template_rel else None
        if template_rel and (not template_file or not template_file.is_file()):
            return {"success": False, "message": f"聊天模板文件不存在: {template_file}"}

        # Network host & API key
        lan_access = cfg.get("lan_access", {}).get("enabled", False)
        selected_ip = cfg.get("lan_access", {}).get("selected_ip", "")
        if lan_access and selected_ip and selected_ip not in get_network_ips()["all_ips"]:
            return {"success": False, "message": "所选局域网 IP 已不属于本机网卡，请重新选择"}
        host = "0.0.0.0" if lan_access else "127.0.0.1"
        port = cfg.get("default_port", 24548)
        if is_port_listening("127.0.0.1", port):
            return {"success": False, "message": f"端口 {port} 已被占用"}

        api_key_cfg = cfg.get("api_key", {})
        api_key = api_key_cfg.get("key", "") if api_key_cfg.get("enabled", False) else ""
        if lan_access and not api_key:
            return {"success": False, "message": "局域网共享必须启用并设置 API Key"}

        # Build args
        args = [
            str(server_exe),
            "--model", str(model_file),
            "--alias", profile["alias"],
            "--host", host,
            "--port", str(port),
        ]
        if generic:
            option_flags = {"ctx_size": "--ctx-size", "ngl": "-ngl", "parallel": "--parallel",
                            "cache_type_k": "--cache-type-k", "cache_type_v": "--cache-type-v",
                            "batch_size": "--batch-size", "ubatch_size": "--ubatch-size",
                            "temp": "--temp", "top_p": "--top-p", "top_k": "--top-k",
                            "min_p": "--min-p", "presence_penalty": "--presence-penalty"}
            for field, flag in option_flags.items():
                if field in effective_params:
                    args.extend([flag, str(effective_params[field])])
            if effective_params.get("flash_attn") is not None:
                args.extend(["--flash-attn", "on" if effective_params["flash_attn"] else "off"])
            if effective_params.get("jinja"):
                args.append("--jinja")
        else:
            args.extend([
            "--ctx-size", str(profile.get("ctx_size", 32768)),
            "-ngl", str(profile.get("ngl", 60)),
            "--parallel", str(profile.get("parallel", 1)),
            "--cache-type-k", profile.get("cache_type_k", "q8_0"),
            "--cache-type-v", profile.get("cache_type_v", "q8_0"),
            "--flash-attn", "on" if profile.get("flash_attn", True) else "off",
            "--batch-size", str(profile.get("batch_size", 256)),
            "--ubatch-size", str(profile.get("ubatch_size", 256)),
            ])

        if not generic and profile.get("jinja", True):
            args.append("--jinja")
        if template_file:
            args.extend(["--chat-template-file", str(template_file)])

        # Reasoning
        if generic:
            effort = effective_params.get("reasoning_effort")
            if effort == "off":
                args.extend(["--reasoning", "off", "--reasoning-budget", "0"])
            else:
                if effective_params.get("reasoning_format"):
                    args.extend(["--reasoning-format", effective_params["reasoning_format"]])
                if effort:
                    args.extend(["--reasoning-effort", effort])
                if "reasoning_budget" in effective_params:
                    args.extend(["--reasoning-budget", str(effective_params["reasoning_budget"])])
        else:
            reasoning = profile.get("reasoning_effort", "medium")
            if reasoning == "off":
                args.extend(["--reasoning-effort", "off", "--reasoning-budget", "0"])
            else:
                args.extend(["--reasoning-format", profile["reasoning_format"],
                             "--reasoning-effort", reasoning,
                             "--reasoning-budget", str(profile["reasoning_budget"])])

        # Sampling
        sampling_temp = profile.get("temp", 1.0)
        sampling_top_p = profile.get("top_p", 0.95)
        sampling_top_k = profile.get("top_k", 20)
        sampling_min_p = profile.get("min_p", 0.0)
        sampling_presence = profile.get("presence_penalty", 0.0)
        if not generic:
            args.extend([
            "--temp", str(sampling_temp),
            "--top-p", str(sampling_top_p),
            "--top-k", str(sampling_top_k),
            "--min-p", str(sampling_min_p),
            "--presence-penalty", str(sampling_presence)
            ])

        proc = None
        owner_job = None
        borrowed_job = False
        try:
            # Keep a job handle open before creating the child. If the UI exits
            # during startup, the helper's temporary handle bridges that gap.
            owner_job, borrowed_job = _open_owner_job_for_launch()
            if ACTIVE_LOG_FILE.exists() and ACTIVE_LOG_FILE.stat().st_size >= MAX_LOG_BYTES:
                archive = LOGS_DIR / f"llama-server-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}.log"
                ACTIVE_LOG_FILE.rename(archive)
            # Write startup header
            with open(ACTIVE_LOG_FILE, "a", encoding="utf-8") as f_hdr:
                f_hdr.write(f"\n\n======================================================\n")
                f_hdr.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting {profile.get('name')}...\n")
                f_hdr.write(f"Model: {model_file.name}; profile: {profile_id}; port: {port}\n")
                f_hdr.write(f"======================================================\n")

            # Give the child its own log handle so it keeps logging after manager exit.
            fd = os.open(str(ACTIVE_LOG_FILE), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
            try:
                import runtime_manager
                child_env = runtime_manager._probe_environment(server_exe)
                if api_key:
                    child_env["LLAMA_API_KEY"] = api_key
                else:
                    child_env.pop("LLAMA_API_KEY", None)
                with runtime_manager.external_dll_context():
                    proc = subprocess.Popen(
                        args, cwd=str(server_exe.parent), stdout=fd,
                        stderr=subprocess.STDOUT, close_fds=True,
                        env=child_env,
                        creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) |
                                       (lifecycle.CREATE_SUSPENDED if owner_job else 0))
                    )
            finally:
                os.close(fd)
            if owner_job:
                lifecycle.assign_process(owner_job, proc.pid)
                lifecycle.resume_process(proc.pid)
            _model_process = proc
            row = None
            for _ in range(8):
                row = _query_pid(proc.pid, force=True)
                if row and row.get("CreationDate"):
                    break
                if proc.poll() is not None:
                    break
                time.sleep(0.25)
            if not row or not row.get("CreationDate"):
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait(timeout=5)
                _model_process = None
                _last_exit_code = proc.returncode
                _last_start_error = "启动后无法核实模型进程身份，请查看最新日志"
                return {"success": False, "message": _last_start_error}
            _atomic_json(PROCESS_FILE, {
                "pid": proc.pid, "created_at": row["CreationDate"],
                "executable": str(server_exe), "model": str(model_file),
                "port": port, "profile": profile_id, "host": host,
                "key_fingerprint": _key_fingerprint(api_key),
                "catalog_model_id": profile_id if generic else next((item["id"] for item in model_catalog.list_models()
                    if Path(item["path"]).resolve() == model_file), None),
                "served_model_id": profile["alias"], "preset_id": preset_id,
                "effective_parameters": effective_params if generic or preset else
                    {field: profile.get(field) for field in ("ctx_size", "ngl", "parallel", "reasoning_effort",
                        "reasoning_budget", "reasoning_format", "temp", "top_p", "top_k", "min_p") if field in profile}
            })
            _model_instances_cache = (0.0, [])
            _model_process_created_at = row["CreationDate"]
            cfg["active_profile"] = profile_id
            cfg["profiles"][profile_id]["last_preset_id"] = preset_id
            if not save_config(cfg):
                _last_start_error = "模型已启动，但当前档位配置未能保存"
            if owner_job and not _live_ui_owner(force=True):
                raise RuntimeError("管理窗口在模型启动时退出，模型已回收")
            return {
                "success": True,
                "message": f"已成功发送启动指令，模型正在加载 (PID: {proc.pid})",
                "pid": proc.pid,
                "profile": profile_id
            }
        except Exception as e:
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                _model_process = None
                _model_process_created_at = None
            record = _read_process_record()
            if proc is not None and record and record.get("pid") == proc.pid:
                _clear_process_record()
            _model_instances_cache = (0.0, [])
            _last_start_error = f"启动进程失败: {e}"
            return {"success": False, "message": f"启动进程失败: {str(e)}"}
        finally:
            if borrowed_job:
                lifecycle.close_handle(owner_job)


def stop_model() -> Dict[str, Any]:
    """Gracefully stop llama-server and clean up memory."""
    global _model_process, _model_process_created_at, _stopping_model, _last_start_error, _last_exit_code, _model_instances_cache
    with _lifecycle_guard():
        block = _control_block(force=True)
        if block:
            return {"success": False, "message": block, "control_read_only": True}
        get_owned_llama_processes()
        row = _verified_process(force=True)
        if not row:
            _check_child_exit()
            return {"success": True, "message": "当前没有正在运行的模型进程"}
        pid = int(row["ProcessId"])
        record = _read_process_record()
        port = record["port"]
        _stopping_model = True
        try:
            if not lifecycle.terminate_exact_process(pid, record["created_at"]):
                return {"success": False, "message": "模型进程身份已变化或无法安全停止"}
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                still_running = _verified_process(force=True) is not None
                port_open = is_port_listening("127.0.0.1", port, timeout=0.2)
                if not still_running and not port_open:
                    if _model_process is not None:
                        try:
                            _model_process.wait(timeout=0.1)
                        except subprocess.TimeoutExpired:
                            pass
                    _model_process = None
                    _model_process_created_at = None
                    _clear_process_record()
                    _model_instances_cache = (0.0, [])
                    _last_start_error = ""
                    _last_exit_code = None
                    return {"success": True, "message": "本项目模型服务已停止，端口已释放"}
                time.sleep(0.4)
            return {"success": False, "message": "停止未完成：进程或端口仍在使用中"}
        except (OSError, subprocess.TimeoutExpired) as e:
            return {"success": False, "message": f"停止模型进程失败: {e}"}
        finally:
            _stopping_model = False


def switch_profile(new_profile_id: str) -> Dict[str, Any]:
    """Switch to a new model profile (stop old -> start new)."""
    with _lifecycle_guard():
        block = _control_block(force=True)
        if block:
            return {"success": False, "message": block, "control_read_only": True}
        cfg = load_config()
        if new_profile_id not in cfg.get("profiles", {}):
            return {"success": False, "message": "档位不存在"}
        running = bool(find_system_llama_processes())
        if not running:
            cfg["active_profile"] = new_profile_id
            ok = save_config(cfg)
            return {"success": ok, "message": "已选择档位；模型未启动" if ok else "配置保存失败",
                    "profile": new_profile_id}
        stopped = stop_model()
        if not stopped["success"]:
            return stopped
        return start_model(new_profile_id)


def get_logs(max_lines: int = 120) -> List[str]:
    """Read latest N lines from the active log file or recent logs."""
    target_file = ACTIVE_LOG_FILE
    if not target_file.exists() or target_file.stat().st_size == 0:
        # Check fallback logs
        candidate = LOGS_DIR / "llama-server-32k-tuned.log"
        if candidate.exists():
            target_file = candidate

    if not target_file.exists():
        return ["暂无日志记录。启动模型后将在此实时显示运行日志。"]

    try:
        max_lines = max(1, min(500, int(max_lines)))
        with open(target_file, "rb") as f:
            f.seek(0, os.SEEK_END)
            position = f.tell()
            chunks = []
            remaining = max_lines + 1
            # Bound both memory and disk work for very large server logs.
            while position > 0 and remaining > 0 and sum(map(len, chunks)) < 1024 * 1024:
                size = min(8192, position)
                position -= size
                f.seek(position)
                block = f.read(size)
                chunks.append(block)
                remaining -= block.count(b"\n")
            lines = b"".join(reversed(chunks)).decode("utf-8", errors="replace").splitlines()[-max_lines:]
            key = load_config().get("api_key", {}).get("key", "")
            return [_safe_log_line(line, key) for line in lines]
    except Exception as e:
        return [f"读取日志失败: {str(e)}"]


def _redact_secrets(line: str, key: str = "") -> str:
    if key:
        line = line.replace(key, "[已隐藏密钥]")
    line = re.sub(r"(?i)(Bearer\s+)\S+", r"\1[已隐藏密钥]", line)
    line = re.sub(r"(?i)((?:LLAMA_API_KEY|api[_-]?key)\s*[=:]\s*)\S+", r"\1[已隐藏密钥]", line)
    line = re.sub(r"sk-qwen38-[0-9a-fA-F]{32,}", "[已隐藏密钥]", line)
    return line


def _safe_log_line(line: str, key: str = "") -> str:
    if re.search(r'(?i)("(?:messages|prompt|input|content)"\s*:|\b(?:messages|prompt|input)=)', line):
        return "[request content hidden]"
    if key:
        line = line.replace(key, "[redacted]")
    line = re.sub(r"(?i)(Bearer\s+)\S+", r"\1[redacted]", line)
    line = re.sub(r"(?i)((?:LLAMA_API_KEY|api[_-]?key|access_token|hf_token|token)\s*[=:]\s*)\S+", r"\1[redacted]", line)
    line = re.sub(r"(?i)([?&](?:token|api_key|access_token)=)[^&\s]+", r"\1[redacted]", line)
    return re.sub(r"sk-[a-zA-Z0-9_-]{20,}", "[redacted]", line)


def diagnostic_redacted() -> Dict[str, Any]:
    """A deliberately small diagnostic record with no credentials or request text."""
    cfg = load_config()
    status = get_status()
    return {"schema_version": 1,
            "app": "DSH Companion",
            "state": status.get("state"), "port": status.get("port"),
            "api_online": status.get("api_online"),
            "restart_required": status.get("restart_required"),
            "control_read_only": status.get("control_read_only"),
            "model_count": len(status.get("model_instances", [])),
            "active_profile": status.get("active_profile"),
            "catalog_model_id": status.get("catalog_model_id"),
            "api_key_enabled": bool(cfg.get("api_key", {}).get("enabled")),
            "lan_enabled": bool(cfg.get("lan_access", {}).get("enabled")),
            "gpu": status.get("gpu"), "ram": status.get("ram"),
            "engine_version": get_runtime_version()}


@functools.lru_cache(maxsize=4)
def _runtime_version_cached(executable: str, mtime_ns: int) -> Optional[str]:
    try:
        import runtime_manager
        _, output = runtime_manager._run(Path(executable), "--version", timeout=5)
        raw = output.splitlines()
        for line in raw:
            line = line.strip()
            if re.match(r"(?i)^(?:llama\.cpp\s+)?(?:version|build)\s*[:=]", line):
                return line[:160]
        return None
    except (OSError, subprocess.TimeoutExpired):
        return None


def get_runtime_version() -> Optional[str]:
    cfg = load_config()
    raw = cfg.get("server_executable", "")
    exe = (BASE_DIR / raw).resolve() if raw else Path()
    if not exe.is_file():
        return None
    return _runtime_version_cached(str(exe), exe.stat().st_mtime_ns)


def list_scripts() -> List[Dict[str, Any]]:
    """List available automated scripts in scripts/ folder."""
    metadata = {
        "11-Check-Status.cmd": {"title": "系统状态与显存核验", "desc": "快速检查当前模型进程、端口 24548、GPU VRAM 占用与系统内存", "type": "cmd"},
        "check_status.ps1": {"title": "PowerShell 深度健康巡检", "desc": "深度检测 API 响应状态、参数模型信息、显存空闲与利用率", "type": "ps1"},
        "run_test_suite.py": {"title": "全功能基准测试套件", "desc": "综合测试中文问答、代码编写、工具调用沙盒与推理", "type": "py"},
        "benchmark_context_needle.py": {"title": "长文本深度大海捞针测试", "desc": "测试 32K/64K 超长上下文窗口下精准事实检索能力", "type": "py"},
        "benchmark_ab_templates.py": {"title": "模板死循环 A/B 对照测试", "desc": "对比原生模板与 froggeric v22.5 补丁防无限思考死循环表现", "type": "py"},
        "benchmark_original_qa.py": {"title": "原版多领域知识评测", "desc": "评测物理、数学、逻辑与常识推理的准确性", "type": "py"},
        "benchmark_writing_eval.py": {"title": "长篇小说连贯写作评测", "desc": "多阶段长篇写作，评估情节节奏、文笔深度与吐字速度", "type": "py"},
        "benchmark_vision.py": {"title": "多模态视觉理解评测", "desc": "结合 mmproj 视觉投影器评估图像识别与 OCR 能力", "type": "py"},
        "10-Stop-LocalAI.cmd": {"title": "一键停止全部模型服务", "desc": "紧急停止所有后台 llama-server 进程并释放显存", "type": "cmd"},
        "01-Start-Uncensored32K.cmd": {"title": "独立启动: 旗舰档 32K", "desc": "在独立命令行窗口运行 Uncensored 32K 档位", "type": "cmd"},
        "02-Start-Original32K.cmd": {"title": "独立启动: 官方原版 32K", "desc": "在独立命令行窗口运行 Original 32K 档位", "type": "cmd"},
        "04-Start-Writing8K.cmd": {"title": "独立启动: 长篇创作档 8K", "desc": "在独立命令行窗口运行 Writing 8K 档位", "type": "cmd"},
        "05-Start-Uncensored64K.cmd": {"title": "独立启动: 极深 64K", "desc": "在独立命令行窗口运行 Uncensored 64K 档位", "type": "cmd"},
    }

    results = []
    # Lifecycle scripts bypass this manager's PID ownership and window controls.
    lifecycle_scripts = {
        "10-Stop-LocalAI.cmd", "01-Start-Uncensored32K.cmd",
        "02-Start-Original32K.cmd", "04-Start-Writing8K.cmd",
        "05-Start-Uncensored64K.cmd"
    }
    if SCRIPTS_DIR.exists():
        for item in sorted(SCRIPTS_DIR.iterdir()):
            if item.name in metadata and item.name not in lifecycle_scripts:
                info = metadata[item.name].copy()
                info["filename"] = item.name
                info["exists"] = True
                results.append(info)
    return results


def run_script(script_name: str) -> Dict[str, Any]:
    """Run an automated script in the background and capture output."""
    global _active_script_task
    with _active_script_lock:
        if _active_script_task and _active_script_task.get("running"):
            return {"success": False, "message": f"已有脚本任务正在运行: {_active_script_task.get('name')}"}

        allowed = {item["filename"] for item in list_scripts()}
        if script_name not in allowed:
            return {"success": False, "message": "脚本不在允许列表中"}
        target = (SCRIPTS_DIR / script_name).resolve()
        if not target.is_relative_to(SCRIPTS_DIR.resolve()) or not target.is_file():
            return {"success": False, "message": f"脚本文件不存在: {script_name}"}

        cmd = []
        if script_name.endswith(".cmd") or script_name.endswith(".bat"):
            cmd = ["cmd.exe", "/c", str(target)]
        elif script_name.endswith(".ps1"):
            cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(target)]
        elif script_name.endswith(".py"):
            python_cmd = _python_command()
            if not python_cmd:
                return {"success": False, "message": "未找到 Python 解释器"}
            cmd = python_cmd + [str(target)]
        else:
            return {"success": False, "message": "不支持的脚本格式"}

        task_id = str(int(time.time()))
        task_info = {
            "task_id": task_id,
            "name": script_name,
            "running": True,
            "exit_code": None,
            "output_lines": [],
            "start_time": time.strftime("%H:%M:%S")
        }
        _active_script_task = task_info

        def worker():
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(BASE_DIR),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                )
                for line in iter(proc.stdout.readline, ''):
                    key = load_config().get("api_key", {}).get("key", "")
                    task_info["output_lines"].append(_redact_secrets(line.rstrip("\r\n")[:4000], key))
                    if len(task_info["output_lines"]) > 500:
                        task_info["output_lines"] = task_info["output_lines"][-500:]
                proc.wait()
                task_info["exit_code"] = proc.returncode
            except Exception as e:
                task_info["output_lines"].append("[执行出错]：请检查脚本与 Python 环境")
                task_info["exit_code"] = -1
            finally:
                task_info["running"] = False

        t = threading.Thread(target=worker, daemon=True)
        t.start()

        return {"success": True, "message": f"脚本 {script_name} 已启动", "task_id": task_id}


def get_script_output() -> Dict[str, Any]:
    """Get output of the currently running or recently completed script."""
    with _active_script_lock:
        if not _active_script_task:
            return {"has_task": False, "running": False, "output": []}
        return {
            "has_task": True,
            "task_id": _active_script_task.get("task_id"),
            "name": _active_script_task.get("name"),
            "running": _active_script_task.get("running", False),
            "exit_code": _active_script_task.get("exit_code"),
            "start_time": _active_script_task.get("start_time"),
            "output": [_redact_secrets(line, load_config().get("api_key", {}).get("key", ""))
                       for line in _active_script_task.get("output_lines", [])]
        }


def _python_command() -> List[str]:
    candidates = []
    if not getattr(sys, "frozen", False) and Path(sys.executable).name.lower().startswith("python"):
        candidates.append([sys.executable])
    for name in ("python.exe", "py.exe"):
        path = shutil.which(name)
        if path:
            candidates.append([path, "-3"] if name == "py.exe" else [path])
    for candidate in candidates:
        try:
            result = subprocess.run(candidate + ["-c", "import sys;sys.exit(sys.version_info.major != 3)"],
                                    capture_output=True, timeout=5,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode == 0:
                return candidate
        except (OSError, subprocess.TimeoutExpired):
            pass
    return []


def generate_secure_api_key() -> str:
    """Generate a high-entropy API key."""
    return f"sk-qwen38-{secrets.token_hex(16)}"


def get_active_endpoint() -> Dict[str, Any]:
    """Public, credential-free descriptor for a verified ready local model."""
    status = get_status()
    cfg = load_config()
    profile = cfg.get("profiles", {}).get(status.get("active_profile"), {})
    record = _read_process_record() if status.get("primary_pid") else None
    ready = bool(status.get("api_online") and not status.get("restart_required")
                 and not status.get("duplicates_detected"))
    served = record.get("served_model_id") if record else status.get("active_model_name")
    catalog_id = record.get("catalog_model_id") if record else profile.get("catalog_model_id")
    if not catalog_id and profile.get("model_path"):
        try:
            import model_catalog
            catalog_id = next((row["id"] for row in model_catalog.list_models()
                               if Path(row["path"]).resolve() == Path(profile["model_path"]).resolve()), None)
        except (OSError, ValueError):
            pass
    effective = record.get("effective_parameters", {}) if record else {}
    context = status.get("active_ctx_size")
    display_name = profile.get("name") or ""
    if catalog_id:
        try:
            import model_catalog
            catalog_row = model_catalog.get_model(catalog_id)
            if catalog_row:
                display_name = catalog_row.get("display_name") or catalog_row.get("name") or display_name
        except (OSError, ValueError):
            pass
    return {"success": ready, "state": status.get("state"), "api_online": bool(status.get("api_online")),
            "base_url": f"http://127.0.0.1:{status['port']}/v1", "port": status["port"],
            "catalog_model_id": catalog_id, "display_name": display_name, "active_profile": status.get("active_profile"),
            "model_id": served, "context_size": context,
            "max_output_tokens": (effective.get("n_predict") if type(effective.get("n_predict")) is int else None),
            "reasoning_mode": effective.get("reasoning_effort", "unknown") if record else "unknown",
            "reasoning_format": effective.get("reasoning_format") if record else None,
            "restart_required": bool(status.get("restart_required")),
            "control_read_only": bool(status.get("control_read_only"))}


def get_active_api_key() -> str:
    """Credential getter for the DSH connection writer; never include in status or logs."""
    cfg = load_config()
    configured = cfg.get("api_key", {})
    key = configured.get("key", "") if configured.get("enabled") else ""
    record = _read_process_record()
    if record and record.get("key_fingerprint") != _key_fingerprint(key):
        return ""
    return key if get_active_endpoint().get("success") else ""


def test_api_connection(api_key: str = "", port: int = 24548) -> Dict[str, Any]:
    """Test connection to the model endpoint with latency measurement."""
    url = f"http://127.0.0.1:{port}/v1/models"
    headers = {"User-Agent": "Qwen38-Manager/1.0"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            latency_ms = round((time.time() - t0) * 1000, 1)
            data = json.loads(resp.read().decode("utf-8"))
            model_id = data.get("data", [{}])[0].get("id", "unknown")
            return {
                "success": True,
                "latency_ms": latency_ms,
                "status_code": resp.status,
                "model_id": model_id,
                "message": f"连接成功！HTTP 200 OK，响应延迟 {latency_ms}ms，当前在线模型: {model_id}"
            }
    except urllib.error.HTTPError as e:
        latency_ms = round((time.time() - t0) * 1000, 1)
        if e.code == 401:
            return {
                "success": False,
                "latency_ms": latency_ms,
                "status_code": 401,
                "message": "服务端拒绝连接 (401 Unauthorized)：API Key 缺失或无效，请检查 API Key 配置。"
            }
        return {
            "success": False,
            "latency_ms": latency_ms,
            "status_code": e.code,
            "message": f"HTTP 请求异常: {e.code} {e.reason}"
        }
    except Exception as e:
        return {
            "success": False,
            "latency_ms": 0,
            "status_code": 0,
            "message": f"连接失败 (模型服务可能未启动或端口被阻断): {str(e)}"
        }
