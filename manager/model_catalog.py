"""Local GGUF library. Only GGUF headers are read; tensor payloads are never loaded."""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import tempfile
import threading
from pathlib import Path
from typing import Any
from app_paths import ROOT, MODELS as DEFAULT_MODELS, LEGACY_ROOT

LIBRARY_FILE = ROOT / "manager" / "model-library.json"
LIBRARY_BACKUP = ROOT / "manager" / "model-library.backup.json"
LEGACY_MODELS = LEGACY_ROOT / "models"
_lock = threading.RLock()
_SPLIT = re.compile(r"^(.*?)-(?P<part>\d{5})-of-(?P<count>\d{5})\.gguf$", re.I)
_WIDTH = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1,
          10: 8, 11: 8, 12: 8}
# GGUF file_type integer assignments vary across newer llama.cpp revisions.
# These are LlamaFileType values in the bundled gguf-py/gguf/constants.py, not tensor GGML types.
_FILE_TYPES = {0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 7: "Q8_0", 8: "Q5_0",
               9: "Q5_1", 10: "Q2_K", 11: "Q3_K_S", 12: "Q3_K_M", 13: "Q3_K_L",
               14: "Q4_K_S", 15: "Q4_K_M", 16: "Q5_K_S", 17: "Q5_K_M",
               18: "Q6_K", 19: "IQ2_XXS", 20: "IQ2_XS", 21: "Q2_K_S",
               22: "IQ3_XS", 23: "IQ3_XXS", 24: "IQ1_S", 25: "IQ4_NL",
               26: "IQ3_S", 27: "IQ3_M", 28: "IQ2_S", 29: "IQ2_M",
               30: "IQ4_XS", 31: "IQ1_M", 32: "BF16", 36: "TQ1_0", 37: "TQ2_0",
               38: "MXFP4_MOE", 39: "NVFP4", 40: "Q1_0", 41: "Q2_0"}
# GGMLQuantizationType's tensor block sizes from the same bundled source.
_TENSOR_SIZES = {0: (1, 4), 1: (1, 2), 2: (32, 18), 3: (32, 20), 6: (32, 22),
                 7: (32, 24), 8: (32, 34), 9: (32, 40), 10: (256, 84),
                 11: (256, 110), 12: (256, 144), 13: (256, 176), 14: (256, 210),
                 15: (256, 292), 16: (256, 66), 17: (256, 74), 18: (256, 98),
                 19: (256, 50), 20: (32, 18), 21: (256, 110), 22: (256, 82),
                 23: (256, 136), 24: (1, 1), 25: (1, 2), 26: (1, 4),
                 27: (1, 8), 28: (1, 8), 29: (256, 56), 30: (1, 2),
                 34: (256, 54), 35: (256, 66), 39: (32, 17), 40: (64, 36),
                 41: (128, 18), 42: (64, 18)}


class GGUFError(ValueError):
    pass


def _atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(value, out, ensure_ascii=False, indent=2)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _read_db() -> dict:
    for path in (LIBRARY_FILE, LIBRARY_BACKUP):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            if doc.get("schema_version") == 1 and isinstance(doc.get("models"), dict):
                return doc
        except (OSError, ValueError, AttributeError):
            pass
    return {"schema_version": 1, "models": {}}


def _save_db(doc: dict) -> None:
    if LIBRARY_FILE.exists():
        try:
            old = json.loads(LIBRARY_FILE.read_text(encoding="utf-8"))
            if old.get("schema_version") == 1:
                _atomic(LIBRARY_BACKUP, old)
        except (OSError, ValueError, AttributeError):
            pass
    _atomic(LIBRARY_FILE, doc)


def _read_exact(file, size: int) -> bytes:
    data = file.read(size)
    if len(data) != size:
        raise GGUFError("Truncated GGUF header")
    return data


def _u32(file) -> int:
    return struct.unpack("<I", _read_exact(file, 4))[0]


def _u64(file) -> int:
    return struct.unpack("<Q", _read_exact(file, 8))[0]


def _string(file) -> str:
    size = _u64(file)
    if size > 16 * 1024 * 1024:
        raise GGUFError("GGUF metadata string exceeds safety limit")
    return _read_exact(file, size).decode("utf-8", "replace")


