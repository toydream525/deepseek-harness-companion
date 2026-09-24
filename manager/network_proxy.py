"""Application-owned outbound proxy policy. Secrets live only in Windows Credential Manager."""

from __future__ import annotations

import ctypes
import json
import os
import sys
import tempfile
import threading
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from urllib.request import getproxies

from app_paths import MANAGER

_VENDOR = Path(__file__).resolve().parent / "_vendor"
if _VENDOR.is_dir() and str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

_PATH = MANAGER / "network-proxy.json"
_TARGET = "DSH-Companion/NetworkProxy"
_LOCK = threading.RLock()
_ACTIVE = 0
_REVISION = 0
_LOOPBACK = {"localhost", "127.0.0.1", "::1"}
_PROXY_ENV = {"http_proxy", "https_proxy", "all_proxy", "node_use_env_proxy"}
_URL_KEYS = ("http_url", "https_url", "socks_url")
_hub_factory_installed = False


class ProxyError(Exception):
    """Safe, user-facing proxy error; never includes an endpoint or credential."""


class _CREDENTIAL(ctypes.Structure):
    _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD), ("Attributes", wintypes.LPVOID),
                ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]


def _credential() -> tuple[str, str] | None:
    if os.name != "nt":
        return None
    advapi = ctypes.WinDLL("Advapi32", use_last_error=True)
    advapi.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(_CREDENTIAL))]
    advapi.CredReadW.restype = wintypes.BOOL
    advapi.CredFree.argtypes = [wintypes.LPVOID]
    pointer = ctypes.POINTER(_CREDENTIAL)()
    if not advapi.CredReadW(_TARGET, 1, 0, ctypes.byref(pointer)):
        if ctypes.get_last_error() == 1168:
            return None
        raise ProxyError("Windows Credential Manager could not read proxy authentication")
    try:
        size = pointer.contents.CredentialBlobSize
        if size < 2 or size > 5120:
            raise ProxyError("Stored proxy authentication is invalid")
        raw = ctypes.string_at(pointer.contents.CredentialBlob, size)
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, list) or len(value) != 2 or not all(isinstance(x, str) for x in value):
            raise ValueError()
        return value[0], value[1]
    except (ValueError, UnicodeError):
        raise ProxyError("Stored proxy authentication is invalid") from None
    finally:
        advapi.CredFree(pointer)


def _write_credential(auth: tuple[str, str] | None) -> None:
    if os.name != "nt":
        if auth:
            raise ProxyError("Windows Credential Manager is unavailable")
        return
    advapi = ctypes.WinDLL("Advapi32", use_last_error=True)
    if auth is None:
        advapi.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        advapi.CredDeleteW.restype = wintypes.BOOL
        if not advapi.CredDeleteW(_TARGET, 1, 0) and ctypes.get_last_error() != 1168:
            raise ProxyError("Windows Credential Manager could not clear proxy authentication")
        return
    data = json.dumps(list(auth), ensure_ascii=False).encode("utf-8")
    if len(data) > 5120:
        raise ProxyError("Proxy authentication is too long")
    blob = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    item = _CREDENTIAL()
    item.Type = 1
    item.TargetName = _TARGET
    item.CredentialBlobSize = len(data)
    item.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
    item.Persist = 2
    item.UserName = "DSH Companion proxy"
    advapi.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIAL), wintypes.DWORD]
    advapi.CredWriteW.restype = wintypes.BOOL
    if not advapi.CredWriteW(ctypes.byref(item), 0):
        raise ProxyError("Windows Credential Manager could not save proxy authentication")


def _settings() -> dict:
    try:
        value = json.loads(_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"mode": "system", "http_url": "", "https_url": "", "socks_url": ""}
    except (OSError, ValueError):
        raise ProxyError("Proxy settings file is unreadable; preserve it for recovery") from None
    if not isinstance(value, dict) or value.get("mode") not in {"direct", "system", "custom"}:
        raise ProxyError("Proxy settings file is invalid; preserve it for recovery")
    result = {"mode": value["mode"]}
    for key in _URL_KEYS:
        item = value.get(key, "")
        if not isinstance(item, str):
            raise ProxyError("Proxy settings file is invalid; preserve it for recovery")
        result[key] = _clean_url(item, "socks5" if key == "socks_url" else "http") if item else ""
    return result


