"""Versioned, portable parameter presets. No credentials or executable commands."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from app_paths import MANAGER
import model_catalog
import model_capabilities

SCHEMA_VERSION = 1
STORE_FILE = MANAGER / "parameter-presets.json"
BACKUP_FILE = MANAGER / "parameter-presets.backup.json"
MAX_IMPORT_BYTES = 2 * 1024 * 1024
_lock = threading.RLock()
_ROW_KEYS = {"id", "schema_version", "name", "model_id", "model_path", "parameters",
             "source", "created_at", "updated_at", "template"}
_PARAM_KEYS = set(model_capabilities._FIELDS)


def _atomic(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(document, out, ensure_ascii=False, indent=2)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _decode(path: Path) -> dict:
    document = json.loads(path.read_text(encoding="utf-8"))
    if (type(document) is not dict or not {"schema_version", "presets"}.issubset(document)
            or set(document) - {"schema_version", "presets", "migrations"}
            or document.get("schema_version") != SCHEMA_VERSION
            or type(document.get("presets")) is not dict
            or ("migrations" in document and type(document["migrations"]) is not list)):
        raise ValueError("Invalid preset store schema")
    for key, raw in document["presets"].items():
        row, errors = _validate_row(raw, check_catalog=False)
        if errors or row["id"] != key:
            raise ValueError("Invalid preset store row")
    return document


def _load(path: Path = STORE_FILE) -> dict:
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "presets": {}}
    try:
        return _decode(path)
    except (OSError, ValueError):
        if path == STORE_FILE and BACKUP_FILE.exists():
            return _decode(BACKUP_FILE)
        raise


def _save(document: dict) -> None:
    if STORE_FILE.exists():
        previous = _decode(STORE_FILE)  # refuse to overwrite corruption silently
        _atomic(BACKUP_FILE, previous)
    _atomic(STORE_FILE, document)


def migrate_legacy_profiles() -> dict:
    """Import the five verified old profiles once, preserving zeroes and template references."""
    with _lock:
        document = _load()
        if "legacy-qwen-profiles-v1" in document.get("migrations", []):
            return {"success": True, "migrated": False}
        try:
            import backend
            cfg = backend.load_config()
            if cfg.get("legacy_source") != str(backend.LEGACY_BASE_DIR):
                return {"success": True, "migrated": False}
            model_catalog.scan_models([str(backend.LEGACY_BASE_DIR / "models")])
            models = model_catalog.list_models()
            created = 0
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            for profile_id, profile in cfg.get("profiles", {}).items():
                if not profile.get("verified_legacy_qwen"):
                    continue
                model = next((row for row in models if Path(row["path"]).resolve()
                              == Path(profile["model_path"]).resolve()), None)
                if not model:
                    continue
                preset_id = "legacy-" + profile_id
                if preset_id in document["presets"]:
                    continue
                row = {"id": preset_id, "schema_version": SCHEMA_VERSION,
                       "name": profile["name"], "model_id": model["id"],
                       "model_path": model["path"],
                       "parameters": {key: profile[key] for key in _PARAM_KEYS if key in profile},
                       "template": {"mode": "file", "path": profile["template_path"]},
                       "source": "legacy_migration", "created_at": now, "updated_at": now}
                validated, errors = _validate_row(row)
                if errors:
                    return {"success": False, "migrated": False, "message": "; ".join(errors)}
                document["presets"][preset_id] = validated
                created += 1
            document.setdefault("migrations", []).append("legacy-qwen-profiles-v1")
            _save(document)
            return {"success": True, "migrated": True, "count": created}
        except (OSError, ValueError) as exc:
            return {"success": False, "migrated": False, "message": str(exc)}


def _validate_row(raw: Any, require_catalog: bool = False,
                  check_catalog: bool = True) -> tuple[dict | None, list[str]]:
    errors = []
    if type(raw) is not dict:
        return None, ["Preset must be an object"]
    unknown = set(raw) - _ROW_KEYS
    if unknown:
        errors.append("Unknown preset fields: " + ", ".join(sorted(unknown)))
    if raw.get("schema_version") != SCHEMA_VERSION:
        errors.append("Unsupported preset schema_version")
    for field in ("id", "name", "model_id", "model_path"):
        if not isinstance(raw.get(field), str) or not raw[field].strip() or len(raw[field]) > 2048:
            errors.append(f"Invalid {field}")
    params = raw.get("parameters")
    if type(params) is not dict:
        errors.append("parameters must be an object")
        params = {}
    else:
        extra = set(params) - _PARAM_KEYS
        if extra:
            errors.append("Unsupported parameters: " + ", ".join(sorted(extra)))
        for name, value in params.items():
            if name in model_capabilities._INT_RANGES:
                low, high = model_capabilities._INT_RANGES[name]
                valid = type(value) is int and low <= value <= high
            elif name in model_capabilities._FLOAT_RANGES:
                low, high = model_capabilities._FLOAT_RANGES[name]
                valid = type(value) in (int, float) and math.isfinite(value) and low <= value <= high
            elif name in ("flash_attn", "jinja"):
                valid = type(value) is bool
            else:
                valid = isinstance(value, str) and bool(re.fullmatch(r"[a-zA-Z0-9_.-]{1,64}", value))
            if not valid:
                errors.append("Invalid parameter value: " + name)
    template = raw.get("template")
    if template is not None:
        if (type(template) is not dict or set(template) not in ({"mode"}, {"mode", "path"})
                or template.get("mode") not in ("embedded", "file")
                or (template.get("mode") == "file" and
                    (not isinstance(template.get("path"), str) or not template["path"].lower().endswith(".jinja")))
                or (template.get("mode") == "embedded" and "path" in template)):
            errors.append("Invalid template reference")
    if errors:
        return None, errors
    row = {key: raw[key] for key in _ROW_KEYS if key in raw}
    row["parameters"] = dict(params)
    catalog = model_catalog.get_model(row["model_id"]) if check_catalog else None
    if catalog:
        if Path(catalog["path"]).resolve() != Path(row["model_path"]).resolve():
            errors.append("Preset model path differs from registered model; rebind or select a new model")
        validation = model_capabilities.validate_parameters(row["model_id"], params)
        errors.extend(validation["errors"])
    elif require_catalog:
        errors.append("Model is not registered; rebind it before saving")
    return (row if not errors else None), errors


def list_presets(model_id: str | None = None) -> list[dict]:
    migrate_legacy_profiles()
    with _lock:
        rows = [dict(row) for row in _load()["presets"].values()]
    if model_id:
        rows = [row for row in rows if row.get("model_id") == model_id]
    return sorted(rows, key=lambda row: row.get("updated_at", ""), reverse=True)


def get_preset(preset_id: str) -> dict | None:
    migrate_legacy_profiles()
    with _lock:
        row = _load()["presets"].get(preset_id)
    return dict(row) if row else None


def save_preset(preset: dict, replace: bool = False) -> dict:
    migrated = migrate_legacy_profiles()
    if not migrated.get("success"):
        return migrated
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    raw = dict(preset) if isinstance(preset, dict) else {}
    raw.setdefault("id", uuid.uuid4().hex)
    raw.setdefault("schema_version", SCHEMA_VERSION)
    raw.setdefault("source", "user")
    raw.setdefault("created_at", now)
    raw["updated_at"] = now
    if raw.get("model_id") and not raw.get("model_path"):
        catalog = model_catalog.get_model(raw["model_id"])
        if catalog:
            raw["model_path"] = catalog["path"]
    row, errors = _validate_row(raw, require_catalog=True)
    if errors:
        return {"success": False, "message": "; ".join(errors), "errors": errors}
    with _lock:
        document = _load()
        old = document["presets"].get(row["id"])
        if old and not replace:
            return {"success": False, "message": "Preset exists; explicit replacement required"}
        if old:
            row["created_at"] = old.get("created_at", row["created_at"])
        document["presets"][row["id"]] = row
        _save(document)
    return {"success": True, "preset": row, "message": "Preset saved"}


def copy_preset(preset_id: str, name: str | None = None) -> dict:
    row = get_preset(preset_id)
    if not row:
        return {"success": False, "message": "Preset not found"}
    row["id"] = uuid.uuid4().hex
    row["name"] = name or row["name"] + " (copy)"
    return save_preset(row)


def reset_preset(preset_id: str) -> dict:
    row = get_preset(preset_id)
    if not row:
        return {"success": False, "message": "Preset not found"}
    row["parameters"] = {}
    return save_preset(row, replace=True)


def delete_preset(preset_id: str) -> dict:
    with _lock:
        document = _load()
        if preset_id not in document["presets"]:
            return {"success": False, "message": "Preset not found"}
        del document["presets"][preset_id]
        _save(document)
    return {"success": True, "message": "Preset removed"}


def restore_presets() -> dict:
    with _lock:
        if not BACKUP_FILE.exists():
            return {"success": False, "message": "No valid backup"}
        try:
            backup = _decode(BACKUP_FILE)
        except (OSError, ValueError):
            return {"success": False, "message": "Backup is invalid; live presets unchanged"}
        _atomic(STORE_FILE, backup)
    return {"success": True, "presets": list_presets(), "message": "Last valid backup restored"}


def export_presets(ids: list[str], path: str | Path) -> dict:
    if not isinstance(ids, list) or not ids or not all(isinstance(item, str) for item in ids):
        return {"success": False, "message": "Select one or more presets"}
    with _lock:
        store = _load()["presets"]
        missing = [item for item in ids if item not in store]
        if missing:
            return {"success": False, "message": "Selected preset no longer exists"}
        rows = [store[item] for item in dict.fromkeys(ids)]
    destination = Path(path).expanduser().resolve()
    if destination == STORE_FILE or destination == BACKUP_FILE:
        return {"success": False, "message": "Choose an export path outside the live preset store"}
    _atomic(destination, {"schema_version": SCHEMA_VERSION, "kind": "dsh-companion-presets",
                          "presets": rows})
    return {"success": True, "path": str(destination), "count": len(rows)}


def _read_import(path: str | Path) -> tuple[dict | None, str, list[str]]:
    source = Path(path).expanduser().resolve()
    try:
        if source.stat().st_size > MAX_IMPORT_BYTES:
            return None, "", ["Import exceeds size limit"]
        raw = source.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        document = json.loads(raw.decode("utf-8-sig"))
    except (OSError, ValueError, UnicodeError) as exc:
        return None, "", ["Unable to read UTF-8 JSON: " + str(exc)]
    if (type(document) is not dict or set(document) != {"schema_version", "kind", "presets"}
            or document.get("schema_version") != SCHEMA_VERSION
            or document.get("kind") != "dsh-companion-presets"
            or type(document.get("presets")) is not list):
        return None, digest, ["Unknown template fields, kind, or schema version"]
    if len(document["presets"]) > 500:
        return None, digest, ["Too many presets"]
    ids = [row.get("id") for row in document["presets"]
           if type(row) is dict and isinstance(row.get("id"), str)]
    if len(ids) != len(set(ids)):
        return None, digest, ["Duplicate preset IDs in import"]
    return document, digest, []


def import_preview(path: str | Path) -> dict:
    document, digest, errors = _read_import(path)
    if errors:
        return {"success": False, "valid": False, "errors": errors, "source_sha256": digest, "presets": []}
    rows = []
    stored = _load()["presets"]
    for raw in document["presets"]:
        row, row_errors = _validate_row(raw)
        if row:
            row = dict(row)
            row["exists"] = row["id"] in stored
            row["path_exists"] = Path(row["model_path"]).is_file()
            row["rebind_required"] = not row["path_exists"] or not model_catalog.get_model(row["model_id"])
            if isinstance(row.get("template"), dict) and row["template"].get("mode") == "file":
                row["template_rebind_required"] = not Path(row["template"]["path"]).is_file()
            rows.append(row)
        errors.extend(row_errors)
    return {"success": not errors, "valid": not errors, "errors": errors, "source_sha256": digest,
            "presets": rows, "count": len(rows)}


def import_presets(path: str | Path, actions: dict[str, str], expected_sha256: str) -> dict:
    preview = import_preview(path)
    if not preview["valid"] or not expected_sha256 or preview["source_sha256"] != expected_sha256:
        return {"success": False, "message": "Import changed since preview or preview is invalid"}
    if type(actions) is not dict:
        return {"success": False, "message": "Explicit per-preset actions required"}
    with _lock:
        # Re-read after acquiring the writer lock; the preview hash binds this commit.
        document, digest, errors = _read_import(path)
        if errors or digest != expected_sha256:
            return {"success": False, "message": "Import file changed after preview"}
        store = _load()
        changed = []
        for raw in document["presets"]:
            row, row_errors = _validate_row(raw)
            if row_errors:
                return {"success": False, "message": "; ".join(row_errors)}
            action = actions.get(row["id"])
            if action == "skip":
                continue
            if action not in ("save_as_new", "replace"):
                return {"success": False, "message": "Choose save_as_new, replace, or skip for every preset"}
            if action == "replace":
                if row["id"] not in store["presets"]:
                    return {"success": False, "message": "Replacement target does not exist"}
            else:
                row["id"] = uuid.uuid4().hex
            store["presets"][row["id"]] = row
            changed.append(row["id"])
        if changed:
            _save(store)
    return {"success": True, "imported_ids": changed, "count": len(changed),
            "message": "Presets imported; model paths must be verified before loading"}
