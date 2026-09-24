"""Per-user Windows startup entry for the native manager."""

from __future__ import annotations

import os
import sys
import winreg
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "DSH-Companion"


def executable_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return Path(__file__).resolve().parent.parent / "DSH-Companion.exe"


def desired_command() -> str:
    return f'"{executable_path()}"'


def read_entry_raw() -> tuple[str, int] | None:
    if os.name != "nt":
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, VALUE_NAME)
    except FileNotFoundError:
        return None


def read_entry() -> str | None:
    entry = read_entry_raw()
    return entry[0] if entry is not None else None


def is_enabled() -> bool:
    return read_entry() == desired_command()


def set_enabled(enabled: bool) -> None:
    if os.name != "nt":
        raise OSError("开机启动仅支持 Windows")
    if enabled and not executable_path().is_file():
        raise FileNotFoundError(f"找不到管理器程序：{executable_path()}")
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, desired_command())
        else:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass
    if is_enabled() != enabled:
        raise OSError("开机启动设置写入后读回不一致")


def restore_entry(value: tuple[str, int] | None) -> None:
    """Restore the exact prior value after a failed config transaction."""
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if value is None:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass
        else:
            winreg.SetValueEx(key, VALUE_NAME, 0, value[1], value[0])
    if read_entry_raw() != value:
        raise OSError("无法恢复原有开机启动设置")