def _clean_url(value: str, kind: str) -> str:
    try:
        parsed = urlsplit(value.strip())
        allowed = {"socks5"} if kind == "socks5" else {"http", "https"}
        if parsed.scheme.lower() not in allowed or not parsed.hostname or parsed.port is None:
            raise ValueError()
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError()
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        return urlunsplit((parsed.scheme.lower(), f"{host}:{parsed.port}", "", "", ""))
    except (ValueError, AttributeError):
        raise ProxyError("Enter a valid proxy URL with an explicit port") from None


def _parse_input_url(value: str, kind: str) -> tuple[str, tuple[str, str] | None]:
    try:
        parsed = urlsplit(value.strip())
        auth = (unquote(parsed.username), unquote(parsed.password or "")) if parsed.username is not None else None
        return _clean_url(value, kind), auth
    except ValueError:
        raise ProxyError("Enter a valid proxy URL with an explicit port") from None


def _system_urls() -> dict:
    proxies = getproxies()
    result = {"http_url": "", "https_url": "", "socks_url": ""}
    for key, target in (("http", "http_url"), ("https", "https_url")):
        raw = proxies.get(key) or proxies.get("all") or ""
        if not raw:
            continue
        if "://" not in raw:
            raw = "http://" + raw
        try:
            parsed = urlsplit(raw)
        except ValueError:
            raise ProxyError("System proxy address is invalid") from None
        if parsed.username is not None or parsed.password is not None:
            raise ProxyError("System proxy embeds authentication; save it as a custom proxy instead")
        if parsed.scheme.lower().startswith("socks"):
            result["socks_url"] = _clean_url(raw, "socks5")
        else:
            result[target] = _clean_url(raw, "http")
    return result


def _resolved(settings: dict | None = None) -> dict:
    settings = settings or _settings()
    if settings["mode"] == "direct":
        return {"http_url": "", "https_url": "", "socks_url": ""}
    return _system_urls() if settings["mode"] == "system" else settings


def _public(settings: dict) -> dict:
    return {**settings, "auth_configured": _credential() is not None,
            "applies_to_new_requests": True, "revision": _REVISION}


def get_settings() -> dict:
    try:
        with _LOCK:
            return {"success": True, "message": "Proxy settings loaded", "data": _public(_settings())}
    except ProxyError as exc:
        return {"success": False, "message": str(exc), "data": None}


