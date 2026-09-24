"""Independent DeepSeek Harness process control for the native manager.

This module never starts or stops the model backend. Ownership is deliberately
limited to processes started by this controller instance in this UI session.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime
import json
import msvcrt
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from urllib.error import HTTPError, URLError
from http.cookiejar import CookieJar
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPCookieProcessor, ProxyHandler
from uuid import uuid4

from app_paths import DATA_ROOT, DSH_DEFAULT_ROOT, INSTALL_ROOT
from network_proxy import ProxyError, child_environment


DSH_ROOT = DSH_DEFAULT_ROOT
PORT = 3080
COMPONENT_CONFIG = DATA_ROOT / "runtime" / "components.json"
_LEGACY_DSH_ROOT = INSTALL_ROOT.parent / "deepseek-harness"  # Optional sibling discovery only.
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_DSH_BIN_RE = re.compile(r"[\\/]@deepseek-ai[\\/]dsh[\\/]lib[\\/]bin\.js\b", re.I)
_WEB_RE = re.compile(r"(?:^|\s)web(?:\s|$)", re.I)
_URL_RE = re.compile(r"https?://(?:127\.0\.0\.1|localhost):\d+[^\s\"'<>]*", re.I)

_kernel = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_kernel.OpenProcess.restype = wintypes.HANDLE
_kernel.GetProcessTimes.argtypes = (
    wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
    ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
    ctypes.POINTER(wintypes.FILETIME),
)
_kernel.GetProcessTimes.restype = wintypes.BOOL
_kernel.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
_kernel.TerminateProcess.restype = wintypes.BOOL
_kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
_kernel.CloseHandle.restype = wintypes.BOOL
_kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
_kernel.WaitForSingleObject.restype = wintypes.DWORD
_kernel.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
_kernel.CreateMutexW.restype = wintypes.HANDLE
_kernel.ReleaseMutex.argtypes = (wintypes.HANDLE,)
_kernel.ReleaseMutex.restype = wintypes.BOOL


def _dsh_bin(root: Path) -> Path:
    if root.name.lower() == "dsh" and root.parent.name.lower() == "@deepseek-ai":
        return root / "lib" / "bin.js"
    return root / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"


def _compatible_dsh_files(root: Path) -> bool:
    binary = _dsh_bin(root)
    scope = binary.parent.parent.parent
    names = ("dsh", "dsh-api-settings-controller", "dsh-llm-pi-ai",
             "dsh-agent-default-model", "dsh-client-connection", "dsh-session-title-llm")
    if not binary.is_file():
        return False
    try:
        for name in names:
            package = json.loads((scope / name / "package.json").read_text(encoding="utf-8"))
            if not isinstance(package, dict) or package.get("version") != "0.1.5-rc.3":
                return False
        return True
    except (OSError, ValueError):
        return False


def _component_preferences() -> dict:
    try:
        data = json.loads(COMPONENT_CONFIG.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _discovered_dsh_root(ignore_preferences: bool = False) -> tuple[Path, str]:
    selected = None if ignore_preferences else _component_preferences().get("dsh_root")
    if isinstance(selected, str) and selected:
        return Path(selected).expanduser(), "selected"
    candidates = [INSTALL_ROOT / "components" / "dsh", DSH_DEFAULT_ROOT]
    npm_root = Path(os.environ.get("APPDATA", "")) / "npm"
    if os.environ.get("APPDATA"):
        candidates.append(npm_root)
    candidates.append(_LEGACY_DSH_ROOT)
    for candidate in candidates:
        if _compatible_dsh_files(candidate):
            return candidate, "discovered"
    return candidates[0], "suggested"


def _discovered_node(ignore_preferences: bool = False) -> tuple[str | None, str]:
    selected = None if ignore_preferences else _component_preferences().get("node_executable")
    if isinstance(selected, str) and selected:
        return selected, "selected"
    found = shutil.which("node.exe")
    return found, "discovered" if found else "missing"


def _verified_node(path: str | Path | None) -> str | None:
    if not path:
        return None
    executable = Path(path).expanduser()
    if executable.name.lower() != "node.exe" or not executable.is_file():
        return None
    try:
        result = subprocess.run([str(executable), "--version"], capture_output=True,
                                text=True, timeout=5, creationflags=_CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    version = result.stdout.strip()
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", version)
    return version if result.returncode == 0 and match and int(match.group(1)) >= 20 else None


def _creation_id(pid: int) -> str | None:
    handle = _kernel.OpenProcess(0x1000, False, pid)  # query limited information
    if not handle:
        return None
    try:
        created = wintypes.FILETIME()
        exited = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not _kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                       ctypes.byref(kernel), ctypes.byref(user)):
            return None
        return str((created.dwHighDateTime << 32) | created.dwLowDateTime)
    finally:
        _kernel.CloseHandle(handle)


def _snapshot() -> tuple[list[dict], list[dict]]:
    # Query the actual TCP owner; a stale PID file is never evidence of a server.
    script = (
        "$p=@(Get-CimInstance Win32_Process -Filter \"Name = 'node.exe'\" | "
        "Select-Object ProcessId,ExecutablePath,CommandLine);"
        f"$l=@(Get-NetTCPConnection -LocalPort {PORT} -State Listen -ErrorAction SilentlyContinue | "
        "Select-Object LocalAddress,LocalPort,OwningProcess);"
        "@{processes=$p;listeners=$l}|ConvertTo-Json -Compress -Depth 4"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=_CREATE_NO_WINDOW, timeout=12, check=True,
    )
    data = json.loads(result.stdout)
    return data.get("processes") or [], data.get("listeners") or []


def _is_dsh(proc: dict) -> bool:
    exe = str(proc.get("ExecutablePath") or "")
    line = str(proc.get("CommandLine") or "")
    return (Path(exe).name.lower() == "node.exe" and bool(_DSH_BIN_RE.search(line))
            and bool(_WEB_RE.search(line)))


def _instances(procs: list[dict], listeners: list[dict], owned_id: tuple[int, str] | None) -> list[dict]:
    addresses: dict[int, str] = {}
    for item in listeners:
        try:
            addresses[int(item["OwningProcess"])] = str(item["LocalAddress"])
        except (KeyError, ValueError, TypeError):
            pass
    output = []
    for proc in procs:
        if not _is_dsh(proc):
            continue
        pid = int(proc["ProcessId"])
        created = _creation_id(pid)
        if created is None:
            continue
        output.append({
            "pid": pid, "created_at": created,
            "exe": str(proc.get("ExecutablePath") or ""),
            "port": PORT if pid in addresses else None,
            "address": addresses.get(pid),
            "owned": owned_id == (pid, created),
        })
    return output


def _authenticated_url(log_path: Path, port: int = PORT) -> str | None:
    try:
        content = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for match in reversed(list(_URL_RE.finditer(content))):
        url = match.group(0).rstrip("),.;")
        if (re.search(r"[?&]token=[^&\s]+", url, re.I)
                and re.match(rf"https?://(?:127\.0\.0\.1|localhost):{port}(?:/|\?|$)", url, re.I)):
            return url
    return None


def _check_authenticated(url: str) -> bool:
    try:
        opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
        with opener.open(url, timeout=4) as response:
            return response.status == 200
    except (HTTPError, URLError, TimeoutError, ValueError):
        return False


class DSHController:
    """Thread safe controller; ownership never survives creation of a new UI session."""

    def __init__(self, root: Path | str | None = None, home: Path | str | None = None,
                 node_executable: Path | str | None = None,
                 runtime_root: Path | str | None = None):
        discovered_root, root_source = _discovered_dsh_root() if root is None else (Path(root), "explicit")
        discovered_node, node_source = _discovered_node() if node_executable is None else (str(node_executable), "explicit")
        self.root = Path(discovered_root)
        self.root_source = root_source
        self.node_source = node_source
        self.home = Path(home) if home is not None else None
        self.runtime_root = (Path(runtime_root) if runtime_root is not None
                             else DATA_ROOT / "runtime" / "dsh-controller")
        self.node_executable = discovered_node
        self.bin = _dsh_bin(self.root)
        self._lock = threading.RLock()
        self._owned_id: tuple[int, str] | None = None
        self._owned_log: Path | None = None
        self._process: subprocess.Popen | None = None
        self._attached_url: str | None = None
        self._attached_id: tuple[int, str] | None = None
        self._rpc_opener = None
        self._rpc_identity: tuple[int, str] | None = None
        self._proxy_applied = False

    def configure_paths(self, root: Path | str, node_executable: Path | str | None,
                        *, root_source: str, node_source: str) -> bool:
        with self._lock:
            if self._owned_id and self._process and self._process.poll() is None:
                return False
            self.root = Path(root)
            self.bin = _dsh_bin(self.root)
            self.root_source = root_source
            self.node_executable = str(node_executable) if node_executable else None
            self.node_source = node_source
            return True

    def _status_locked(self) -> dict:
        try:
            procs, listeners = _snapshot()
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
            return {"running": False, "owned": False, "pid": None, "created_at": None,
                    "port": PORT, "can_open": False, "instances": [],
                    "message": f"无法检查 DSH 进程：{type(exc).__name__}", "error": True}
        instances = _instances(procs, listeners, self._owned_id)
        owner_pids = {int(x.get("OwningProcess", -1)) for x in listeners}
        running = next((x for x in instances if x["pid"] in owner_pids), None)
        if self._owned_id and not any((x["pid"], x["created_at"]) == self._owned_id
                                      for x in instances):
            pid, created = self._owned_id
            # A transient CIM miss or denied query must not erase cleanup rights.
            exited = self._process is not None and self._process.poll() is not None
            reused = _creation_id(pid) not in (None, created)
            if exited or reused:
                self._owned_id = None
                self._owned_log = None
                self._process = None
        current_id = (running["pid"], running["created_at"]) if running else None
        if self._attached_id and self._attached_id != current_id:
            self._attached_url = None
            self._attached_id = None
            self._rpc_opener = None
            self._rpc_identity = None
        can_open = bool(running and ((running["owned"] and self._owned_log
                        and _authenticated_url(self._owned_log, PORT))
                        or (self._attached_id == current_id and self._attached_url)))
        if running:
            message = "DSH 正在运行（由本窗口启动）" if running["owned"] else "DSH 正在运行（外部实例）"
        elif listeners:
            message = "3080 端口被其他进程占用"
        elif instances:
            message = "发现 DSH 进程，正在启动或未监听 3080"
        else:
            message = "DSH 未运行"
        return {"running": bool(running), "owned": bool(running and running["owned"]),
                "pid": running["pid"] if running else None,
                "created_at": running["created_at"] if running else None,
                "port": PORT, "can_open": can_open, "instances": instances,
                "message": message, "error": False, "port_occupied": bool(listeners),
                "proxy_control": ("owned_proxy" if self._proxy_applied else "owned_direct")
                if running and running["owned"] else
                "external_unmanaged" if running else "not_running"}

    def status(self) -> dict:
        with self._lock:
            return self._status_locked()

    def start(self) -> dict:
        with self._lock:
            mutex = _kernel.CreateMutexW(None, False, "Local\\DeepSeekHarness-3080-launch")
            if not mutex:
                return {**self._status_locked(), "success": False,
                        "message": "无法取得 DSH 启动互斥锁"}
            acquired = _kernel.WaitForSingleObject(mutex, 0) in (0, 0x80)
            if not acquired:
                _kernel.CloseHandle(mutex)
                return {**self._status_locked(), "success": False,
                        "message": "DSH 正在由另一个窗口启动，请稍候"}
            try:
                return self._start_with_file_lock()
            finally:
                _kernel.ReleaseMutex(mutex)
                _kernel.CloseHandle(mutex)

    def _start_with_file_lock(self) -> dict:
        with self._lock:
            lock_dir = self.runtime_root
            lock_dir.mkdir(parents=True, exist_ok=True)
            lock_file = lock_dir / "launch.lock"
            with lock_file.open("a+b") as guard:
                guard.seek(0)
                try:
                    msvcrt.locking(guard.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    return {**self._status_locked(), "success": False,
                            "message": "DSH 正在由另一个窗口启动，请稍候"}
                try:
                    return self._start_locked()
                finally:
                    guard.seek(0)
                    msvcrt.locking(guard.fileno(), msvcrt.LK_UNLCK, 1)

    def _start_locked(self) -> dict:
        before = self._status_locked()
        if before["error"]:
            return {"success": False, **before}
        if before["running"] or before["port_occupied"] or before["instances"]:
            return {"success": False, **before,
                    "message": "DSH 已运行或正在启动；已阻止重复启动" if before["instances"]
                    else "3080 端口已被占用；已阻止启动"}
        if not _compatible_dsh_files(self.root):
            return {**before, "success": False,
                    "message": "独立 DSH 安装不完整：找不到启动文件"}
        logs = self.runtime_root / "logs" / "launches"
        logs.mkdir(parents=True, exist_ok=True)
        node = self.node_executable or shutil.which("node.exe")
        node_version = _verified_node(node)
        if not node_version:
            return {**before, "success": False,
                    "message": "找不到 Node 运行环境，请先执行安装检测"}
        environment = {**os.environ,
                       **({"DSH_HOME": str(self.home)} if self.home else {})}
        environment["PATH"] = str(Path(node).parent) + os.pathsep + environment.get("PATH", "")
        try:
            environment = child_environment(environment)
        except ProxyError as exc:
            return {**before, "success": False, "message": str(exc)}
        except Exception as exc:
            return {**before, "success": False,
                    "message": "无法安全读取 DSH 网络配置：" + type(exc).__name__}
        if environment.get("NODE_USE_ENV_PROXY") == "1":
            version = tuple(int(part) for part in node_version[1:].split("."))
            if version < (24, 5, 0):
                return {**before, "success": False,
                        "message": "所选 Node.js 版本不支持 DSH 的受控 HTTP(S) 代理；请使用 Node.js 24.5 或更新版本"}
        self._proxy_applied = environment.get("NODE_USE_ENV_PROXY") == "1"
        name = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
        out_path, err_path = logs / f"{name}.stdout.log", logs / f"{name}.stderr.log"
        try:
            with out_path.open("wb") as out, err_path.open("wb") as err:
                proc = subprocess.Popen(
                    [node, str(self.bin), "web", "--host", "127.0.0.1",
                     "--port", str(PORT), "--no-open"],
                    cwd=self.root, stdout=out, stderr=err,
                    stdin=subprocess.DEVNULL, creationflags=_CREATE_NO_WINDOW,
                    env=environment,
                )
        except OSError as exc:
            return {**before, "success": False,
                    "message": f"无法启动 DSH：{type(exc).__name__}"}
        created = _creation_id(proc.pid)
        if created is None:
            proc.terminate()
            proc.wait(timeout=5)
            return {**self._status_locked(), "success": False,
                    "message": "无法确认 DSH 进程身份，启动已取消"}
        self._owned_id = (proc.pid, created)
        self._owned_log = out_path
        self._process = proc
        record = {"pid": proc.pid, "created_at": created, "stdout": str(out_path),
                  "stderr": str(err_path), "started_at": datetime.now().isoformat()}
        try:
            (self.runtime_root / "last-launch.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            stopped = self._terminate_exact(proc.pid, created)
            if stopped:
                self._owned_id = None
                self._owned_log = None
                self._process = None
            return {**self._status_locked(), "success": False,
                    "message": "无法写入 DSH 运行记录；启动已回滚" if stopped
                    else "无法写入 DSH 运行记录，自动收尾失败，请手动停止本窗口 DSH"}
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            current = self._status_locked()
            if current["running"] and current["owned"]:
                listener = next((x for x in current["instances"] if x["owned"]), None)
                if listener and listener["address"] not in ("127.0.0.1", "::1"):
                    stopped = self._terminate_exact(proc.pid, created)
                    if stopped:
                        self._owned_id = None
                        self._owned_log = None
                        self._process = None
                    return {**self._status_locked(), "success": False,
                            "message": "DSH 未绑定到本机回环地址，启动已收尾" if stopped
                            else "DSH 未绑定到本机回环地址，自动收尾失败"}
                url = _authenticated_url(out_path, PORT)
                if url and _check_authenticated(url):
                    return {"success": True, **current, "message": "DSH 已启动，认证页面可打开"}
            if proc.poll() is not None:
                self._owned_id = None
                self._owned_log = None
                self._process = None
                return {"success": False, **self._status_locked(),
                        "message": "DSH 启动后退出，请查看独立目录中的启动日志"}
            if current["port_occupied"] and not current["owned"]:
                stopped = self._terminate_exact(proc.pid, created)
                if stopped:
                    self._owned_id = None
                    self._owned_log = None
                    self._process = None
                return {"success": False, **current,
                        "message": "3080 端口被其他进程占用，DSH 启动已取消" if stopped
                        else "3080 端口被占用且 DSH 自动收尾失败，请手动停止本窗口 DSH"}
            time.sleep(0.5)
        stopped = self._terminate_exact(proc.pid, created)
        if stopped:
            self._owned_id = None
            self._owned_log = None
            self._process = None
        return {"success": False, **self._status_locked(),
                "message": "DSH 未在 60 秒内完成认证就绪；启动已收尾，请查看独立目录日志"
                if stopped else "DSH 未在 60 秒内就绪，自动收尾失败，请手动停止本窗口 DSH"}

    @staticmethod
    def _terminate_exact(pid: int, created_at: str) -> bool:
        try:
            procs, _ = _snapshot()
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            return False
        proc = next((p for p in procs if int(p.get("ProcessId", -1)) == pid), None)
        if not proc or not _is_dsh(proc):
            return False
        handle = _kernel.OpenProcess(0x101001, False, pid)  # synchronize + terminate + query limited
        if not handle:
            return False
        try:
            made = wintypes.FILETIME()
            ended = wintypes.FILETIME()
            kernel = wintypes.FILETIME()
            user = wintypes.FILETIME()
            if not _kernel.GetProcessTimes(handle, ctypes.byref(made), ctypes.byref(ended),
                                           ctypes.byref(kernel), ctypes.byref(user)):
                return False
            actual = str((made.dwHighDateTime << 32) | made.dwLowDateTime)
            if actual != created_at:
                return False
            if not _kernel.TerminateProcess(handle, 1):
                return False
            return _kernel.WaitForSingleObject(handle, 5000) == 0
        finally:
            _kernel.CloseHandle(handle)

    def stop(self) -> dict:
        with self._lock:
            current = self._status_locked()
            if not self._owned_id:
                return {"success": False, **current,
                        "message": "本窗口未启动 DSH；外部实例保持运行"}
            pid, created = self._owned_id
            if not self._terminate_exact(pid, created):
                return {"success": False, **self._status_locked(),
                        "message": "进程身份已变化或无法停止 DSH"}
            self._owned_id = None
            self._owned_log = None
            self._process = None
            return {"success": True, **self._status_locked(), "message": "已停止本窗口启动的 DSH"}

    def force_stop(self, pid: int, created_at: str) -> dict:
        with self._lock:
            current = self._status_locked()
            selected = next((x for x in current["instances"]
                             if x["pid"] == int(pid) and x["created_at"] == str(created_at)), None)
            if not selected:
                return {"success": False, **current,
                        "message": "目标 DSH 进程已变化，请刷新状态后重试"}
            if not self._terminate_exact(int(pid), str(created_at)):
                return {"success": False, **self._status_locked(),
                        "message": "目标身份核验失败或无法强制停止"}
            if self._owned_id == (int(pid), str(created_at)):
                self._owned_id = None
                self._owned_log = None
                self._process = None
            if self._attached_id == (int(pid), str(created_at)):
                self._attached_id = None
                self._attached_url = None
                self._rpc_opener = None
                self._rpc_identity = None
            return {"success": True, **self._status_locked(),
                    "message": f"已强制停止指定 DSH（PID {pid}）"}

    def cleanup(self) -> dict:
        with self._lock:
            if not self._owned_id:
                return {"success": True, **self._status_locked(),
                        "message": "没有本窗口拥有的 DSH 需要收尾"}
            return self.stop()

    def open_web(self) -> dict:
        with self._lock:
            current = self._status_locked()
            if not current["running"]:
                return {"success": False, **current, "message": "DSH 未运行"}
            if current["owned"] and self._owned_log:
                url = _authenticated_url(self._owned_log, PORT)
            elif self._attached_id == (current["pid"], current["created_at"]):
                url = self._attached_url
            else:
                url = None
            if not url:
                return {"success": False, **current,
                        "message": "外部 DSH 的认证链接不可安全取得，请使用其原启动链接"}
            if not _check_authenticated(url):
                return {"success": False, **current,
                        "message": "DSH 认证链接不可用，请查看独立目录中的启动日志"}
            os.startfile(url)
            return {"success": True, **current, "message": "已打开 DSH 认证页面"}

    def authenticated_link(self, status: dict) -> str:
        """Return the in-memory launch link only for the current, verified process."""
        with self._lock:
            if not status.get("running") or not status.get("can_open"):
                return ""
            identity = (status.get("pid"), status.get("created_at"))
            if status.get("owned") and identity == self._owned_id and self._owned_log:
                return _authenticated_url(self._owned_log, PORT) or ""
            if identity == self._attached_id:
                return self._attached_url or ""
            return ""

    def attach_authenticated_url(self, url: str) -> dict:
        """Accept a user's original launch link in memory for this exact process."""
        with self._lock:
            current = self._status_locked()
            try:
                parsed = urlsplit(url.strip())
                valid = (current["running"] and parsed.scheme == "http"
                         and parsed.hostname in ("127.0.0.1", "localhost")
                         and parsed.port == PORT and parsed.path in ("", "/")
                         and not parsed.username and not parsed.password and not parsed.fragment
                         and bool(re.search(r"(?:^|&)token=[^&]+", parsed.query)))
            except ValueError:
                valid = False
            if not valid:
                return {**current, "success": False,
                        "message": "请提供当前本机 DSH 的原始认证启动链接"}
            opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
            try:
                with opener.open(url, timeout=6) as response:
                    if response.status != 200:
                        raise ValueError("not authenticated")
            except (HTTPError, URLError, TimeoutError, ValueError):
                return {**current, "success": False,
                        "message": "认证链接未通过当前 DSH 验证"}
            after = self._status_locked()
            if (after["pid"], after["created_at"]) != (current["pid"], current["created_at"]):
                return {**after, "success": False, "message": "DSH 进程已变化，请重新连接"}
            self._attached_url = url
            self._attached_id = (current["pid"], current["created_at"])
            self._rpc_opener = opener
            self._rpc_identity = self._attached_id
            return {**self._status_locked(), "success": True,
                    "message": "已连接当前 DSH 的认证会话"}

    def _session_locked(self):
        current = self._status_locked()
        if not current["running"]:
            raise RuntimeError("DSH 未运行")
        identity = (current["pid"], current["created_at"])
        if self._rpc_opener is not None and self._rpc_identity == identity:
            return self._rpc_opener
        if current["owned"] and self._owned_log:
            url = _authenticated_url(self._owned_log, PORT)
        elif self._attached_id == identity:
            url = self._attached_url
        else:
            url = None
        if not url:
            raise RuntimeError("需要当前 DSH 的原始认证启动链接")
        opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
        try:
            with opener.open(url, timeout=6) as response:
                if response.status != 200:
                    raise RuntimeError("DSH 认证失败")
        except (HTTPError, URLError, TimeoutError) as exc:
            raise RuntimeError("DSH 认证失败") from exc
        self._rpc_opener = opener
        self._rpc_identity = identity
        return opener

    def call_remote(self, endpoint: str, args: dict) -> dict:
        """Invoke the installed DSH Connection unary RPC with its cookie session."""
        if not re.fullmatch(r"[A-Za-z0-9_$.-]+(?:/[A-Za-z0-9_$.-]+)*", endpoint):
            raise ValueError("Invalid DSH RPC endpoint")
        with self._lock:
            opener = self._session_locked()
            rpc_id = str(uuid4())
            payload = {"type": "client-request", "rpcId": rpc_id,
                       "method": endpoint, "payload": {"args": args}}
            request = Request(f"http://127.0.0.1:{PORT}/api/{endpoint}",
                              data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                              headers={"Content-Type": "application/json"}, method="POST")
            try:
                with opener.open(request, timeout=15) as response:
                    result = json.load(response)
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
                raise RuntimeError("DSH 远程调用失败或认证已失效") from exc
            if (not isinstance(result, dict) or result.get("type") != "server-response"
                    or result.get("rpcId") != rpc_id or not isinstance(result.get("result"), dict)):
                raise RuntimeError("DSH 返回了无效的远程响应")
            return result["result"]
