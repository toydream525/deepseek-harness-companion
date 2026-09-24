"""Small, shared visual feedback for native button actions.

Only the UI thread touches these helpers.  Long work remains in the caller's
worker; a click merely marks its own button until that worker reports back.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer


_active_button = None
_pending_counts: dict[int, int] = {}


def connect_button(widget, callback):
    def invoke(_checked=False):
        global _active_button
        previous = _active_button
        _active_button = widget
        try:
            callback()
        finally:
            _active_button = previous
    widget.clicked.connect(invoke)
    return widget


def active_button():
    return _active_button


def _repolish(widget):
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def begin_pending(widget):
    if widget is None:
        return
    identity = id(widget)
    count = _pending_counts.get(identity, 0)
    _pending_counts[identity] = count + 1
    if count:
        return
    widget.setProperty("feedbackLabel", widget.text())
    widget.setProperty("feedbackTooltip", widget.toolTip())
    widget.setProperty("feedbackEnabled", widget.isEnabled())
    widget.setProperty("busy", "true")
    widget.setText(f"{widget.text()} · 处理中…")
    widget.setToolTip("正在处理，请稍候；完成后可重试。")
    if widget.isEnabled():
        widget.setEnabled(False)
    _repolish(widget)


def end_pending(widget, success: bool):
    if widget is None:
        return
    identity = id(widget)
    count = _pending_counts.get(identity, 0)
    if count > 1:
        _pending_counts[identity] = count - 1
        return
    if not count:
        return
    _pending_counts.pop(identity, None)
    original = widget.property("feedbackLabel") or widget.text()
    old_tip = widget.property("feedbackTooltip") or ""
    was_enabled = bool(widget.property("feedbackEnabled"))
    widget.setProperty("busy", "false")
    widget.setProperty("feedbackResult", "success" if success else "error")
    result_text = "✓ 已完成" if success else "未完成 · 可重试"
    result_tip = "操作已完成。" if success else "操作未完成；请查看本页提示，可修改后重试。"
    widget.setText(result_text)
    widget.setToolTip(result_tip)
    if was_enabled:
        widget.setEnabled(True)
    _repolish(widget)

    def restore():
        if _pending_counts.get(identity):
            return
        try:
            if widget.text() == result_text:
                widget.setText(original)
            if widget.toolTip() == result_tip:
                widget.setToolTip(old_tip)
            widget.setProperty("feedbackResult", "")
            _repolish(widget)
        except RuntimeError:
            pass

    QTimer.singleShot(1500, restore)
