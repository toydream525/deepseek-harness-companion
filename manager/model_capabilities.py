"""Describe GGUF and installed llama-server capabilities without guessing from model names."""

from __future__ import annotations

import functools
import math
import re
import subprocess
from pathlib import Path
from typing import Any

import model_catalog

_INT_RANGES = {"ctx_size": (128, 1048576), "ngl": (-1, 999),
               "batch_size": (1, 8192), "ubatch_size": (1, 8192),
               "parallel": (1, 128), "reasoning_budget": (-1, 1048576),
               "top_k": (0, 1000)}
_FLOAT_RANGES = {"temp": (0, 5), "top_p": (0, 1), "min_p": (0, 1),
                 "presence_penalty": (-2, 2)}
_FIELDS = {
    "ctx_size": "--ctx-size", "ngl": "--n-gpu-layers", "batch_size": "--batch-size",
    "ubatch_size": "--ubatch-size", "parallel": "--parallel",
    "flash_attn": "--flash-attn", "jinja": "--jinja",
    "cache_type_k": "--cache-type-k", "cache_type_v": "--cache-type-v",
    "reasoning_format": "--reasoning-format", "reasoning_effort": "--reasoning-effort",
    "reasoning_budget": "--reasoning-budget", "temp": "--temp", "top_p": "--top-p",
    "top_k": "--top-k", "min_p": "--min-p", "presence_penalty": "--presence-penalty",
}


@functools.lru_cache(maxsize=8)
def _probe_help(path: str, mtime_ns: int) -> str:
    try:
        import runtime_manager
        _, output = runtime_manager._run(Path(path), "--help", timeout=8)
        return output
    except (OSError, subprocess.TimeoutExpired):
        return ""


def runtime_flags(executable: str | Path | None = None) -> dict:
    if not executable:
        try:
            import backend
            executable = backend.load_config().get("server_executable", "")
        except (ImportError, OSError):
            executable = ""
    if executable:
        try:
            import backend
            path = (backend.BASE_DIR / executable).resolve()
        except ImportError:
            path = Path(executable).resolve()
    else:
        path = None
    if not path or not path.is_file():
        return {"known": False, "executable": str(path) if path else "", "flags": {}, "cache_choices": []}
    help_text = _probe_help(str(path), path.stat().st_mtime_ns)
    supported = {field: bool(re.search(r"(?<![\w-])" + re.escape(flag) + r"(?:[\s,=]|$)", help_text))
                 for field, flag in _FIELDS.items()}
    # Old/new llama.cpp spellings are aliases. -ngl is used by the verified runtime.
    supported["ngl"] = supported["ngl"] or bool(re.search(r"(?<!\w)-ngl(?:\s|,|$)", help_text))
    cache_section = re.search(r"--cache-type-k\s+TYPE[^\n]*\n\s*allowed values:\s*([^\n]+)", help_text)
    cache_choices = [item.strip() for item in cache_section.group(1).split(",")] if cache_section else []
    format_section = help_text.split("--reasoning-format FORMAT", 1)[-1].split("--reasoning-effort LEVEL", 1)[0]
    format_choices = [choice for choice in ("none", "deepseek", "deepseek-legacy")
                      if re.search(r"(?m)^\s*- " + re.escape(choice) + r":", format_section)]
    effort_section = help_text.split("--reasoning-effort LEVEL", 1)[-1].split("--reasoning-budget N", 1)[0]
    effort_choices = [choice for choice in ("default", "minimal", "low", "medium", "high", "xhigh", "max")
                      if re.search(r"(?<![\w-])['\"]?" + re.escape(choice) + r"['\"]?(?![\w-])", effort_section)]
    # The direct/off preset uses llama.cpp's explicit --reasoning off switch.
    if re.search(r"--reasoning\s+\[on\|off\|auto\]", help_text):
        effort_choices.append("off")
    return {"known": bool(help_text), "executable": str(path), "flags": supported,
            "cache_choices": cache_choices, "reasoning_format_choices": format_choices,
            "reasoning_effort_choices": effort_choices}