def _persist(settings: dict) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="network-proxy.", suffix=".tmp", dir=_PATH.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"schema_version": 1, **settings}, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, _PATH)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def save_settings(value: dict) -> dict:
    """Accept mode, URL fields, optional username/password or clear_auth.

    A supplied URL userinfo is moved to Credential Manager and never persisted.
    Reject changes during an active HF lease so metadata and bytes share a policy.
    """
    global _REVISION
    try:
        if not isinstance(value, dict):
            raise ProxyError("Invalid proxy settings")
        with _LOCK:
            if _ACTIVE:
                raise ProxyError("Pause the active Hugging Face task before changing proxy settings")
            previous = _settings()
            settings = {"mode": value.get("mode", previous["mode"])}
            if settings["mode"] not in {"direct", "system", "custom"}:
                raise ProxyError("Choose Direct, System proxy, or Custom proxy")
            url_auth = None
            for key in _URL_KEYS:
                raw = value.get(key, previous[key])
                if not isinstance(raw, str):
                    raise ProxyError("Invalid proxy URL")
                settings[key], embedded = _parse_input_url(raw, "socks5" if key == "socks_url" else "http") if raw.strip() else ("", None)
                if embedded:
                    if url_auth and url_auth != embedded:
                        raise ProxyError("Use one proxy authentication for the configured endpoints")
                    url_auth = embedded
            if settings["mode"] == "custom":
                if not any(settings[key] for key in _URL_KEYS):
                    raise ProxyError("Enter at least one custom proxy URL")
                if settings["socks_url"] and (settings["http_url"] or settings["https_url"]):
                    raise ProxyError("Choose either SOCKS5 or HTTP/HTTPS proxy URLs")
            if value.get("clear_auth") and (url_auth or value.get("username") or value.get("password")):
                raise ProxyError("Choose either new authentication or Clear authentication")
            username = value.get("username")
            password = value.get("password")
            if username is not None or password is not None:
                if not isinstance(username, str) or not isinstance(password, str) or not username:
                    raise ProxyError("Enter a proxy username and password")
                if url_auth and url_auth != (username, password):
                    raise ProxyError("Proxy URL and authentication fields disagree")
                url_auth = (username, password)
            if url_auth and settings["mode"] != "custom":
                raise ProxyError("Proxy authentication is available only for a custom proxy")
            new_auth = None if value.get("clear_auth") else url_auth
            old_auth = _credential()
            if new_auth is not None and os.name != "nt":
                raise ProxyError("Windows Credential Manager is unavailable")
            # Validate local policy and SOCKS support before modifying durable state.
            candidate = _resolved(settings)
            if settings["mode"] == "custom" and candidate["socks_url"]:
                _require_socks()
            if value.get("clear_auth") or new_auth is not None:
                _write_credential(new_auth)
            try:
                _persist(settings)
            except OSError:
                if value.get("clear_auth") or new_auth is not None:
                    _write_credential(old_auth)
                raise ProxyError("Could not save proxy settings") from None
            _REVISION += 1
            _reset_hub_client()
            return {"success": True, "message": "Proxy settings saved for new requests; restart owned DSH to apply its proxy", "data": _public(settings)}
    except ProxyError as exc:
        return {"success": False, "message": str(exc), "data": None}


def _require_socks() -> None:
    try:
        import socksio  # noqa: F401
    except ImportError:
        raise ProxyError("SOCKS5 support is unavailable in this installation") from None


def _proxy_auth_url(url: str, auth: tuple[str, str] | None) -> str:
    if not auth:
        return url
    parsed = urlsplit(url)
    user, password = auth
    return urlunsplit((parsed.scheme, f"{quote(user, safe='')}:{quote(password, safe='')}@{parsed.netloc}", "", "", ""))


def _proxy_for_scheme(resolved: dict, scheme: str) -> str:
    return resolved["socks_url"] or resolved[f"{scheme}_url"] or ""


def create_client(*, settings: dict | None = None, timeout=None, event_hooks=None):
    """Build an httpx client with explicit transports and a loopback bypass."""
    import httpx
    snapshot = settings or _settings()
    resolved = _resolved(snapshot)
    if resolved["socks_url"]:
        _require_socks()
    auth = _credential() if snapshot["mode"] == "custom" else None
    class _RoutingTransport(httpx.BaseTransport):
        def __init__(self):
            self.direct = httpx.HTTPTransport(trust_env=False)
            self.proxies = {scheme: httpx.HTTPTransport(proxy=_proxy_auth_url(url, auth), trust_env=False)
                            for scheme in ("http", "https") if (url := _proxy_for_scheme(resolved, scheme))}

        def handle_request(self, request):
            host = (request.url.host or "").lower().strip("[]")
            transport = self.direct if host in _LOOPBACK else self.proxies.get(request.url.scheme, self.direct)
            return transport.handle_request(request)

        def close(self):
            self.direct.close()
            for transport in self.proxies.values():
                transport.close()

    return httpx.Client(transport=_RoutingTransport(), trust_env=False, verify=True,
                        follow_redirects=True, timeout=timeout if timeout is not None else httpx.Timeout(30, connect=15),
                        event_hooks=event_hooks)


