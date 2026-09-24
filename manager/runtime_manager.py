"""Discover and verify a user-installed llama.cpp engine without bundling it."""

from __future__ import annotations

import ctypes
import json
import os
import platform
import re
import subprocess
import sys
import threading
import webbrowser
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app_paths import INSTALL_ROOT

REVIEWED_RELEASE = "b11149"
RELEASE_URL = f"https://github.com/ggml-org/llama.cpp/releases/tag/{REVIEWED_RELEASE}"
_ASSET_BASE = f"https://github.com/ggml-org/llama.cpp/releases/download/{REVIEWED_RELEASE}/"
_ASSETS = {
    "cpu": ("llama-b11149-bin-win-cpu-x64.zip",),
    "cuda": ("llama-b11149-bin-win-cuda-12.4-x64.zip",
             "cudart-llama-bin-win-cuda-12.4-x64.zip"),
    "vulkan": ("llama-b11149-bin-win-vulkan-x64.zip",),
    "rocm": ("llama-b11149-bin-win-rocm-10.0-x64.zip",),
}
_ASSET_METADATA = {
    "llama-b11149-bin-win-cpu-x64.zip": (18559583, "d1cb5f9ef7bbb7068954b4c9767d5b5309e20bcefeb61d4aafc47f9581f38752"),
    "llama-b11149-bin-win-cuda-12.4-x64.zip": (253869752, "d3140fe21ab2e665a706ca27923b27ca264f1c564b5837abea4566cc49c16096"),
    "cudart-llama-bin-win-cuda-12.4-x64.zip": (391443627, "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6"),
    "llama-b11149-bin-win-vulkan-x64.zip": (32126960, "ca432b775c5dcb5af85bbdcf22b06b6e2dca792f30334b19ef4a45ee4a512446"),
    "llama-b11149-bin-win-rocm-10.0-x64.zip": (251910635, "46a4eb51eb6ba6b677f67f1a4b37a82034560f484767915e6afaec43048c0ff0"),
}
_GPU_NOTES = {
    "cpu": "适用于所有受支持的 Windows x64 机器；大型模型在 CPU 上可能较慢。",
    "cuda": "仅适用于兼容的 NVIDIA 显卡和驱动。两个官方 ZIP 解压到同一文件夹；无需安装完整 CUDA Toolkit。",
    "vulkan": "可作为支持 Vulkan 的显卡候选；显卡及驱动兼容性须用所选引擎实际列出设备确认。",
    "rocm": "仅在 AMD 官方兼容列表明确支持该显卡、Windows 和 ROCm 版本时选择；不能仅凭 AMD 显卡名称判定。",
}
_dll_launch_lock = threading.RLock()


@contextmanager
def external_dll_context():
    """Undo PyInstaller's process-wide DLL override only while creating a child."""
    with _dll_launch_lock:
        frozen_windows = os.name == "nt" and getattr(sys, "frozen", False)
        if frozen_windows:
            ctypes.windll.kernel32.SetDllDirectoryW(None)
        try:
            yield
        finally:
            if frozen_windows:
                ctypes.windll.kernel32.SetDllDirectoryW(str(Path(sys._MEIPASS)))


def _resolved_candidate(path: str | Path) -> tuple[Path | None, list[str]]:
    candidate = Path(path).expanduser()
    if candidate.is_file():
        return candidate.resolve(), []
    if candidate.is_dir():
        direct = candidate / "llama-server.exe"
        if direct.is_file():
            return direct.resolve(), []
        matches = [p.resolve() for p in candidate.rglob("llama-server.exe")
                   if len(p.relative_to(candidate).parts) <= 4]
        if len(matches) == 1:
            return matches[0], []
        return None, [str(p) for p in matches[:20]]
    return None, []


def _probe_environment(executable: Path) -> dict[str, str]:
    """Use the executable's DLLs and Windows itself, excluding developer PATH entries."""
    env = os.environ.copy()
    windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    env["PATH"] = os.pathsep.join((str(executable.parent), str(windows / "System32"), str(windows)))
    return env


def _run(executable: Path, argument: str, timeout: int = 12) -> tuple[int | None, str]:
    try:
        with external_dll_context():
            process = subprocess.Popen([str(executable), argument], cwd=str(executable.parent),
                                       env=_probe_environment(executable), stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                       errors="replace",
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            return None, "探测超时"
        return process.returncode, (stdout + "\n" + stderr)[:500_000]
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)