def _legacy_profile(model_path: str) -> dict | None:
    try:
        import backend
        cfg = backend.load_config()
        if cfg.get("legacy_source") != str(backend.LEGACY_BASE_DIR):
            return None
        for profile in cfg.get("profiles", {}).values():
            if (profile.get("verified_legacy_qwen") is True
                    and Path(profile.get("model_path", "")).resolve() == Path(model_path).resolve()
                    and Path(profile.get("template_path", "")).resolve()
                    == (backend.LEGACY_BASE_DIR / "templates" / "froggeric" / "chat_template.jinja").resolve()):
                return profile
    except (ImportError, OSError, ValueError):
        pass
    return None


def describe_model(model_id: str, executable: str | Path | None = None) -> dict:
    row = model_catalog.get_model(model_id)
    if not row:
        return {"model_id": model_id, "success": False, "message": "Model not registered",
                "fields": {}, "thinking": {"mode": "unknown", "source": "unknown", "unknown": True}}
    runtime = runtime_flags(executable)
    legacy = _legacy_profile(row["path"])
    ctx_limit = row.get("context_length")
    if type(ctx_limit) is not int or ctx_limit < 128:
        ctx_limit = 1048576
    defaults = {
        "ctx_size": min(ctx_limit, 4096), "ngl": -1,
        "batch_size": 256, "ubatch_size": 256, "parallel": 1,
        "cache_type_k": "f16", "cache_type_v": "f16",
        "flash_attn": False, "jinja": bool(row.get("chat_template_present")),
        "temp": 0.8, "top_p": 0.95, "top_k": 40, "min_p": 0.0,
        "presence_penalty": 0.0,
    }
    if legacy:
        defaults.update({k: legacy[k] for k in defaults if k in legacy})
        defaults.update({"reasoning_format": legacy.get("reasoning_format"),
                         "reasoning_effort": legacy.get("reasoning_effort"),
                         "reasoning_budget": legacy.get("reasoning_budget")})
    else:
        # Runtime CLI support is observable for a copied GGUF.  A selected
        # external template still needs a real inference test before its
        # model-specific reasoning behaviour can be called verified.
        for name in ("reasoning_format", "reasoning_effort", "reasoning_budget"):
            if runtime["flags"].get(name):
                defaults[name] = None
    fields = {}
    for name, value in defaults.items():
        bounds = _INT_RANGES.get(name) or _FLOAT_RANGES.get(name)
        if name == "ctx_size":
            bounds = (128, min(1048576, ctx_limit))
        choices = None
        if name in ("cache_type_k", "cache_type_v"):
            choices = list(runtime["cache_choices"])
        elif name in ("flash_attn", "jinja"):
            choices = [False, True]
        elif name == "reasoning_effort":
            choices = (["off", "low", "medium", "high"] if legacy else
                       runtime.get("reasoning_effort_choices"))
        elif name == "reasoning_format":
            choices = [legacy.get("reasoning_format")] if legacy else runtime.get("reasoning_format_choices")
        if name in ("reasoning_format", "reasoning_effort") and not choices:
            continue
        fields[name] = {"supported_choices": choices, "default": value,
                        "range": list(bounds) if bounds else None,
                        "default_source": "verified_legacy_profile" if legacy and name in legacy else "application_policy",
                        "source": "verified_legacy_profile" if legacy and name in legacy else
                                  "gguf_metadata" if name == "ctx_size" and row.get("context_length") else
                                  "runtime_help" if runtime["flags"].get(name) else "application_default",
                        "unknown": not runtime["flags"].get(name, False)}
    return {
        "model_id": model_id, "success": True, "model_name": row["name"],
        "architecture": row.get("architecture"), "metadata_status": row.get("metadata_status"),
        "fields": fields, "runtime_flags": runtime,
        "thinking": {"mode": "verified" if legacy else "unknown",
                     "effort": legacy.get("reasoning_effort") if legacy else None,
                     "budget": legacy.get("reasoning_budget") if legacy else None,
                     "source": "verified_legacy_profile" if legacy else "unknown",
                     "unknown": not bool(legacy)},
        "tools": {"supported": None, "source": "unknown", "unknown": True},
        "management_presets": ["memory_saver", "balanced", "quality"],
    }


