"""Resumable, single-worker Hugging Face GGUF downloads for DSH Companion.

The Hub library owns repository metadata and signed download URLs. Bytes are
streamed in bounded chunks so pause/cancel can close handles deterministically.
No credential or signed URL is serialized to disk.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import uuid
from ctypes import wintypes
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse

from app_paths import ROOT, MODELS
import model_catalog
import network_proxy

for _vendor in (Path(__file__).resolve().parent / "_vendor",
                Path(__file__).resolve().parent.parent / "_vendor"):
    if _vendor.is_dir() and str(_vendor) not in sys.path:
        sys.path.insert(0, str(_vendor))

_STATE = ROOT / "manager" / "hf-download-tasks.json"
_CREDENTIAL_TARGET = "DSH-Companion/HuggingFace"
_SHA = re.compile(r"^[0-9a-f]{40}$", re.I)
_HASH64 = re.compile(r"^[0-9a-f]{64}$", re.I)
_SPLIT = re.compile(r"^(.*?)-(\d{5})-of-(\d{5})\.gguf$", re.I)
_VALID_COMPONENT = re.compile(r"^[^<>:\"|?*\x00-\x1f]+$")
_CHUNK = 1024 * 1024
_lock = threading.RLock()
_wake = threading.Event()
_worker: threading.Thread | None = None
_stopping = False
_tasks: dict[str, dict] = {}
_state_error: str | None = None


class DownloadError(Exception):
    pass


def _hub():
    try:
        import huggingface_hub
        import httpx
        network_proxy.install_hub_client_factory()
        return huggingface_hub, httpx
    except ImportError as exc:
        raise DownloadError("Hugging Face download support is unavailable in this installation") from exc


def _safe_error(exc: BaseException) -> str:
    """Only fixed strings enter persisted errors; exceptions may contain URLs."""
    name = type(exc).__name__
    if name in {"GatedRepoError", "HfHubHTTPError"} and getattr(getattr(exc, "response", None), "status_code", None) == 403:
        return "Access denied. Accept the repository terms on huggingface.co and save a permitted token."
    if name in {"RepositoryNotFoundError", "EntryNotFoundError", "RevisionNotFoundError"}:
        return "Repository, revision, or file was not found or access is denied."
    if name in {"ConnectTimeout", "ReadTimeout", "TimeoutException"}:
        return "Hugging Face request timed out. Retry the task."
    if isinstance(exc, DownloadError):
        return str(exc)
    return "Hugging Face request failed. Check network access and retry."


def _component(value: str) -> str:
    if not isinstance(value, str) or not value or value in {".", ".."} or not _VALID_COMPONENT.fullmatch(value):
        raise DownloadError("Invalid repository file path")
    if value.rstrip(" .") != value:
        raise DownloadError("Invalid repository file path")
    if value.casefold().split(".")[0] in {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}:
        raise DownloadError("Invalid repository file path")
    return value


def _safe_path(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or value.startswith("/"):
        raise DownloadError("Invalid repository file path")
    parts = value.split("/")
    for part in parts:
        _component(part)
    return PurePosixPath(*parts)


def _inside(root: Path, candidate: Path) -> Path:
    root = root.resolve()
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise DownloadError("Destination path escapes the model folder")
    return resolved


def _repo_id(value: str) -> str:
    if not isinstance(value, str) or len(value.split("/")) != 2:
        raise DownloadError("Enter a Hugging Face model repository URL")
    for part in value.split("/"):
        _component(part)
    return value


def _parse_repo_url(url: str, revision: str | None) -> tuple[str, str | None]:
    if not isinstance(url, str):
        raise DownloadError("Enter a Hugging Face model repository URL")
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"https", ""} or (parsed.netloc and parsed.netloc.lower() not in {"huggingface.co", "www.huggingface.co"}):
        raise DownloadError("Use a huggingface.co model URL")
    path = unquote(parsed.path).strip("/") if parsed.netloc else url.strip().strip("/")
    parts = path.split("/")
    if len(parts) < 2:
        raise DownloadError("Enter a Hugging Face model repository URL")
    repo = _repo_id("/".join(parts[:2]))
    inferred = parts[3] if len(parts) >= 4 and parts[2] in {"tree", "blob", "resolve"} else None
    if len(parts) > 2 and parts[2] not in {"tree", "blob", "resolve"}:
        raise DownloadError("Use a Hugging Face model repository or file URL")
    chosen = revision if revision is not None else inferred
    if chosen is not None and (not chosen or "/" in chosen or "\\" in chosen or chosen in {".", ".."}):
        raise DownloadError("Invalid revision")
    return repo, chosen


def _token() -> str | None:
    if os.name != "nt":
        raise DownloadError("Windows Credential Manager is unavailable")
    credential = ctypes.POINTER(_CREDENTIAL)()
    advapi = ctypes.WinDLL("Advapi32", use_last_error=True)
    advapi.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(_CREDENTIAL))]
    advapi.CredReadW.restype = wintypes.BOOL
    advapi.CredFree.argtypes = [wintypes.LPVOID]
    if not advapi.CredReadW(_CREDENTIAL_TARGET, 1, 0, ctypes.byref(credential)):
        if ctypes.get_last_error() == 1168:
            return None
        raise DownloadError("Windows Credential Manager could not read the token")
    try:
        size = credential.contents.CredentialBlobSize
        if not size or size > 5120:
            raise DownloadError("Stored token is invalid")
        return ctypes.string_at(credential.contents.CredentialBlob, size).decode("utf-8")
    finally:
        advapi.CredFree(credential)


class _CREDENTIAL(ctypes.Structure):
    _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD), ("Attributes", wintypes.LPVOID),
                ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]


def set_token(token: str) -> dict:
    if not isinstance(token, str) or not token.strip() or len(token.encode("utf-8")) > 5120:
        return {"success": False, "message": "Enter a valid Hugging Face token"}
    if os.name != "nt":
        return {"success": False, "message": "Windows Credential Manager is unavailable"}
    data = token.strip().encode("utf-8")
    blob = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    item = _CREDENTIAL()
    item.Type = 1
    item.TargetName = _CREDENTIAL_TARGET
    item.CredentialBlobSize = len(data)
    item.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
    item.Persist = 2  # CRED_PERSIST_LOCAL_MACHINE, protected for this user.
    item.UserName = "Hugging Face"
    advapi = ctypes.WinDLL("Advapi32", use_last_error=True)
    advapi.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIAL), wintypes.DWORD]
    advapi.CredWriteW.restype = wintypes.BOOL
    if not advapi.CredWriteW(ctypes.byref(item), 0):
        return {"success": False, "message": "Windows Credential Manager could not save the token"}
    return {"success": True, "message": "Token saved in Windows Credential Manager"}


def has_token() -> bool:
    return bool(_token())


def clear_token() -> dict:
    if os.name != "nt":
        return {"success": False, "message": "Windows Credential Manager is unavailable"}
    advapi = ctypes.WinDLL("Advapi32", use_last_error=True)
    advapi.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    advapi.CredDeleteW.restype = wintypes.BOOL
    if not advapi.CredDeleteW(_CREDENTIAL_TARGET, 1, 0) and ctypes.get_last_error() != 1168:
        return {"success": False, "message": "Windows Credential Manager could not remove the token"}
    return {"success": True, "message": "Token removed"}


def _api():
    hub, _ = _hub()
    return hub.HfApi(token=_token() or False)


def _repo_files(repo_id: str, commit_sha: str) -> dict[str, dict]:
    api = _api()
    entries = {}
    for entry in api.list_repo_tree(repo_id, revision=commit_sha, recursive=True):
        if not hasattr(entry, "size") or not str(entry.path).lower().endswith(".gguf"):
            continue
        path = str(_safe_path(entry.path))
        size = entry.size
        if type(size) is not int or size <= 0:
            raise DownloadError("Repository has a GGUF file without a valid size")
        lfs = getattr(entry, "lfs", None)
        sha256 = (lfs.get("sha256") if isinstance(lfs, dict) else getattr(lfs, "sha256", None))
        blob_id = getattr(entry, "blob_id", None)
        entries[path] = {"path": path, "size_bytes": size,
                         "sha256": sha256.lower() if isinstance(sha256, str) and _HASH64.fullmatch(sha256) else None,
                         "blob_id": blob_id.lower() if isinstance(blob_id, str) and _SHA.fullmatch(blob_id) else None}
    return entries


def _groups(entries: dict[str, dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for path, item in entries.items():
        name = PurePosixPath(path).name
        match = _SPLIT.match(name)
        if match:
            count = int(match.group(3))
            part = int(match.group(2))
            if not 1 <= count <= 512 or not 1 <= part <= count:
                continue
            key = str(PurePosixPath(path).parent / match.group(1))
            row = grouped.setdefault(key, {"id": key, "files": [], "expected": count, "parts": set()})
            row["files"].append({"path": path, "size_bytes": item["size_bytes"]})
            row["parts"].add(part)
            if row["expected"] != count:
                row["expected"] = -1
        else:
            grouped[path] = {"id": path, "files": [{"path": path, "size_bytes": item["size_bytes"]}], "expected": 1, "parts": {1}}
    result = []
    for row in grouped.values():
        files = sorted(row["files"], key=lambda item: item["path"])
        expected = row["expected"]
        result.append({"id": row["id"], "files": files,
                       "size_bytes": sum(item["size_bytes"] for item in files),
                       "complete": expected > 0 and row["parts"] == set(range(1, expected + 1)) and len(files) == expected})
    return sorted(result, key=lambda row: row["id"].casefold())


def list_remote_gguf(url: str, revision: str | None = None) -> dict:
    try:
        with network_proxy.request_lease():
            repo_id, revision = _parse_repo_url(url, revision)
            api = _api()
            info = api.model_info(repo_id, revision=revision, files_metadata=False)
            commit_sha = str(info.sha)
            if not _SHA.fullmatch(commit_sha):
                raise DownloadError("Hugging Face did not return a commit hash")
            groups = _groups(_repo_files(repo_id, commit_sha))
        return {"success": True, "repo_id": repo_id, "revision": revision or "main",
                "commit_sha": commit_sha, "groups": groups,
                "message": f"Found {len(groups)} GGUF choice(s)" if groups else "No GGUF files found"}
    except Exception as exc:
        return {"success": False, "repo_id": None, "revision": revision,
                "commit_sha": None, "groups": [], "message": _safe_error(exc)}


def _atomic_state() -> None:
    if _state_error:
        raise DownloadError(_state_error)
    _STATE.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="hf-download-tasks.", suffix=".tmp", dir=_STATE.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"schema_version": 1, "tasks": _tasks}, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, _STATE)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _load_state() -> None:
    global _tasks, _state_error
    try:
        doc = json.loads(_STATE.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or doc.get("schema_version") != 1 or not isinstance(doc.get("tasks"), dict):
            raise ValueError("Unsupported task file format")
        for task_id, row in doc["tasks"].items():
            if (not isinstance(task_id, str) or not isinstance(row, dict)
                    or row.get("id") != task_id
                    or row.get("status") not in {"queued", "downloading", "pausing", "paused", "completed", "canceled", "failed"}
                    or not isinstance(row.get("files"), list)
                    or type(row.get("progress_bytes")) is not int
                    or type(row.get("total_bytes")) is not int):
                raise ValueError("Invalid task row")
        _tasks = doc["tasks"]
        changed = False
        for row in _tasks.values():
            if row.get("status") in {"queued", "downloading", "pausing"}:
                row["status"] = "paused"
                row["speed_bps"] = 0
                changed = True
        if changed:
            _atomic_state()
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError, AttributeError):
        # Preserve corrupted state for recovery; GUI remains importable.
        _tasks = {}
        _state_error = "Download task file is unreadable; preserve it for recovery"


def _public(row: dict) -> dict:
    return {key: value for key, value in row.items() if key not in {"_destination", "_control", "_last_update"}}


def list_download_tasks() -> list[dict]:
    with _lock:
        if _state_error:
            raise DownloadError(_state_error)
        return [_public(dict(row)) for row in _tasks.values()]


def get_download_task(task_id: str) -> dict | None:
    with _lock:
        row = _tasks.get(task_id)
        return _public(dict(row)) if row else None


def _ensure_worker() -> None:
    global _worker, _stopping
    if _stopping:
        raise DownloadError("Downloads are shutting down")
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_worker_loop, name="HF download worker", daemon=True)
        _worker.start()
    _wake.set()


def start_download(selection: dict, destination_root: str | Path | None = None) -> dict:
    try:
        if _state_error:
            raise DownloadError(_state_error)
        if not isinstance(selection, dict):
            raise DownloadError("Choose a GGUF file or complete split group")
        repo_id = _repo_id(selection.get("repo_id"))
        commit_sha = selection.get("commit_sha")
        if not isinstance(commit_sha, str) or not _SHA.fullmatch(commit_sha):
            raise DownloadError("Choose a pinned Hugging Face revision")
        group_id = selection.get("group_id")
        declared = selection.get("files")
        if not isinstance(group_id, str) or not isinstance(declared, list) or not declared:
            raise DownloadError("Choose a GGUF file or complete split group")
        with network_proxy.request_lease():
            entries = _repo_files(repo_id, commit_sha)
        group = next((row for row in _groups(entries) if row["id"] == group_id), None)
        if not group or not group["complete"] or sorted(declared, key=lambda x: x.get("path", "")) != group["files"]:
            raise DownloadError("Selected GGUF group changed or is incomplete; refresh the repository")
        root = Path(destination_root or MODELS).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        target = root / _component(repo_id.split("/")[0]) / _component(repo_id.split("/")[1]) / commit_sha[:12]
        _inside(root, target)
        for file in group["files"]:
            final = target.joinpath(*_safe_path(file["path"]).parts)
            _inside(root, final)
            if final.exists():
                raise DownloadError("Destination already contains this GGUF file")
        total = group["size_bytes"]
        free = shutil.disk_usage(root).free
        if free < total + max(100 * 1024 * 1024, total // 100):
            raise DownloadError("Insufficient free space for this download")
        task_id = uuid.uuid4().hex
        files = [{**entries[file["path"]], "progress_bytes": 0} for file in group["files"]]
        row = {"id": task_id, "status": "queued", "progress_bytes": 0,
               "total_bytes": total, "speed_bps": 0, "files": files, "error": None,
               "repo_id": repo_id, "commit_sha": commit_sha, "group_id": group_id,
               "destination": str(target), "created_at": time.time(), "_control": None}
        with _lock:
            if _stopping:
                raise DownloadError("Downloads are shutting down")
            _tasks[task_id] = row
            _atomic_state()
            _ensure_worker()
        return {"success": True, "task_id": task_id, "message": "Download queued"}
    except Exception as exc:
        return {"success": False, "task_id": None, "message": _safe_error(exc)}


def _task_action(task_id: str, action: str) -> dict:
    with _lock:
        if _state_error:
            return {"success": False, "task": None, "message": _state_error}
        if _stopping and action in {"resume", "retry"}:
            return {"success": False, "task": get_download_task(task_id), "message": "Downloads are shutting down"}
        row = _tasks.get(task_id)
        if row is None:
            return {"success": False, "task": None, "message": "Download task not found"}
        status = row["status"]
        permitted = {"pause": {"queued", "downloading"}, "resume": {"paused"},
                     "cancel": {"queued", "downloading", "pausing", "paused", "failed"},
                     "retry": {"failed", "canceled"}}
        if status not in permitted[action]:
            return {"success": False, "task": _public(dict(row)), "message": f"Cannot {action} a {status} download"}
        if action == "pause":
            row["_control"] = "pause"
            row["status"] = "pausing" if status == "downloading" else "paused"
        elif action == "cancel":
            row["_control"] = "cancel"
            row["status"] = "pausing" if status in {"downloading", "pausing"} else "canceled"
        else:
            row["_control"] = None
            row["status"] = "queued"
            row["error"] = None
        row["speed_bps"] = 0 if row["status"] != "downloading" else row["speed_bps"]
        _atomic_state()
        if row["status"] == "queued":
            _ensure_worker()
        return {"success": True, "task": _public(dict(row)), "message": f"Download {action} requested"}


def pause_download(task_id: str) -> dict:
    return _task_action(task_id, "pause")


def resume_download(task_id: str) -> dict:
    return _task_action(task_id, "resume")


def cancel_download(task_id: str) -> dict:
    return _task_action(task_id, "cancel")


def retry_download(task_id: str) -> dict:
    return _task_action(task_id, "retry")


def _progress(row: dict, file: dict, amount: int, started: float, base: int) -> None:
    with _lock:
        file["progress_bytes"] = amount
        row["progress_bytes"] = sum(item["progress_bytes"] for item in row["files"])
        elapsed = max(time.monotonic() - started, 0.001)
        row["speed_bps"] = max(0, int((row["progress_bytes"] - base) / elapsed))
        if time.monotonic() - row.get("_last_update", 0) > 1:
            row["_last_update"] = time.monotonic()
            _atomic_state()


def _check_control(row: dict) -> None:
    with _lock:
        if _stopping:
            row["_control"] = "pause"
        control = row.get("_control")
    if control:
        raise _Interrupted(control)


class _Interrupted(Exception):
    pass


def _verify_file(path: Path, meta: dict, row: dict | None = None) -> None:
    if path.stat().st_size != meta["size_bytes"]:
        raise DownloadError("Downloaded file size does not match repository metadata")
    digest256 = hashlib.sha256() if meta.get("sha256") else None
    digest1 = hashlib.sha1() if not digest256 and meta.get("blob_id") else None
    if digest1:
        digest1.update(f"blob {meta['size_bytes']}\0".encode("ascii"))
    with path.open("rb") as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            if row is not None:
                _check_control(row)
            if digest256:
                digest256.update(chunk)
            if digest1:
                digest1.update(chunk)
    if digest256 and digest256.hexdigest() != meta["sha256"]:
        raise DownloadError("Downloaded file SHA-256 does not match Hugging Face")
    if digest1 and digest1.hexdigest() != meta["blob_id"]:
        raise DownloadError("Downloaded file Git hash does not match Hugging Face")
    model_catalog.read_gguf_header(path)


def _download_file(row: dict, meta: dict, started: float, base: int) -> None:
    hub, httpx = _hub()
    target = Path(row["destination"])
    relative = _safe_path(meta["path"])
    partial = target / ".hf-incomplete" / row["id"] / Path(*relative.parts)
    partial = partial.with_name(partial.name + ".part")
    final = target.joinpath(*relative.parts)
    _inside(Path(row["destination"]), partial)
    _inside(Path(row["destination"]), final)
    partial.parent.mkdir(parents=True, exist_ok=True)
    final.parent.mkdir(parents=True, exist_ok=True)
    _inside(Path(row["destination"]), partial)
    _inside(Path(row["destination"]), final)
    offset = partial.stat().st_size if partial.exists() else 0
    if offset > meta["size_bytes"]:
        partial.unlink()
        offset = 0
    _progress(row, meta, offset, started, base)
    _check_control(row)
    if offset < meta["size_bytes"]:
        url = hub.hf_hub_url(row["repo_id"], meta["path"], revision=row["commit_sha"])
        metadata = hub.get_hf_file_metadata(url, token=_token() or False)
        if metadata.commit_hash and metadata.commit_hash.lower() != row["commit_sha"].lower():
            raise DownloadError("Repository revision changed during download")
        if metadata.size is not None and metadata.size != meta["size_bytes"]:
            raise DownloadError("Repository file size changed during download")
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        location_host = urlparse(metadata.location).hostname
        if location_host in {"huggingface.co", "www.huggingface.co"}:
            token = _token()
            if token:
                headers["Authorization"] = f"Bearer {token}"
        # Signed CDN URLs come from the official hub metadata endpoint.
        with network_proxy.create_client(timeout=httpx.Timeout(30, connect=15)) as client:
            with client.stream("GET", metadata.location, headers=headers) as response:
                if response.status_code == 403:
                    raise DownloadError("Access denied. Accept the repository terms on huggingface.co and save a permitted token.")
                if offset and response.status_code != 206:
                    raise DownloadError("Server did not honor resume range; retry after removing partial data")
                if not offset and response.status_code != 200:
                    raise DownloadError(f"Download server returned HTTP {response.status_code}")
                if offset:
                    expected_range = f"bytes {offset}-"
                    if not response.headers.get("content-range", "").startswith(expected_range):
                        raise DownloadError("Download server returned an invalid byte range")
                with partial.open("ab" if offset else "wb") as stream:
                    for chunk in response.iter_bytes(_CHUNK):
                        _check_control(row)
                        stream.write(chunk)
                        offset += len(chunk)
                        if offset > meta["size_bytes"]:
                            raise DownloadError("Download exceeded expected file size")
                        _progress(row, meta, offset, started, base)
                    stream.flush()
                    os.fsync(stream.fileno())
    _check_control(row)
    try:
        _verify_file(partial, meta, row)
    except DownloadError as exc:
        if ("does not match Hugging Face" in str(exc)
                or "size does not match repository metadata" in str(exc)):
            partial.unlink(missing_ok=True)
            _progress(row, meta, 0, started, base)
        raise
    if final.exists():
        raise DownloadError("Destination file appeared during download")
    os.replace(partial, final)


def _run_task(row: dict) -> None:
    started = time.monotonic()
    base = row["progress_bytes"]
    try:
        with network_proxy.request_lease():
            for meta in row["files"]:
                _check_control(row)
                final = Path(row["destination"]).joinpath(*_safe_path(meta["path"]).parts)
                if final.is_file():
                    _verify_file(final, meta, row)
                    _progress(row, meta, meta["size_bytes"], started, base)
                    continue
                _download_file(row, meta, started, base)
        first = Path(row["destination"]).joinpath(*_safe_path(row["files"][0]["path"]).parts)
        registered = model_catalog.add_model(first)
        if not registered.get("success") or not registered.get("model", {}).get("complete"):
            raise DownloadError("GGUF validation or model registration failed")
        with _lock:
            row["status"] = "completed"
            row["progress_bytes"] = row["total_bytes"]
            row["speed_bps"] = 0
            row["error"] = None
            row["model_id"] = registered["model"]["id"]
            _atomic_state()
    except _Interrupted as exc:
        with _lock:
            row["status"] = "canceled" if str(exc) == "cancel" else "paused"
            row["_control"] = None
            row["speed_bps"] = 0
            _atomic_state()
    except Exception as exc:
        with _lock:
            row["status"] = "failed"
            row["_control"] = None
            row["speed_bps"] = 0
            row["error"] = _safe_error(exc)
            _atomic_state()


def _worker_loop() -> None:
    while True:
        with _lock:
            if _stopping:
                return
            row = next((item for item in _tasks.values() if item["status"] == "queued"), None)
            if row:
                row["status"] = "downloading"
                row["_control"] = None
                _atomic_state()
            else:
                _wake.clear()
        if row:
            _run_task(row)
        else:
            _wake.wait()


def shutdown_downloads(wait: bool = True) -> dict:
    global _stopping
    with _lock:
        _stopping = True
        for row in _tasks.values():
            if row["status"] == "queued":
                row["status"] = "paused"
            elif row["status"] in {"downloading", "pausing"}:
                row["_control"] = "pause"
                row["status"] = "pausing"
        if not _state_error:
            _atomic_state()
        worker = _worker
        _wake.set()
    if wait and worker and worker.is_alive():
        worker.join()  # Return only after the stream and file handles close.
    with _lock:
        if wait and not _state_error:
            _atomic_state()
    return {"success": True, "message": "Downloads stopped" if wait else "Download shutdown requested"}


with _lock:
    _load_state()