def install_hub_client_factory() -> None:
    global _hub_factory_installed
    with _LOCK:
        if not _hub_factory_installed:
            from huggingface_hub import set_client_factory
            from huggingface_hub.utils._http import hf_request_event_hook
            set_client_factory(lambda: create_client(timeout=None, event_hooks={"request": [hf_request_event_hook]}))
            _hub_factory_installed = True


def _reset_hub_client() -> None:
    if _hub_factory_installed:
        from huggingface_hub import set_client_factory
        from huggingface_hub.utils._http import hf_request_event_hook
        set_client_factory(lambda: create_client(timeout=None, event_hooks={"request": [hf_request_event_hook]}))


@contextmanager
def request_lease():
    global _ACTIVE
    with _LOCK:
        _ACTIVE += 1
    try:
        yield
    finally:
        with _LOCK:
            _ACTIVE -= 1


def child_environment(base_env: dict[str, str]) -> dict[str, str]:
    """Return environment for a new owned DSH; never mutate the parent process."""
    if not isinstance(base_env, dict):
        raise ProxyError("Invalid DSH environment")
    with _LOCK:
        settings = _settings()
        resolved = _resolved(settings)
        if resolved["socks_url"]:
            raise ProxyError("DSH does not support SOCKS5 proxy; choose HTTP/HTTPS or Direct")
        result = {key: val for key, val in base_env.items() if key.lower() not in _PROXY_ENV and key.lower() != "no_proxy"}
        bypass = [item.strip() for item in (base_env.get("NO_PROXY") or base_env.get("no_proxy") or "").split(",") if item.strip()]
        for host in ("localhost", "127.0.0.1", "::1", "[::1]"):
            if host.lower() not in {item.lower() for item in bypass}:
                bypass.append(host)
        result["NO_PROXY"] = ",".join(bypass)
        if settings["mode"] != "direct":
            auth = _credential() if settings["mode"] == "custom" else None
            for scheme in ("http", "https"):
                if url := resolved[f"{scheme}_url"]:
                    result[scheme.upper() + "_PROXY"] = _proxy_auth_url(url, auth)
            if any(resolved[f"{scheme}_url"] for scheme in ("http", "https")):
                result["NODE_USE_ENV_PROXY"] = "1"
        return result


def component_status() -> dict:
    try:
        with _LOCK:
            settings = _settings()
            resolved = _resolved(settings)
            has_http = bool(resolved["http_url"] or resolved["https_url"])
            has_socks = bool(resolved["socks_url"])
            return {"success": True, "message": "Proxy capabilities checked", "data": {
                "mode": settings["mode"], "revision": _REVISION, "active_hf_requests": _ACTIVE,
                "system_proxy_detected": bool(any(resolved[key] for key in _URL_KEYS)) if settings["mode"] == "system" else None,
                "hugging_face": "supported" if not has_socks or _socks_available() else "unavailable",
                "owned_dsh": "unsupported_socks5" if has_socks else "requires_restart" if has_http else "direct",
                "external_dsh": "unmanaged", "local_bypass": True}}
    except ProxyError as exc:
        return {"success": False, "message": str(exc), "data": None}


def _socks_available() -> bool:
    try:
        _require_socks()
        return True
    except ProxyError:
        return False


def test_connection(url: str = "https://huggingface.co") -> dict:
    """Perform a real HEAD through the selected policy; response contains no URLs/secrets."""
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None:
            raise ProxyError("Enter a valid HTTP or HTTPS test URL")
        with request_lease():
            with create_client(timeout=10) as client:
                response = client.head(url)
                status = response.status_code
        message = ("Connection succeeded" if 200 <= status < 400 else
                   "Proxy authentication failed" if status == 407 else f"Connection returned HTTP {status}")
        return {"success": 200 <= status < 400,
                "message": message,
                "data": {"status_code": status}}
    except ProxyError as exc:
        return {"success": False, "message": str(exc), "data": None}
    except Exception as exc:
        name = type(exc).__name__
        message = "Proxy authentication failed" if name == "ProxyError" else "Connection test failed; check proxy and network access"
        return {"success": False, "message": message, "data": None}