def _value(file, kind: int, depth: int = 0) -> Any:
    if depth > 2:
        raise GGUFError("Nested GGUF metadata exceeds safety limit")
    if kind == 8:
        return _string(file)
    if kind == 9:
        element_type, count = _u32(file), _u64(file)
        if count > 1_000_000:
            raise GGUFError("GGUF metadata array exceeds safety limit")
        if element_type in _WIDTH:
            end = file.tell() + count * _WIDTH[element_type]
            if end > min(file.seek(0, 2), 64 * 1024 * 1024):
                raise GGUFError("Truncated or oversized GGUF metadata array")
            file.seek(end)
            return {"array_length": count}
        if element_type == 8:
            for _ in range(count):
                _string(file)
            return {"array_length": count}
        raise GGUFError("Unsupported GGUF metadata array type")
    if kind not in _WIDTH:
        raise GGUFError("Unsupported GGUF metadata type")
    raw = _read_exact(file, _WIDTH[kind])
    fmt = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "f",
           7: "?", 10: "Q", 11: "q", 12: "d"}[kind]
    return struct.unpack("<" + fmt, raw)[0]


def read_gguf_header(path: str | Path) -> dict:
    """Parse a bounded metadata header without reading tensor records or tensor data."""
    source = Path(path)
    with source.open("rb") as file:
        if _read_exact(file, 4) != b"GGUF":
            raise GGUFError("Not a GGUF file")
        version = _u32(file)
        if version not in (2, 3):
            raise GGUFError(f"Unsupported GGUF version {version}")
        tensor_count, metadata_count = _u64(file), _u64(file)
        if metadata_count > 100_000:
            raise GGUFError("GGUF metadata count exceeds safety limit")
        selected = {}
        for _ in range(metadata_count):
            if file.tell() > 64 * 1024 * 1024:
                raise GGUFError("GGUF header exceeds safety limit")
            key = _string(file)
            kind = _u32(file)
            value = _value(file, kind)
            if (key in {"general.architecture", "general.name", "general.file_type",
                        "general.basename", "general.size_label", "split.count", "split.no",
                        "tokenizer.chat_template", "general.alignment"}
                    or key.endswith((".context_length", ".block_count"))):
                selected[key] = value
        # Tensor descriptors contain dimensions, type and offset, but no payload bytes.
        # Validate the last claimed byte against file length to detect truncated shards.
        if tensor_count > 100_000:
            raise GGUFError("GGUF tensor count exceeds safety limit")
        max_end = 0
        payload_checked = True
        for _ in range(tensor_count):
            if file.tell() > 64 * 1024 * 1024:
                raise GGUFError("GGUF tensor directory exceeds safety limit")
            _string(file)
            dimensions = _u32(file)
            if not 1 <= dimensions <= 8:
                raise GGUFError("Invalid GGUF tensor dimensions")
            count = 1
            for _ in range(dimensions):
                count *= _u64(file)
                if count > 2**60:
                    raise GGUFError("Invalid GGUF tensor size")
            tensor_type, offset = _u32(file), _u64(file)
            block = _TENSOR_SIZES.get(tensor_type)
            if block and count % block[0] == 0:
                max_end = max(max_end, offset + count // block[0] * block[1])
            else:
                payload_checked = False
                max_end = max(max_end, offset + 1)
        alignment = selected.get("general.alignment", 32)
        if type(alignment) is not int or not 1 <= alignment <= 4096:
            raise GGUFError("Invalid GGUF tensor alignment")
        data_start = (file.tell() + alignment - 1) // alignment * alignment
        if source.stat().st_size < data_start + max_end:
            raise GGUFError("GGUF tensor data is truncated")
        architecture = selected.get("general.architecture")
        file_type = selected.get("general.file_type")
        return {
            "gguf_version": version, "tensor_count": tensor_count,
            "architecture": architecture, "name": selected.get("general.name") or source.stem,
            "file_type": file_type,
            "quantization": _FILE_TYPES.get(file_type) or (f"GGUF ftype {file_type}" if type(file_type) is int else None),
            "context_length": selected.get(f"{architecture}.context_length") if architecture else None,
            "block_count": selected.get(f"{architecture}.block_count") if architecture else None,
            "chat_template_present": bool(selected.get("tokenizer.chat_template")),
            "split_count": selected.get("split.count"), "split_no": selected.get("split.no"),
            "payload_checked": payload_checked,
        }


def _group(path: Path) -> tuple[str, list[Path], bool]:
    match = _SPLIT.match(path.name)
    if not match:
        return path.stem, [path], True
    count = int(match.group("count"))
    if not 1 <= count <= 512:
        raise GGUFError("Invalid split count")
    parts = [path.with_name(f"{match.group(1)}-{i:05d}-of-{count:05d}.gguf")
             for i in range(1, count + 1)]
    return match.group(1), parts, all(part.is_file() for part in parts)


def _row(path: Path, favorite: bool = False, source: str = "local") -> dict:
    path = path.resolve()
    group_name, files, complete = _group(path)
    first = files[0] if files[0].is_file() else path
    metadata_status = "ok"
    try:
        metadata = read_gguf_header(first)
        if complete and len(files) > 1:
            for index, part in enumerate(files):
                shard = metadata if index == 0 else read_gguf_header(part)
                if (shard.get("split_count") != len(files) or shard.get("split_no") != index
                        or shard.get("architecture") != metadata.get("architecture")):
                    raise GGUFError("GGUF shards have mismatched split metadata")
                if not shard.get("payload_checked"):
                    raise GGUFError("GGUF shard has unknown tensor payload size")
    except (OSError, GGUFError) as exc:
        metadata = {}
        metadata_status = str(exc)
    model_id = hashlib.sha256(str(first).casefold().encode("utf-8")).hexdigest()[:24]
    return {
        "id": model_id, "name": metadata.get("name") or group_name,
        "display_name": metadata.get("name") or group_name,
        "path": str(first), "files": [str(p) for p in files],
        "size_bytes": sum(p.stat().st_size for p in files if p.is_file()),
        "architecture": metadata.get("architecture"),
        "quantization": metadata.get("quantization"),
        "quantization_source": "gguf_metadata" if metadata.get("quantization") else "unknown",
        "file_type": metadata.get("file_type"),
        "context_length": metadata.get("context_length"),
        "block_count": metadata.get("block_count"),
        "chat_template_present": metadata.get("chat_template_present", False),
        "startable": metadata.get("architecture") not in ("clip", None),
        "complete": complete and metadata_status == "ok" and bool(metadata.get("payload_checked")),
        "favorite": bool(favorite), "source": source,
        "metadata_status": metadata_status,
    }


def list_models(query: str = "", favorites_only: bool = False) -> list[dict]:
    with _lock:
        rows = [dict(row) for row in _read_db()["models"].values()]
    for row in rows:
        if not all(Path(file).is_file() for file in row.get("files", [])):
            row["complete"] = False
            row["metadata_status"] = "Model file missing; rebind its path"
    for row in rows:
        row.setdefault("display_name", row.get("name") or Path(row.get("path", "")).stem)
    term = query.casefold().strip()
    if term:
        rows = [row for row in rows if term in (row.get("display_name", "") + " " + row.get("name", "") + " " + row.get("path", "") + " " +
                                                 str(row.get("architecture") or "") + " " +
                                                 str(row.get("quantization") or "")).casefold()]
    if favorites_only:
        rows = [row for row in rows if row.get("favorite")]
    return sorted(rows, key=lambda row: (not row.get("favorite", False), row.get("name", "").casefold()))


def get_model(model_id: str) -> dict | None:
    with _lock:
        row = _read_db()["models"].get(model_id)
    if not row:
        return None
    result = dict(row)
    if not all(Path(file).is_file() for file in result.get("files", [])):
        result["complete"] = False
        result["metadata_status"] = "Model file missing; rebind its path"
    return result


def verify_model(model_id: str) -> dict:
    """Recheck all registered shards and tensor spans immediately before startup."""
    prior = get_model(model_id)
    if not prior:
        return {"success": False, "message": "Model not registered"}
    try:
        current = _row(Path(prior["path"]), favorite=prior.get("favorite", False),
                       source=prior.get("source", "local"))
        if (not current["complete"] or not current["startable"]
                or current.get("architecture") != prior.get("architecture")):
            return {"success": False, "message": "Model files or architecture changed; rescan or rebind"}
        current["id"] = model_id
        current["display_name"] = prior.get("display_name") or prior.get("name") or current["name"]
        return {"success": True, "model": current}
    except (OSError, GGUFError, ValueError) as exc:
        return {"success": False, "message": str(exc)}


def add_model(path: str | Path) -> dict:
    candidate = Path(path).expanduser().resolve()
    if not candidate.is_file() or candidate.suffix.casefold() != ".gguf":
        return {"success": False, "message": "Select an existing GGUF file"}
    try:
        with _lock:
            db = _read_db()
            row = _row(candidate)
            canonical = str(Path(row["path"]).resolve()).casefold()
            existing_id = next((key for key, item in db["models"].items()
                                if str(Path(item.get("path", "")).resolve()).casefold() == canonical), None)
            if existing_id:
                row["id"] = existing_id
            previous = db["models"].get(row["id"], {})
            row["favorite"] = bool(previous.get("favorite"))
            row["display_name"] = previous.get("display_name") or previous.get("name") or row["name"]
            db["models"][row["id"]] = row
            _save_db(db)
        return {"success": True, "model": row, "message": "Model registered"}
    except (OSError, GGUFError, ValueError) as exc:
        return {"success": False, "message": str(exc)}


def scan_models(paths: list[str] | None = None) -> dict:
    if paths is None:
        paths = [str(DEFAULT_MODELS)]
    before = {row["id"] for row in list_models()}
    added = updated = incomplete = 0
    errors = []
    seen = set()
    for raw in paths:
        path = Path(raw).expanduser()
        candidates = [path] if path.is_file() else path.rglob("*.gguf") if path.is_dir() else []
        for candidate in candidates:
            try:
                group, files, _ = _group(candidate)
                first = files[0]
                if first in seen:
                    continue
                seen.add(first)
                result = add_model(first if first.is_file() else candidate)
                if result["success"]:
                    row = result["model"]
                    added += row["id"] not in before
                    updated += row["id"] in before
                    incomplete += not row["complete"]
                else:
                    errors.append({"path": str(candidate), "message": result["message"]})
            except (OSError, GGUFError, ValueError) as exc:
                errors.append({"path": str(candidate), "message": str(exc)})
    return {"success": not errors, "added": added, "updated": updated,
            "incomplete": incomplete, "errors": errors, "models": list_models()}


def set_display_name(model_id: str, value: str) -> dict:
    name = " ".join(str(value or "").split())
    if not name or len(name) > 120:
        return {"success": False, "message": "Display name must contain 1\u2013120 characters"}
    with _lock:
        db = _read_db()
        row = db["models"].get(model_id)
        if not row:
            return {"success": False, "message": "Model not registered"}
        row["display_name"] = name
        _save_db(db)
    return {"success": True, "model": dict(row), "message": "Display name saved"}


def set_favorite(model_id: str, value: bool) -> dict:
    with _lock:
        db = _read_db()
        row = db["models"].get(model_id)
        if not row:
            return {"success": False, "message": "Model not found"}
        row["favorite"] = bool(value)
        _save_db(db)
        return {"success": True, "model": row}


def rebind_model(model_id: str, path: str | Path) -> dict:
    candidate = Path(path).expanduser().resolve()
    if not candidate.is_file() or candidate.suffix.casefold() != ".gguf":
        return {"success": False, "message": "Replacement GGUF file does not exist"}
    with _lock:
        db = _read_db()
        prior = db["models"].get(model_id)
        if not prior:
            return {"success": False, "message": "Model not found"}
        row = _row(candidate, favorite=prior.get("favorite", False), source=prior.get("source", "local"))
        if row.get("metadata_status") != "ok":
            return {"success": False, "message": row["metadata_status"]}
        if (prior.get("architecture") and row.get("architecture")
                and prior["architecture"] != row["architecture"]):
            return {"success": False, "message": "Architecture changed; add this as a new model and review presets"}
        other = next((key for key, item in db["models"].items() if key != model_id
                      and str(Path(item.get("path", "")).resolve()).casefold() == str(candidate).casefold()), None)
        if other:
            return {"success": False, "message": "Replacement path is already registered"}
        row["id"] = model_id
        row["display_name"] = prior.get("display_name") or prior.get("name") or row["name"]
        db["models"][model_id] = row
        _save_db(db)
        return {"success": True, "model": row, "message": "Model path rebound; review saved presets before starting",
                "presets_require_review": True}
