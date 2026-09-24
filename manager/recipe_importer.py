"""Portable, versioned Qwen recipe import/export over the existing preset store.

This module never starts models, downloads files, changes defaults or touches
credentials. All committed rows use parameter_store's validator and one atomic
store write, so a failed import cannot leave a partially imported recipe.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any

import model_catalog
import model_capabilities
import parameter_store


SCHEMA_VERSION = 1
KIND = "dsh-companion-recipe"
BUILTIN_NAME = "qwen38-original-v1.json"
MAX_RECIPE_BYTES = 2 * 1024 * 1024
_RESOURCE_KEYS = {"filename", "size_bytes", "architecture", "quantization"}
_TEMPLATE_KEYS = {"mode", "filename", "sha256", "source"}
_PRESET_KEYS = {"id", "name", "role", "phase", "verified_on_source_host",
                "model_resource", "template", "parameters"}
_TOP_KEYS = {"schema_version", "kind", "recipe_id", "recipe_version",
             "source_reference", "description", "runtime_expectations",
             "presets", "unverified_experiments"}
_ID_RE = re.compile(r"[a-z][a-z0-9_-]{2,100}\Z")
_SHA_RE = re.compile(r"[0-9a-fA-F]{64}\Z")
_VERSION_RE = re.compile(r"\d+\.\d+\.\d+\Z")
_PINNED_TEMPLATE_SOURCE = "froggeric/Qwen-Fixed-Chat-Templates v22.5 @ 855bffc"
_PINNED_TEMPLATE_LF_SHA256 = "E57684BAE4156211A55473C5A63BE976A405A37AB5BE5AE0E5ABF1DF5349C4B2"
_PINNED_TEMPLATE_CRLF_SHA256 = "4E3057E958BC01EFB8E24701EF37C809051644774C6040E37C777927D9BFE22E"


def _builtin_path() -> Path:
    root = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
    return root / "resources" / "recipes" / BUILTIN_NAME


def _safe_filename(value: Any, suffix: str) -> bool:
    return (isinstance(value, str) and 1 <= len(value) <= 255 and
            Path(value).name == value and "/" not in value and "\\" not in value and
            value.lower().endswith(suffix) and value not in (".", ".."))


def _read_recipe(path: str | Path) -> tuple[dict | None, str, list[str]]:
    try:
        source = Path(path).expanduser().resolve()
        if source.stat().st_size > MAX_RECIPE_BYTES:
            return None, "", ["配置包超过 2 MiB 上限"]
        raw = source.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        document = json.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        return None, "", ["无法读取 UTF-8 JSON：" + str(exc)]
    errors: list[str] = []
    if type(document) is not dict or set(document) != _TOP_KEYS:
        return None, digest, ["配置包顶层字段不符合受支持版本"]
    if document.get("schema_version") != SCHEMA_VERSION or document.get("kind") != KIND:
        errors.append("配置包版本或种类不受支持")
    if not isinstance(document.get("recipe_id"), str) or not _ID_RE.fullmatch(document["recipe_id"]):
        errors.append("recipe_id 无效")
    if not isinstance(document.get("recipe_version"), str) or not _VERSION_RE.fullmatch(document["recipe_version"]):
        errors.append("recipe_version 无效")
    if (not isinstance(document.get("source_reference"), str) or
            not re.fullmatch(r"public-recipe:[a-z][a-z0-9_-]{2,100}@\d+\.\d+\.\d+", document["source_reference"])):
        errors.append("公开配方来源标识无效")
    if not isinstance(document.get("description"), str) or len(document["description"]) > 2000:
        errors.append("description 无效")
    expectations = document.get("runtime_expectations")
    if (type(expectations) is not dict or set(expectations) !=
            {"engine", "mtp", "host", "context_baseline", "parallel"} or
            any(not isinstance(expectations.get(key), str) or len(expectations[key]) > 300
                for key in ("engine", "mtp", "host")) or
            type(expectations.get("context_baseline")) is not int or
            type(expectations.get("parallel")) is not int):
        errors.append("runtime_expectations 无效")
    experiments = document.get("unverified_experiments")
    if (type(experiments) is not list or len(experiments) > 30 or
            any(not isinstance(x, str) or len(x) > 300 for x in experiments)):
        errors.append("unverified_experiments 无效")
    presets = document.get("presets")
    if type(presets) is not list or not 1 <= len(presets) <= 100:
        return None, digest, errors + ["预设数须在 1 到 100 之间"]
    seen_ids: set[str] = set()
    resources: dict[str, dict] = {}
    for index, item in enumerate(presets):
        prefix = f"预设 {index + 1}："
        if type(item) is not dict or set(item) != _PRESET_KEYS:
            errors.append(prefix + "字段不符合 schema")
            continue
        if not isinstance(item["id"], str) or not _ID_RE.fullmatch(item["id"]):
            errors.append(prefix + "id 无效")
        elif item["id"] in seen_ids:
            errors.append(prefix + "id 重复")
        else:
            seen_ids.add(item["id"])
        if not isinstance(item["name"], str) or not item["name"].strip() or len(item["name"]) > 200:
            errors.append(prefix + "名称无效")
        if not isinstance(item["role"], str) or not _ID_RE.fullmatch(item["role"]):
            errors.append(prefix + "role 无效")
        if item["phase"] not in ("baseline", "advanced_unverified", "custom"):
            errors.append(prefix + "phase 无效")
        if type(item["verified_on_source_host"]) is not bool:
            errors.append(prefix + "验证标志无效")
        resource = item["model_resource"]
        if (type(resource) is not dict or set(resource) != _RESOURCE_KEYS or
                not _safe_filename(resource.get("filename"), ".gguf") or
                type(resource.get("size_bytes")) is not int or resource["size_bytes"] <= 0 or
                any(not isinstance(resource.get(key), str) or
                    not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", resource[key])
                    for key in ("architecture", "quantization"))):
            errors.append(prefix + "模型资源身份无效")
        elif item["role"] in resources and resources[item["role"]] != resource:
            errors.append(prefix + "同一 role 的资源身份冲突")
        else:
            resources[item["role"]] = resource
        template = item["template"]
        if type(template) is not dict:
            errors.append(prefix + "模板无效")
        elif template.get("mode") == "embedded":
            if set(template) != {"mode"}:
                errors.append(prefix + "内嵌模板字段无效")
        elif (set(template) != _TEMPLATE_KEYS or template.get("mode") != "file" or
              not _safe_filename(template.get("filename"), ".jinja") or
              not isinstance(template.get("sha256"), str) or
              not _SHA_RE.fullmatch(template["sha256"]) or
              not isinstance(template.get("source"), str) or len(template["source"]) > 300):
            errors.append(prefix + "外部模板身份无效")
        params = item["parameters"]
        if type(params) is not dict:
            errors.append(prefix + "parameters 无效")
        else:
            dummy = {"id": item["id"], "schema_version": 1, "name": item["name"],
                     "model_id": "unbound", "model_path": "unbound.gguf",
                     "parameters": params, "template": {"mode": "embedded"},
                     "source": "recipe-validation"}
            _, row_errors = parameter_store._validate_row(dummy, check_catalog=False)
            errors.extend(prefix + error for error in row_errors)
    return (document if not errors else None), digest, errors


def _model_candidates(resource: dict, rows: list[dict]) -> list[dict]:
    candidates = []
    for row in rows:
        if not row.get("complete") or not row.get("startable"):
            continue
        if (row.get("architecture") != resource["architecture"] or
                row.get("quantization") != resource["quantization"]):
            continue
        path = Path(str(row.get("path") or ""))
        if not path.is_file():
            continue
        size = row.get("size_bytes") or path.stat().st_size
        exact = path.name.casefold() == resource["filename"].casefold() and size == resource["size_bytes"]
        candidates.append({"model_id": row["id"], "name": row.get("name") or path.name,
                           "filename": path.name, "size_bytes": size,
                           "exact_identity": exact,
                           "valid_binding": size == resource["size_bytes"]})
    return sorted(candidates, key=lambda x: (not x["exact_identity"], x["name"].casefold()))


def _template_candidates(model_paths: list[Path], filename: str) -> list[Path]:
    paths: list[Path] = []
    for model_path in model_paths:
        for parent in list(model_path.parents)[:5]:
            paths.extend((parent / "templates" / "froggeric" / filename,
                          parent / "templates" / filename, parent / filename))
    try:
        for row in parameter_store._load()["presets"].values():
            template = row.get("template") or {}
            if template.get("mode") == "file":
                paths.append(Path(template.get("path", "")))
    except (OSError, ValueError):
        pass
    return list(dict.fromkeys(paths))


def _template_match(spec: dict, selected: Any, model_paths: list[Path]) -> tuple[str | None, list[dict]]:
    if spec["mode"] == "embedded":
        return "", []
    paths = [Path(selected)] if isinstance(selected, str) and selected else _template_candidates(model_paths, spec["filename"])
    matches = []
    for path in paths:
        try:
            if not path.is_file() or path.name.casefold() != spec["filename"].casefold():
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            expected = spec["sha256"].upper()
            pinned_equivalent = (spec.get("source") == _PINNED_TEMPLATE_SOURCE and
                                 expected in {_PINNED_TEMPLATE_LF_SHA256, _PINNED_TEMPLATE_CRLF_SHA256} and
                                 digest.upper() in {_PINNED_TEMPLATE_LF_SHA256, _PINNED_TEMPLATE_CRLF_SHA256})
            matches.append({"path": str(path.resolve()), "sha256": digest,
                            "matched": digest.casefold() == spec["sha256"].casefold() or pinned_equivalent})
        except OSError:
            continue
    valid = [x for x in matches if x["matched"]]
    # Equal hashes make multiple locations equivalent; prefer the first path
    # discovered beside the selected model over an old preset's path.
    return (valid[0]["path"] if valid else None), matches


def _engine_status(params: list[dict]) -> dict:
    runtime = model_capabilities.runtime_flags()
    required = {key for values in params for key in values}
    missing = sorted(key for key in required if not runtime.get("flags", {}).get(key))
    choices = runtime.get("cache_choices") or []
    if any(values.get(key) == "q8_0" for values in params for key in ("cache_type_k", "cache_type_v")) and "q8_0" not in choices:
        missing.append("cache:q8_0")
    if any(values.get("reasoning_format") == "deepseek" for values in params) and "deepseek" not in (runtime.get("reasoning_format_choices") or []):
        missing.append("reasoning_format:deepseek")
    for effort in {values.get("reasoning_effort") for values in params} - {None}:
        if effort not in (runtime.get("reasoning_effort_choices") or []):
            missing.append("reasoning_effort:" + effort)
    return {"ready": bool(runtime.get("known")) and not missing,
            "executable": runtime.get("executable") or "", "missing_flags": sorted(set(missing))}


def _prepare(document: dict, bindings: dict | None, template_path: Any,
             include_advanced: bool) -> dict:
    bindings = bindings or {}
    if type(bindings) is not dict or any(not isinstance(k, str) or not isinstance(v, str)
                                       for k, v in bindings.items()):
        return {"errors": ["bindings 须为 role 到模型库 ID 的映射"], "slots": [], "rows": []}
    selected = [x for x in document["presets"]
                if include_advanced or x["phase"] != "advanced_unverified"]
    catalog = model_catalog.list_models()
    by_id = {row["id"]: row for row in catalog}
    engine = _engine_status([x["parameters"] for x in selected])
    slots = []
    rows = []
    errors = []
    template_ready = True
    for role in dict.fromkeys(x["role"] for x in selected):
        presets = [x for x in selected if x["role"] == role]
        resource = presets[0]["model_resource"]
        candidates = _model_candidates(resource, catalog)
        explicit = bindings.get(role)
        good = [x for x in candidates if x["valid_binding"]]
        exact = [x for x in good if x["exact_identity"]]
        model_id = explicit if explicit in {x["model_id"] for x in good} else (
            exact[0]["model_id"] if explicit is None and len(exact) == 1 else None)
        missing = []
        if explicit and not model_id:
            missing.append({"code": "invalid_model_binding", "message": "选定模型与配置包资源身份不符"})
        elif not model_id:
            missing.append({"code": "model_binding_required", "message": "请选择已入库且资源身份匹配的 GGUF"})
        model_paths = [Path(by_id[model_id]["path"])] if model_id else [Path(by_id[x["model_id"]]["path"]) for x in good]
        template_matches = {}
        template_details = []
        for item in presets:
            spec = item["template"]
            if spec["mode"] == "embedded":
                template_matches[item["id"]] = ""
                template_details.append({"preset_id": item["id"], "mode": "embedded",
                                         "matched_path": None, "candidates": []})
                continue
            if isinstance(template_path, dict):
                selected_template = template_path.get(item["id"], template_path.get(role))
            else:
                selected_template = template_path
            matched, candidate_templates = _template_match(spec, selected_template, model_paths)
            template_matches[item["id"]] = matched
            template_details.append({"preset_id": item["id"], "mode": "file",
                                     "matched_path": matched, "candidates": candidate_templates})
            if not matched:
                template_ready = False
                missing.append({"code": "template_binding_required", "preset_id": item["id"],
                                "message": "请选择 SHA256 匹配的 Jinja 模板"})
        chosen_paths = list(dict.fromkeys(x for x in template_matches.values() if x))
        chosen_template = chosen_paths[0] if len(chosen_paths) == 1 else None
        template_candidates = [candidate for detail in template_details for candidate in detail["candidates"]]
        slot = {"id": role, "label": role.replace("_", " "), "role": role,
                "phase": presets[0]["phase"], "preset_ids": [x["id"] for x in presets],
                "verified_on_source_host": all(x["verified_on_source_host"] for x in presets),
                "matched_model_id": model_id, "candidates": candidates,
                "template_path": chosen_template, "template_candidates": template_candidates,
                "templates": template_details,
                "missing": missing, "validated": bool(model_id and not missing),
                "model_resource": resource}
        slots.append(slot)
        if not slot["validated"]:
            continue
        model = by_id[model_id]
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for item in presets:
            row = {"id": item["id"], "schema_version": 1, "name": item["name"],
                   "model_id": model_id, "model_path": model["path"],
                   "parameters": dict(item["parameters"]),
                   "template": ({"mode": "embedded"} if item["template"]["mode"] == "embedded"
                                else {"mode": "file", "path": template_matches[item["id"]]}),
                   "source": f"recipe:{document['recipe_id']}:{role}",
                   "created_at": now, "updated_at": now}
            _, row_errors = parameter_store._validate_row(row, require_catalog=False,
                                                          check_catalog=False)
            if engine["ready"]:
                validation = model_capabilities.validate_parameters(model_id, row["parameters"])
                row_errors.extend(validation.get("errors") or [])
            if row_errors:
                errors.extend(f"{item['id']}: {error}" for error in row_errors)
            else:
                rows.append(row)
    return {"slots": slots, "rows": rows, "errors": errors,
            "engine": engine, "template_ready": template_ready,
            "selected_count": len(selected)}


def preview_recipe(path: str | Path, bindings: dict | None = None,
                   template_path: str | dict | None = None,
                   include_advanced: bool = False) -> dict:
    document, digest, errors = _read_recipe(path)
    if errors:
        return {"success": False, "message": "; ".join(errors), "source_sha256": digest,
                "recipe_version": None, "slots": [], "engine_ready": False,
                "template_ready": False, "errors": errors}
    prepared = _prepare(document, bindings, template_path, include_advanced)
    errors = prepared["errors"]
    slots = prepared["slots"]
    unresolved = [slot for slot in slots if slot["missing"]]
    return {"success": not errors, "message": ("配置可导入" if not unresolved and not errors else
            "部分资源待绑定" if not errors else "; ".join(errors)),
            "recipe_id": document["recipe_id"], "recipe_version": document["recipe_version"],
            "source_sha256": digest, "slots": slots, "unresolved": unresolved,
            "engine_ready": prepared["engine"]["ready"],
            "engine": prepared["engine"], "template_ready": prepared["template_ready"],
            "validated_count": len(prepared["rows"]), "selected_count": prepared["selected_count"],
            "errors": errors, "advanced_excluded": not include_advanced}


def preview_builtin_recipe(bindings: dict | None = None,
                           template_path: str | dict | None = None) -> dict:
    return preview_recipe(_builtin_path(), bindings, template_path)


def import_recipe(path: str | Path, bindings: dict | None,
                  template_path: str | dict | None, expected_sha256: str,
                  include_advanced: bool = False) -> dict:
    if not isinstance(expected_sha256, str) or not _SHA_RE.fullmatch(expected_sha256):
        return {"success": False, "message": "请先预览并提供配置摘要", "created": [],
                "skipped": [], "unresolved": [], "errors": ["invalid_expected_sha256"], "presets": []}
    with parameter_store._lock:
        document, digest, errors = _read_recipe(path)
        if errors or digest.casefold() != expected_sha256.casefold():
            return {"success": False, "message": "配置包已变化或无效，请重新预览", "created": [],
                    "skipped": [], "unresolved": [], "errors": errors or ["source_changed"], "presets": []}
        prepared = _prepare(document, bindings, template_path, include_advanced)
        if prepared["errors"]:
            return {"success": False, "message": "参数验证失败，未写入任何预设", "created": [],
                    "skipped": [], "unresolved": [], "errors": prepared["errors"], "presets": []}
        try:
            store = parameter_store._load()
        except (OSError, ValueError) as exc:
            return {"success": False, "message": "现有预设库无效，未写入", "created": [],
                    "skipped": [], "unresolved": [], "errors": [str(exc)], "presets": []}
        created, rebound, skipped = [], [], []
        changed = False
        for row in prepared["rows"]:
            old = store["presets"].get(row["id"])
            if old is None:
                store["presets"][row["id"]] = row
                created.append(row["id"])
                changed = True
                continue
            if old.get("source") != row["source"]:
                skipped.append({"id": row["id"], "reason": "id_owned_by_other_preset"})
                continue
            updated = dict(old)
            updated["model_id"] = row["model_id"]
            updated["model_path"] = row["model_path"]
            # A user may have changed one preset to the embedded template or
            # another local file. Preserve that choice on a routine reimport;
            # an explicit template binding is the user's request to replace it.
            old_template = old.get("template") or {}
            if isinstance(template_path, dict):
                role = row["source"].rsplit(":", 1)[-1]
                explicit_template = bool(template_path.get(row["id"], template_path.get(role)))
            else:
                explicit_template = bool(template_path)
            preserve_template = not explicit_template and old_template.get("mode") == "embedded"
            if not explicit_template and old_template.get("mode") == "file":
                old_file = Path(str(old_template.get("path") or ""))
                new_file = Path(row["template"]["path"]) if row["template"]["mode"] == "file" else None
                if old_file.is_file() and new_file and old_file.resolve() != new_file.resolve():
                    try:
                        preserve_template = (hashlib.sha256(old_file.read_bytes()).digest() !=
                                             hashlib.sha256(new_file.read_bytes()).digest())
                    except OSError:
                        preserve_template = False
            updated["template"] = old_template if preserve_template else row["template"]
            _, row_errors = parameter_store._validate_row(updated, check_catalog=False)
            if prepared["engine"]["ready"]:
                row_errors.extend(model_capabilities.validate_parameters(
                    row["model_id"], updated["parameters"]).get("errors") or [])
            if row_errors:
                return {"success": False, "message": "现有自定义参数与新资源不兼容；未写入", "created": [],
                        "skipped": [], "unresolved": [], "errors": row_errors, "presets": []}
            if updated == old:
                skipped.append({"id": row["id"], "reason": "already_imported"})
            else:
                updated["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                store["presets"][row["id"]] = updated
                rebound.append(row["id"])
                changed = True
        if changed:
            try:
                parameter_store._save(store)
            except (OSError, ValueError) as exc:
                return {"success": False, "message": "预设事务写入失败", "created": [],
                        "skipped": [], "unresolved": [], "errors": [str(exc)], "presets": []}
        unresolved = [{"role": slot["role"], "preset_ids": slot["preset_ids"],
                       "missing": slot["missing"]} for slot in prepared["slots"] if slot["missing"]]
        imported = created + rebound
        return {"success": True,
                "message": ("预设已导入；运行引擎待验证" if not prepared["engine"]["ready"] else
                            "已导入可匹配预设；其余资源待绑定" if unresolved else "配置已导入"),
                "recipe_id": document["recipe_id"], "recipe_version": document["recipe_version"],
                "source_sha256": digest, "created": created, "rebound": rebound,
                "skipped": skipped, "unresolved": unresolved, "errors": [],
                "presets": [store["presets"][key] for key in imported],
                "engine_ready": prepared["engine"]["ready"],
                "template_ready": prepared["template_ready"],
                "advanced_excluded": not include_advanced}


def import_builtin_recipe(bindings: dict | None, template_path: str | dict | None,
                          expected_sha256: str, include_advanced: bool = False) -> dict:
    return import_recipe(_builtin_path(), bindings, template_path,
                         expected_sha256, include_advanced)


def export_recipe(path: str | Path, preset_ids: list[str] | None = None) -> dict:
    """Export current bound recipe rows; explicit IDs may include other presets.

    With preset_ids=None only rows with the original-prompt recipe source are
    selected. User edits to those rows are exported exactly as stored.
    """
    try:
        with parameter_store._lock:
            store = parameter_store._load()["presets"]
            if preset_ids is None:
                ids = [key for key, row in store.items()
                       if str(row.get("source", "")).startswith("recipe:qwen38-original-prompt:")]
            elif (type(preset_ids) is list and preset_ids and
                  all(isinstance(key, str) for key in preset_ids)):
                ids = list(dict.fromkeys(preset_ids))
            else:
                return {"success": False, "message": "请选择有效的预设 ID", "errors": ["invalid_preset_ids"]}
            if not ids or any(key not in store for key in ids):
                return {"success": False, "message": "没有可导出的完整预设", "errors": ["preset_missing"]}
            rows = [dict(store[key]) for key in ids]
        built, _, build_errors = _read_recipe(_builtin_path())
        if build_errors:
            return {"success": False, "message": "内置配方无效", "errors": build_errors}
        builtin_by_id = {x["id"]: x for x in built["presets"]}
        exported = []
        for row in rows:
            catalog = model_catalog.get_model(row["model_id"])
            if not catalog or not catalog.get("complete") or not catalog.get("startable"):
                raise ValueError("模型未完整入库：" + row["id"])
            model_path = Path(catalog["path"])
            if not model_path.is_file() or model_path.resolve() != Path(row["model_path"]).resolve():
                raise ValueError("预设模型路径待重新绑定：" + row["id"])
            source = str(row.get("source") or "")
            role = source.split(":", 2)[2] if source.startswith("recipe:") and source.count(":") >= 2 else "custom_" + row["id"]
            old = builtin_by_id.get(row["id"])
            template = row.get("template") or {"mode": "embedded"}
            if template["mode"] == "file":
                template_file = Path(template["path"])
                if not template_file.is_file():
                    raise ValueError("模板文件待重新绑定：" + row["id"])
                template_sha = hashlib.sha256(template_file.read_bytes()).hexdigest().upper()
                template_source = (old or {}).get("template", {}).get("source", "user-selected local template")
                if (template_source == _PINNED_TEMPLATE_SOURCE and
                        template_sha in {_PINNED_TEMPLATE_LF_SHA256, _PINNED_TEMPLATE_CRLF_SHA256}):
                    template_sha = _PINNED_TEMPLATE_LF_SHA256
                template_export = {"mode": "file", "filename": template_file.name,
                                   "sha256": template_sha, "source": template_source}
            else:
                template_export = {"mode": "embedded"}
            exported.append({"id": row["id"], "name": row["name"], "role": role,
                "phase": (old or {}).get("phase", "custom"),
                "verified_on_source_host": False,
                "model_resource": {"filename": model_path.name,
                    "size_bytes": catalog.get("size_bytes") or model_path.stat().st_size,
                    "architecture": catalog["architecture"],
                    "quantization": catalog["quantization"]},
                "template": template_export, "parameters": dict(row["parameters"])})
        document = {"schema_version": SCHEMA_VERSION, "kind": KIND,
            "recipe_id": "qwen38-original-prompt" if preset_ids is None else "exported-user-presets",
            "recipe_version": built["recipe_version"], "source_reference": built["source_reference"],
            "description": "当前已绑定预设的可移植导出；模型和模板需在目标机重新绑定。",
            "runtime_expectations": built["runtime_expectations"],
            "presets": exported, "unverified_experiments": built["unverified_experiments"]}
        destination = Path(path).expanduser().resolve()
        forbidden = {parameter_store.STORE_FILE.resolve(), parameter_store.BACKUP_FILE.resolve(),
                     _builtin_path().resolve()}
        if destination in forbidden:
            raise ValueError("请选择预设库和内置配置以外的导出路径")
        parameter_store._atomic(destination, document)
        _, digest, errors = _read_recipe(destination)
        if errors:
            raise ValueError("导出校验失败：" + "; ".join(errors))
        return {"success": True, "message": "配套配置已导出", "path": str(destination),
                "source_sha256": digest, "count": len(exported), "preset_ids": ids,
                "errors": []}
    except (OSError, ValueError, KeyError) as exc:
        return {"success": False, "message": str(exc), "errors": [str(exc)]}