def validate_parameters(model_id: str, params: dict) -> dict:
    descriptor = describe_model(model_id)
    if not descriptor.get("success"):
        return {"valid": False, "errors": [descriptor.get("message")], "effective": {}}
    if not isinstance(params, dict):
        return {"valid": False, "errors": ["Parameters must be an object"], "effective": {}}
    fields = descriptor["fields"]
    effective = {name: spec["default"] for name, spec in fields.items()
                 if not spec["unknown"] and spec["default"] is not None}
    errors = []
    for name, value in params.items():
        spec = fields.get(name)
        if not spec:
            errors.append(f"Unsupported or unknown parameter: {name}")
            continue
        if spec["unknown"]:
            errors.append(f"Runtime support is unknown or unavailable for {name}")
            continue
        bounds = spec["range"]
        if name in _INT_RANGES:
            valid = type(value) is int and bounds[0] <= value <= bounds[1]
        elif name in _FLOAT_RANGES:
            valid = type(value) in (int, float) and math.isfinite(value) and bounds[0] <= value <= bounds[1]
        elif name in ("flash_attn", "jinja"):
            valid = type(value) is bool
        else:
            valid = isinstance(value, str) and value in (spec["supported_choices"] or [])
        if not valid:
            errors.append(f"Invalid value for {name}")
        else:
            effective[name] = value
    if ("ubatch_size" in effective and "batch_size" in effective
            and effective["ubatch_size"] > effective["batch_size"]):
        errors.append("ubatch_size cannot exceed batch_size")
    return {"valid": not errors, "errors": errors, "effective": effective,
            "model_id": model_id, "stored": dict(params)}


def recommend_parameters(model_id: str, preset: str = "balanced") -> dict:
    result = validate_parameters(model_id, {})
    if not result["valid"]:
        return result
    params = result["effective"]
    if preset == "memory_saver":
        if "ctx_size" in params:
            params["ctx_size"] = min(params["ctx_size"], 4096)
        for key in ("cache_type_k", "cache_type_v"):
            if key in params and "q8_0" in (describe_model(model_id)["fields"][key]["supported_choices"] or []):
                params[key] = "q8_0"
    elif preset == "quality":
        if "ctx_size" in params:
            params["ctx_size"] = min(8192, describe_model(model_id)["fields"]["ctx_size"]["range"][1])
        for key in ("cache_type_k", "cache_type_v"):
            if key in params and "f16" in (describe_model(model_id)["fields"][key]["supported_choices"] or []):
                params[key] = "f16"
    elif preset != "balanced":
        return {"valid": False, "errors": ["Unknown management preset"], "effective": {}}
    return validate_parameters(model_id, params)


def estimate_memory(model_id: str, params: dict | None = None) -> dict:
    row = model_catalog.get_model(model_id)
    if not row:
        return {"estimated_vram_gb": None, "confidence": "unknown", "notes": ["Model not registered"]}
    if not row.get("complete"):
        return {"estimated_vram_gb": None, "confidence": "unknown", "notes": ["Model files incomplete"]}
    validation = validate_parameters(model_id, params or {})
    if not validation["valid"]:
        return {"estimated_vram_gb": None, "confidence": "unknown", "notes": validation["errors"]}
    # File size is an observable lower-order proxy; KV/cache and GPU offload vary by runtime.
    return {"estimated_vram_gb": round(row["size_bytes"] / (1024 ** 3) * 1.15, 2),
            "confidence": "rough", "notes": ["Approximate full-offload weights and small overhead only",
                                            "KV cache, activation memory and partial offload are not estimated; actual VRAM may differ"]}