def probe_executable(path: str | Path) -> dict[str, Any]:
    """Read-only validation; accepts a file or a folder, including paths with spaces."""
    executable, choices = _resolved_candidate(path)
    if not executable:
        detail = ("目录里找到多个 llama-server.exe，请选中具体文件" if choices else
                  "未找到 llama-server.exe，请先从官方发布页下载并解压，再选择文件或所在文件夹")
        return {"success": False, "message": detail, "path": str(path), "choices": choices,
                "health": "missing", "version": None, "backend": "unknown", "devices": []}
    if executable.name.casefold() != "llama-server.exe":
        return {"success": False, "message": "请选择 llama-server.exe", "path": str(executable),
                "health": "invalid", "version": None, "backend": "unknown", "devices": []}
    version_code, version_text = _run(executable, "--version")
    version_match = re.search(r"(?im)^version:\s*(.+)$", version_text)
    if version_code != 0 or not version_match:
        return {"success": False, "message": "引擎未能独立启动；请检查下载包是否完整、架构及依赖库是否匹配",
                "path": str(executable), "health": "unavailable", "version": None,
                "backend": "unknown", "devices": [], "diagnostic": version_text[-1200:]}
    help_code, help_text = _run(executable, "--help")
    required = ("--model", "--port", "--host")
    missing_flags = [flag for flag in required if flag not in help_text]
    if help_code != 0 or missing_flags:
        return {"success": False, "message": "该程序缺少控制台需要的 llama-server 参数",
                "path": str(executable), "health": "incompatible",
                "version": version_match.group(1).strip(), "backend": "unknown", "devices": [],
                "missing_flags": missing_flags}
    devices_code, devices_text = _run(executable, "--list-devices")
    devices = [line.strip() for line in devices_text.splitlines()
               if re.match(r"\s*(?:CUDA|Vulkan|ROCm|HIP|SYCL|OpenCL)\d+:\s+", line, re.I)]
    backend = ("cuda" if any("CUDA" in d.upper() for d in devices) else
               "rocm" if any(re.search(r"ROCm|HIP", d, re.I) for d in devices) else
               "vulkan" if any("VULKAN" in d.upper() for d in devices) else
               "gpu" if devices else "cpu")
    note = ("引擎已列出 GPU 设备；实际模型推理仍需启动后验证。" if devices else
            "引擎可启动，但未列出 GPU 设备；目前按 CPU 模式使用，大模型可能较慢。")
    if devices_code != 0:
        note = "引擎可启动，但设备探测失败；GPU 状态未知，实际推理需另行验证。"
        backend = "unknown"
    return {"success": True, "message": note, "path": str(executable),
            "health": "ready" if devices_code == 0 else "device_unknown",
            "version": version_match.group(1).strip(), "backend": backend,
            "devices": devices, "device_probe_ok": devices_code == 0,
            "build_matches_reviewed_release": f"build {REVIEWED_RELEASE[1:]}" in version_match.group(1)}


def selected_server_executable() -> Path | None:
    import backend
    raw = backend.load_config().get("server_executable", "")
    if not raw:
        return None
    candidate = (INSTALL_ROOT / raw).resolve()
    return candidate if candidate.is_file() else None


