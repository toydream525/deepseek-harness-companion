"""Non-interactive deployment of one built-in Qwen preset for the packaged EXE.

The public PowerShell wrappers only pass paths and a mode. This module reuses
the application's own catalog, runtime probe and recipe importer; it does not
download weights, start a model, or alter DSH settings.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

import backend
import model_capabilities
import model_catalog
import parameter_store
import recipe_importer
import runtime_manager


MODES = {
    "Uncensored32K": "recipe-qwen38-uncensored-32k-v1",
    "UncensoredDirect32K": "recipe-qwen38-uncensored-direct-32k-v1",
    "Original32K": "recipe-qwen38-original-32k-v1",
    "Writing8K": "recipe-qwen38-writing-iq4-8k-v1",
}
_REQUEST_KEYS = {"schema_version", "mode", "engine_path", "model_path",
                 "template_path", "allow_unverified"}
_STORE_FILES = (backend.CONFIG_FILE, backend.BACKUP_FILE,
                model_catalog.LIBRARY_FILE, model_catalog.LIBRARY_BACKUP,
                parameter_store.STORE_FILE, parameter_store.BACKUP_FILE)


def _result(mode: str = "", preset_id: str = "", *, success: bool = False,
            message: str = "", errors: list[str] | None = None,
            dry_run: bool = False, **extra: Any) -> dict:
    return {"success": success, "message": message, "mode": mode,
            "preset_id": preset_id, "engine_ready": False,
            "model_ready": False, "template_ready": False,
            "created": [], "skipped": [], "unresolved": [],
            "errors": errors or [], "dry_run": dry_run, **extra}


def _read_request(path: Path) -> dict:
    if path.stat().st_size > 16 * 1024:
        raise ValueError("请求文件超过 16 KiB")
    request = json.loads(path.read_text(encoding="utf-8-sig"))
    if type(request) is not dict or set(request) != _REQUEST_KEYS:
        raise ValueError("请求字段不符合部署 schema")
    if request["schema_version"] != 1 or request["mode"] not in MODES:
        raise ValueError("部署模式或 schema 不受支持")
    if type(request["allow_unverified"]) is not bool:
        raise ValueError("allow_unverified 须为布尔值")
    if request["mode"] == "Writing8K" and not request["allow_unverified"]:
        raise ValueError("Writing8K 尚未完成推理验收，须明确允许未验证档位")
    if request["mode"] != "Writing8K" and request["allow_unverified"]:
        raise ValueError("已测档位无需 allow_unverified")
    for key in ("engine_path", "model_path", "template_path"):
        value = request[key]
        if not isinstance(value, str) or not value.strip() or len(value) > 32767:
            raise ValueError(key + " 无效")
        p = Path(value).expanduser()
        if not p.is_absolute():
            raise ValueError(key + " 必须是绝对路径")
        request[key] = str(p.resolve())
    return request


@contextmanager
def deployment_mutex(timeout_ms: int = 120000):
    """Shared GUI-startup/deployment lock; GUI must publish its owner inside it."""
    if type(timeout_ms) is not int or not 0 <= timeout_ms <= 120000:
        raise ValueError("invalid deployment mutex timeout")
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
    name = "Local\\DSHCompanionDeploy_" + hashlib.sha256(
        str(backend.DATA_ROOT).casefold().encode("utf-8")).hexdigest()[:24]
    handle = kernel.CreateMutexW(None, False, name)
    if not handle:
        raise RuntimeError("无法创建部署互斥锁")
    acquired = False
    try:
        outcome = kernel.WaitForSingleObject(handle, timeout_ms)
        if outcome not in (0, 0x80):
            raise RuntimeError("另一部署任务正在运行，请稍后重试")
        acquired = True
        yield
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def _hardware_check(mode: str) -> tuple[dict, list[str]]:
    gpu = backend.get_gpu_metrics()
    ram = backend.get_system_ram()
    details = {"gpu": gpu.get("name") if gpu.get("ok") else "unknown",
               "vram_gb": gpu.get("total_gb"), "ram_gb": ram.get("total_gb")}
    errors = []
    # These 27B presets carry 60 GPU layers (52 for the unverified IQ4). A
    # smaller/unknown GPU must not silently inherit the source-host offload.
    if not gpu.get("ok") or not isinstance(gpu.get("total_gb"), (int, float)) or gpu["total_gb"] < 15:
        errors.append("该档位按 16 GB NVIDIA 显存配置；显存不足或无法确认，请在应用内从更小模型/参数开始")
    if not isinstance(ram.get("total_gb"), (int, float)) or ram["total_gb"] < 56:
        errors.append("该参考档位按 64 GB 内存配置；内存不足或无法确认，请勿直接套用")
    if mode == "Writing8K":
        details["verified_on_source_host"] = False
    else:
        details["verified_on_source_host"] = True
    return details, errors


def _single_recipe(mode: str) -> tuple[Path, dict, dict]:
    document, _, errors = recipe_importer._read_recipe(recipe_importer._builtin_path())
    if errors or document is None:
        raise ValueError("内置配方无效")
    items = [row for row in document["presets"] if row["id"] == MODES[mode]]
    if len(items) != 1:
        raise ValueError("内置配方缺少选定模式")
    document = {**document, "presets": items}
    handle = tempfile.NamedTemporaryFile(mode="w", suffix=".json", encoding="utf-8",
                                         delete=False)
    try:
        json.dump(document, handle, ensure_ascii=False)
        handle.close()
        return Path(handle.name), document, items[0]
    except BaseException:
        handle.close()
        Path(handle.name).unlink(missing_ok=True)
        raise


@contextmanager
def _hypothetical_catalog(model: dict, executable: Path):
    original_list = model_catalog.list_models
    original_get = model_catalog.get_model
    original_flags = model_capabilities.runtime_flags
    original_legacy = model_capabilities._legacy_profile
    def list_models(query: str = "", favorites_only: bool = False):
        rows = [row for row in original_list(query, favorites_only)
                if row["id"] != model["id"]]
        return rows + [model]
    def get_model(model_id: str):
        return model if model_id == model["id"] else original_get(model_id)
    def runtime_flags(executable_arg=None):
        return original_flags(executable)
    model_catalog.list_models = list_models
    model_catalog.get_model = get_model
    model_capabilities.runtime_flags = runtime_flags
    # _legacy_profile calls backend.load_config(), which creates a first-run
    # config file. Preview must remain genuinely read-only.
    model_capabilities._legacy_profile = lambda _path: None
    try:
        yield
    finally:
        model_catalog.list_models = original_list
        model_catalog.get_model = original_get
        model_capabilities.runtime_flags = original_flags
        model_capabilities._legacy_profile = original_legacy


def _snapshot() -> dict[Path, bytes | None]:
    return {path: path.read_bytes() if path.exists() else None for path in _STORE_FILES}


def _restore(snapshot: dict[Path, bytes | None]) -> list[str]:
    errors = []
    for path, previous in snapshot.items():
        try:
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                fd, name = tempfile.mkstemp(prefix=path.name + ".rollback.", dir=path.parent)
                try:
                    with os.fdopen(fd, "wb") as out:
                        out.write(previous)
                        out.flush()
                        os.fsync(out.fileno())
                    os.replace(name, path)
                finally:
                    Path(name).unlink(missing_ok=True)
        except OSError:
            errors.append("无法回滚 " + path.name)
    return errors


def _execute(request: dict, dry_run: bool) -> dict:
    mode = request["mode"]
    preset_id = MODES[mode]
    hardware, hardware_errors = _hardware_check(mode)
    if hardware_errors:
        return _result(mode, preset_id, message="硬件检查未通过", errors=hardware_errors,
                       dry_run=dry_run, hardware=hardware)
    engine = runtime_manager.probe_executable(request["engine_path"])
    if not engine.get("success") or engine.get("backend") != "cuda":
        return _result(mode, preset_id, message="推理引擎不可用或未列出 CUDA 设备",
                       errors=[engine.get("message", "引擎探测失败")], dry_run=dry_run,
                       hardware=hardware)
    engine_path = Path(engine["path"])
    model_path = Path(request["model_path"])
    if not model_path.is_file() or model_path.suffix.casefold() != ".gguf":
        return _result(mode, preset_id, message="GGUF 文件不存在；请从对应 Hugging Face 页面手动下载",
                       errors=["model_missing"], dry_run=dry_run, hardware=hardware)
    template = Path(request["template_path"])
    if not template.is_file() or template.name.casefold() != "chat_template.jinja":
        return _result(mode, preset_id, message="固定 chat_template.jinja 不存在",
                       errors=["template_missing"], dry_run=dry_run, hardware=hardware)
    recipe_path, _, item = _single_recipe(mode)
    try:
        resource = item["model_resource"]
        model = model_catalog._row(model_path)
        if (not model["complete"] or not model["startable"] or
                model["architecture"] != resource["architecture"] or
                model["quantization"] != resource["quantization"] or
                model["size_bytes"] != resource["size_bytes"] or
                model_path.name.casefold() != resource["filename"].casefold()):
            return _result(mode, preset_id, message="GGUF 的名称、大小或元数据与选定档位不符",
                           errors=["model_identity_mismatch"], dry_run=dry_run,
                           hardware=hardware)
        with _hypothetical_catalog(model, engine_path):
            preview = recipe_importer.preview_recipe(
                recipe_path, {item["role"]: model["id"]}, str(template))
        if (not preview.get("success") or not preview.get("engine_ready") or
                preview.get("validated_count") != 1 or preview.get("unresolved")):
            errors = list(preview.get("errors") or [])
            if not preview.get("engine_ready"):
                errors.append("引擎缺少该配方所需的运行参数")
            if preview.get("unresolved"):
                errors.append("模型或模板身份尚未匹配")
            return _result(mode, preset_id, message="配置预检未通过", errors=errors,
                           dry_run=dry_run, hardware=hardware,
                           engine_ready=bool(preview.get("engine_ready")),
                           model_ready=True, template_ready=bool(preview.get("template_ready")),
                           unresolved=preview.get("unresolved") or [])
        if dry_run:
            return _result(mode, preset_id, success=True,
                           message="预检通过；未修改应用配置、未启动模型",
                           dry_run=True, hardware=hardware, engine_ready=True,
                           model_ready=True, template_ready=True)
        snapshot = _snapshot()
        try:
            selected = runtime_manager.selected_server_executable()
            if not selected or selected.resolve() != engine_path.resolve():
                engine_result = runtime_manager.select_executable(engine_path)
                if not engine_result.get("success"):
                    raise RuntimeError("引擎选择失败：" + engine_result.get("message", "unknown"))
            existing = next((x for x in model_catalog.list_models()
                             if x["id"] == model["id"] and x.get("complete") and
                             Path(x["path"]).resolve() == model_path.resolve()), None)
            if not existing:
                added = model_catalog.add_model(model_path)
                if not added.get("success"):
                    raise RuntimeError("模型入库失败：" + added.get("message", "unknown"))
            imported = recipe_importer.import_recipe(
                recipe_path, {item["role"]: model["id"]}, str(template),
                preview["source_sha256"])
            if not imported.get("success") or imported.get("unresolved") or not imported.get("engine_ready"):
                raise RuntimeError("预设导入失败：" + imported.get("message", "unknown"))
            if any(x.get("reason") == "id_owned_by_other_preset" for x in imported.get("skipped", [])):
                raise RuntimeError("同 ID 预设属于用户自定义项目，未覆盖")
            stored = parameter_store.get_preset(preset_id)
            if not stored or stored.get("model_id") != model["id"]:
                raise RuntimeError("预设未正确绑定所选模型")
            return _result(mode, preset_id, success=True,
                           message="资源已检测并导入；请在伴航选择此预设启动并试聊验证",
                           hardware=hardware, engine_ready=True, model_ready=True,
                           template_ready=True, created=imported.get("created") or [],
                           skipped=imported.get("skipped") or [], dry_run=False)
        except (OSError, ValueError, RuntimeError) as exc:
            rollback_errors = _restore(snapshot)
            return _result(mode, preset_id, message="部署失败，已尝试恢复原配置",
                           errors=[str(exc), *rollback_errors], dry_run=False,
                           hardware=hardware, rolled_back=not rollback_errors)
    finally:
        recipe_path.unlink(missing_ok=True)


def execute_deployment(request_path: Path, *, dry_run: bool) -> dict:
    """Validate and import exactly one built-in preset. Safe for EXE CLI use."""
    try:
        request = _read_request(Path(request_path))
    except (OSError, ValueError, TypeError) as exc:
        return _result(message="部署请求无效", errors=[str(exc)], dry_run=dry_run)
    mode = request["mode"]
    try:
        with deployment_mutex():
            if backend._live_ui_owner(force=True) or backend._legacy_gui_instances(force=True):
                return _result(mode, MODES[mode], message="请先关闭伴航或旧版管理器窗口",
                               errors=["ui_running"], dry_run=dry_run)
            # _scan_model_instances() calls load_config(), which creates a
            # first-run config. A dry run must not mutate that state; and a
            # fresh install with no config cannot own a configured model.
            if (not dry_run and
                    (backend.CONFIG_FILE.exists() or backend.BACKUP_FILE.exists()) and
                    backend._scan_model_instances(force=True)):
                return _result(mode, MODES[mode], message="模型服务正在运行，请先在伴航中停止",
                               errors=["model_running"], dry_run=dry_run)
            return _execute(request, dry_run)
    except (OSError, ValueError, RuntimeError) as exc:
        return _result(mode, MODES[mode], message="部署检查失败",
                       errors=[str(exc)], dry_run=dry_run)