def _video_controllers() -> list[dict[str, str]]:
    if os.name != "nt":
        return []
    script = ("Get-CimInstance Win32_VideoController | "
              "Select-Object Name,PNPDeviceID,DriverVersion | ConvertTo-Json -Compress")
    try:
        result = subprocess.run([str(Path(os.environ.get("SystemRoot", r"C:\Windows")) /
                                      "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"),
                                 "-NoProfile", "-NonInteractive", "-Command", script],
                                capture_output=True, text=True, timeout=8,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        parsed = json.loads(result.stdout) if result.returncode == 0 and result.stdout.strip() else []
        return [{"name": str(row.get("Name") or ""),
                 "driver_version": str(row.get("DriverVersion") or ""),
                 "vendor": ("nvidia" if "VEN_10DE" in str(row.get("PNPDeviceID") or "").upper() else
                            "amd" if "VEN_1002" in str(row.get("PNPDeviceID") or "").upper() else
                            "intel" if "VEN_8086" in str(row.get("PNPDeviceID") or "").upper() else "unknown")}
                for row in (parsed if isinstance(parsed, list) else [parsed]) if isinstance(row, dict)]
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []


def _cuda_driver_devices() -> int | None:
    if os.name != "nt":
        return None
    try:
        driver = ctypes.WinDLL("nvcuda.dll")
        if driver.cuInit(0) != 0:
            return 0
        count = ctypes.c_int()
        if driver.cuDeviceGetCount(ctypes.byref(count)) != 0:
            return 0
        return count.value
    except (OSError, AttributeError):
        return None


def detect_hardware() -> dict[str, Any]:
    controllers = _video_controllers()
    cuda_count = _cuda_driver_devices()
    try:
        ctypes.WinDLL("vulkan-1.dll")
        vulkan_loader = True
    except (OSError, AttributeError):
        vulkan_loader = False
    return {"os": platform.system(), "architecture": platform.machine(),
            "video_controllers": controllers, "cuda_driver_device_count": cuda_count,
            "vulkan_loader_present": vulkan_loader,
            "gpu_runtime_confirmed": False,
            "note": "显卡/驱动检测只提供下载候选；实际可用性以所选引擎列出的设备和模型推理为准。"}


def official_download_info() -> dict[str, Any]:
    hardware = detect_hardware()
    vendors = {row["vendor"] for row in hardware["video_controllers"]}
    recommendations = ["cpu"]
    if "nvidia" in vendors:
        recommendations.append("cuda" if (hardware["cuda_driver_device_count"] or 0) > 0 else "vulkan")
    elif "amd" in vendors or "intel" in vendors:
        recommendations.append("vulkan")
    channels = []
    for name, assets in _ASSETS.items():
        channels.append({"id": name, "recommended_candidate": name in recommendations,
                         "assets": [{"name": asset, "url": _ASSET_BASE + asset,
                                     "size_bytes": _ASSET_METADATA[asset][0],
                                     "sha256": _ASSET_METADATA[asset][1]} for asset in assets],
                         "note": _GPU_NOTES[name], "verified_usable": False})
    return {"success": True, "message": "从 llama.cpp 官方发布页下载并解压，选择 llama-server.exe 后检测。",
            "release": REVIEWED_RELEASE, "release_url": RELEASE_URL,
            "channels": channels, "hardware": hardware,
            "compatibility_urls": {
                "cuda": "https://docs.nvidia.com/cuda/archive/12.4.0/cuda-toolkit-release-notes/",
                "rocm": "https://rocm.docs.amd.com/en/latest/compatibility/compatibility-matrix.html",
                "vulkan": "https://docs.vulkan.org/guide/latest/checking_for_support.html"},
            "steps": ["选择与 Windows 架构及显卡相符的官方 ZIP；不确定时先选 CPU。",
                      "CUDA 12.4 须同时下载同页 cudart ZIP，并把两个 ZIP 解压到同一个文件夹。",
                      "选择解压后的 llama-server.exe，点击检测并使用。"]}


def open_official_download(channel: str = "cpu") -> dict[str, Any]:
    if channel not in _ASSETS:
        return {"success": False, "message": "未知的下载通道"}
    opened = webbrowser.open(RELEASE_URL)
    return {"success": bool(opened), "message": "已打开官方发布页" if opened else "无法打开浏览器，请复制官方链接",
            "url": RELEASE_URL, "channel": channel,
            "assets": list(_ASSETS[channel])}


def check_runtime() -> dict[str, Any]:
    import backend
    configured = backend.load_config().get("server_executable", "")
    candidate = (INSTALL_ROOT / configured).resolve() if configured else None
    probe = (probe_executable(candidate) if candidate and candidate.is_file() else
             {"success": False, "message": "尚未选择可用的 llama-server.exe。请下载并选择引擎文件。",
              "path": str(candidate) if candidate else "", "health": "missing",
              "version": None, "backend": "unknown", "devices": []})
    info = official_download_info()
    return {**probe, "selected": str(candidate) if candidate else "",
            "recommended": [row["id"] for row in info["channels"] if row["recommended_candidate"]],
            "hardware": info["hardware"], "channels": info["channels"],
            "official_release_url": RELEASE_URL,
            "steps": info["steps"]}


def select_executable(path: str | Path) -> dict[str, Any]:
    """Commit a verified engine choice only while this app owns no running model."""
    import backend
    probe = probe_executable(path)
    if not probe["success"]:
        return probe
    with backend._lifecycle_guard():
        if backend._scan_model_instances(force=True):
            return {"success": False, "message": "模型服务正在运行；请先停止当前模型，再切换引擎。",
                    "health": "busy", "path": probe["path"]}
        cfg = backend.load_config()
        old = cfg.get("server_executable", "")
        cfg["server_executable"] = probe["path"]
        if not backend.save_config(cfg):
            return {"success": False, "message": "无法保存引擎路径，原设置已保留。",
                    "health": "save_failed", "path": probe["path"]}
    return {**probe, "message": "引擎已检测并设为当前使用路径。", "previous": old,
            "selected": probe["path"]}


def list_runtime_channels() -> list[dict[str, Any]]:
    return official_download_info()["channels"]


def update_runtime(path: str | Path) -> dict[str, Any]:
    """Review a newly downloaded executable and switch; no unreviewed auto-update."""
    return select_executable(path)
