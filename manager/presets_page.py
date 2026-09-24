"""Versioned parameter presets with capability-based controls and previews."""

from __future__ import annotations

import copy
import difflib
import json
import sys
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QTextEdit,
    QVBoxLayout, QWidget, QBoxLayout,
)

import model_catalog
import model_capabilities
import parameter_store
from button_feedback import connect_button

PARAMETERS = (
    ("reasoning_effort", "思考强度"), ("ctx_size", "上下文"),
    ("cache_type_k", "K 缓存"), ("cache_type_v", "V 缓存"),
    ("ngl", "GPU 层数"), ("batch_size", "批大小"),
    ("temp", "温度"), ("top_p", "Top P"),
    ("reasoning_budget", "思考预算"), ("reasoning_format", "思考格式"),
    ("ubatch_size", "微批大小"), ("parallel", "并行请求数"),
    ("flash_attn", "Flash Attention"), ("jinja", "启用 Jinja 模板解析"),
    ("top_k", "Top K"), ("min_p", "Min P"),
    ("presence_penalty", "重复惩罚"),
)
SOURCE_LABELS = {
    "verified_legacy_profile": "已验证旧预设",
    "runtime_help": "运行时支持",
    "gguf_metadata": "模型信息",
    "application_default": "应用默认",
    "unknown": "未知",
}


def _btn(label, fn, primary=False):
    widget = QPushButton(label)
    widget.setObjectName("primaryButton" if primary else "secondaryButton")
    return connect_button(widget, fn)


class PresetsPage(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self.presets = []
        self.current = None
        self.capabilities = {}
        self.fields = {}
        self.model_rows = []
        self.import_path = ""
        self.import_preview_data = {}
        self.models_loaded = False
        self.presets_loaded = False
        self.capabilities_ready = False
        self.capability_request = 0
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)
        body = QWidget()
        body.setObjectName("pageSurface")
        scroll.setWidget(body)
        page = QVBoxLayout(body)
        page.setContentsMargins(28, 24, 28, 24)
        page.setSpacing(24)
        title = QLabel("参数预设")
        title.setObjectName("pageTitle")
        page.addWidget(title)
        page.addWidget(QLabel("预设使用固定版本 JSON；未知能力保持未知，保存前验证生效参数。"))

        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        card_layout.addWidget(QLabel("模型与预设"))
        selectors = QHBoxLayout()
        self.model_combo = QComboBox()
        self.model_combo.currentIndexChanged.connect(lambda _: self._model_changed())
        self.preset_combo = QComboBox()
        self.preset_combo.currentIndexChanged.connect(self._preset_changed)
        selectors.addWidget(self.model_combo, 1)
        selectors.addWidget(self.preset_combo, 1)
        selectors.addWidget(_btn("刷新", self.refresh_all))
        card_layout.addLayout(selectors)
        template_row = QHBoxLayout()
        self.template_mode = QComboBox()
        self.template_mode.addItem("使用 GGUF 内嵌聊天模板", "embedded")
        self.template_mode.addItem("使用外部 .jinja 文件", "file")
        self.template_mode.currentIndexChanged.connect(self._template_mode_changed)
        self.template_path = QLineEdit()
        self.template_path.setPlaceholderText("仅在选择外部模板时填写 .jinja 文件")
        self.template_browse = _btn("选择模板", self.choose_template)
        template_row.addWidget(QLabel("聊天模板"))
        template_row.addWidget(self.template_mode)
        template_row.addWidget(self.template_path, 1)
        template_row.addWidget(self.template_browse)
        template_row.addWidget(_btn("复制路径", lambda: self.host.copy_text(self.template_path.text())))
        card_layout.addLayout(template_row)
        self._template_mode_changed()
        save_top = QHBoxLayout()
        for label, fn in (("另存为新预设", self.save_as), ("覆盖当前预设", self.save_replace),
                          ("查看差异", self.show_diff)):
            save_top.addWidget(_btn(label, fn, label == "另存为新预设"))
        save_top.addStretch()
        card_layout.addLayout(save_top)
        groups = QHBoxLayout()
        groups.setSpacing(18)
        self.parameter_groups = groups
        generation = QFrame()
        generation.setObjectName("subsection")
        generation_layout = QVBoxLayout(generation)
        generation_heading = QLabel("生成 · 思考、上下文与采样")
        generation_heading.setObjectName("sectionTitle")
        generation_layout.addWidget(generation_heading)
        generation_layout.addWidget(QLabel("单次输出上限在试聊或 DSH 请求中设置。"))
        self.generation_form = QFormLayout()
        self.generation_form.setSpacing(10)
        generation_layout.addLayout(self.generation_form)
        generation_layout.addStretch()
        runtime = QFrame()
        runtime.setObjectName("subsection")
        runtime_layout = QVBoxLayout(runtime)
        runtime_heading = QLabel("运行 · 显存层、KV 与批量")
        runtime_heading.setObjectName("sectionTitle")
        runtime_layout.addWidget(runtime_heading)
        self.runtime_form = QFormLayout()
        self.runtime_form.setSpacing(10)
        runtime_layout.addLayout(self.runtime_form)
        runtime_layout.addStretch()
        groups.addWidget(generation, 1)
        groups.addWidget(runtime, 1)
        card_layout.addLayout(groups)
        self.advanced_toggle = _btn("展开高级参数", self.toggle_advanced)
        advanced_row = QHBoxLayout()
        advanced_row.addWidget(self.advanced_toggle)
        advanced_row.addStretch()
        card_layout.addLayout(advanced_row)
        self.advanced_panel = QWidget()
        self.advanced_form = QFormLayout(self.advanced_panel)
        self.advanced_form.setSpacing(10)
        self.advanced_panel.setVisible(False)
        card_layout.addWidget(self.advanced_panel)
        self.source_label = QLabel("能力来源读取中…")
        self.source_label.setWordWrap(True)
        card_layout.addWidget(self.source_label)
        estimate_row = QHBoxLayout()
        estimate_row.addWidget(_btn("验证参数", self.validate))
        estimate_row.addWidget(_btn("估算显存", self.estimate))
        estimate_row.addStretch()
        card_layout.addLayout(estimate_row)
        self.estimate_label = QLabel("显存值仅为估算，实际占用以运行状态为准。")
        self.estimate_label.setWordWrap(True)
        card_layout.addWidget(self.estimate_label)
        save_bottom = QHBoxLayout()
        save_bottom.addWidget(_btn("重置预设", self.reset))
        save_bottom.addWidget(_btn("恢复整个预设库备份", self.restore))
        save_bottom.addStretch()
        card_layout.addLayout(save_bottom)
        page.addWidget(card)

        import_card = QFrame()
        import_card.setObjectName("card")
        import_layout = QVBoxLayout(import_card)
        import_layout.setContentsMargins(20, 18, 20, 18)
        import_layout.addWidget(QLabel("导入、导出与差异预览"))
        recipe_row = QHBoxLayout()
        recipe_row.addWidget(_btn("一键导入原提示词配置", self.host.import_original_prompt_recipe))
        recipe_row.addWidget(_btn("导入本地配套配置包", self.host.import_external_recipe))
        recipe_row.addWidget(_btn("导出当前配套配置", self.host.export_current_recipe))
        recipe_row.addStretch()
        import_layout.addLayout(recipe_row)
        io_top = QHBoxLayout()
        for label, fn in (("导出当前", self.export_current), ("导出全部", self.export_all),
                          ("预览导入", self.preview_import),
                          ("重绑缺失模板", self.rebind_import_template),
                          ("执行导入", self.commit_import)):
            io_top.addWidget(_btn(label, fn))
        io_top.addStretch()
        import_layout.addLayout(io_top)
        io_bottom = QHBoxLayout()
        io_bottom.addWidget(_btn("打开预设 JSON 位置", self.open_store_location))
        io_bottom.addWidget(_btn("打开 JSON 模板目录", self.open_template_location))
        io_bottom.addStretch()
        import_layout.addLayout(io_bottom)
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMinimumHeight(190)
        import_layout.addWidget(self.preview)
        page.addWidget(import_card)
        page.addStretch()
        QTimer.singleShot(0, self.refresh_all)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.parameter_groups.setDirection(
            QBoxLayout.Direction.TopToBottom if self.width() < 940
            else QBoxLayout.Direction.LeftToRight)

    def refresh_all(self):
        self.models_loaded = False
        self.presets_loaded = False
        self.host._async("preset_models", lambda: model_catalog.list_models(), self._show_models)
        self.host._async("preset_list", lambda: parameter_store.list_presets(), self._show_presets)

    def _show_models(self, rows):
        if not isinstance(rows, list):
            return
        current = self.model_combo.currentData()
        self.model_rows = rows
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for row in rows:
            self.model_combo.addItem(str(row.get("name") or row.get("id")), row.get("id"))
        if current:
            index = self.model_combo.findData(current)
            if index >= 0:
                self.model_combo.setCurrentIndex(index)
        self.model_combo.blockSignals(False)
        self.models_loaded = True
        self._sync_lists()

    def _show_presets(self, rows):
        if not isinstance(rows, list):
            return
        current = self.preset_combo.currentData()
        self.presets = rows
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        for row in rows:
            self.preset_combo.addItem(str(row.get("name") or row.get("id")), row.get("id"))
        if current:
            index = self.preset_combo.findData(current)
            if index >= 0:
                self.preset_combo.setCurrentIndex(index)
        self.preset_combo.blockSignals(False)
        self.presets_loaded = True
        self._sync_lists()

    def _sync_lists(self):
        if self.models_loaded and self.presets_loaded:
            self._preset_changed()

    def _model_changed(self, preserve_preset=False):
        model_id = self.model_combo.currentData()
        if not preserve_preset and self.current and self.current.get("model_id") != model_id:
            self.current = None
            self.preview.setPlainText("已切换模型，新预设草稿不会继承上一模型的参数。")
            self.template_mode.setCurrentIndex(0)
            self.template_path.clear()
        self.capabilities_ready = False
        self.capabilities = {}
        self.source_label.setText("正在读取此模型的能力和参数来源…")
        self.capability_request += 1
        revision = self.capability_request
        if model_id:
            self.host._async("capabilities", lambda: model_capabilities.describe_model(model_id),
                             lambda data: self._show_capabilities(model_id, revision, data))

    def _show_capabilities(self, model_id, revision, data):
        if not isinstance(data, dict):
            return
        if revision != self.capability_request or model_id != self.model_combo.currentData():
            return
        if data.get("model_id") and data.get("model_id") != model_id:
            return
        self.capabilities_ready = True
        self.capabilities = data
        for form in (self.generation_form, self.runtime_form):
            while form.rowCount():
                form.removeRow(0)
        while self.advanced_form.rowCount():
            self.advanced_form.removeRow(0)
        self.fields = {}
        fields = data.get("fields") or {}
        current_params = (self.current or {}).get("parameters") or {}
        sources = []
        generation_keys = {"reasoning_effort", "ctx_size", "reasoning_budget", "temp", "top_p"}
        runtime_keys = {"ngl", "cache_type_k", "cache_type_v", "batch_size", "ubatch_size"}
        for key, label in PARAMETERS:
            spec = fields.get(key) or {}
            choices = spec.get("supported_choices") or []
            widget = QComboBox()
            if spec.get("unknown") or not spec:
                widget.addItem("未知 · 不设置", None)
                widget.setEnabled(False)
            elif choices:
                widget.addItem("使用默认", None)
                for choice in choices:
                    widget.addItem(str(choice), choice)
                value = current_params.get(key)
                index = widget.findData(value)
                if index >= 0:
                    widget.setCurrentIndex(index)
            elif spec.get("range"):
                widget.setEditable(True)
                widget.addItem("使用默认", None)
                value = current_params.get(key, spec.get("default"))
                if value is not None:
                    widget.setEditText(str(value))
                widget.setToolTip(f"范围：{spec['range']}")
            else:
                widget.addItem("未知 · 不设置", None)
                widget.setEnabled(False)
            source = spec.get("source") or "unknown"
            sources.append(f"{label}：{SOURCE_LABELS.get(source, '未知')} ({source})")
            widget.setToolTip(f"能力依据：{SOURCE_LABELS.get(source, '未知')}\n内部来源：{source}")
            widget.setMinimumWidth(170)
            target = (self.generation_form if key in generation_keys else
                      self.runtime_form if key in runtime_keys else self.advanced_form)
            target.addRow(label, widget)
            self.fields[key] = (widget, spec)
        thinking = data.get("thinking") or {}
        thinking_mode = "已验证" if thinking.get("mode") == "verified" else "未知"
        self.source_label.setText(
            f"思考模式：{thinking_mode}。灰显项代表未确认支持，不会写入新参数。"
            "悬停控件可看具体依据。")
        self.source_label.setToolTip("\n".join(sources))

    def toggle_advanced(self):
        visible = not self.advanced_panel.isVisible()
        self.advanced_panel.setVisible(visible)
        self.advanced_toggle.setText("收起高级参数" if visible else "展开高级参数")

    def _preset_changed(self):
        preset_id = self.preset_combo.currentData()
        self.current = next((row for row in self.presets if row.get("id") == preset_id), None)
        if self.current and self.current.get("model_id"):
            index = self.model_combo.findData(self.current["model_id"])
            if index >= 0:
                self.model_combo.blockSignals(True)
                self.model_combo.setCurrentIndex(index)
                self.model_combo.blockSignals(False)
        template = (self.current or {}).get("template") or {"mode": "embedded"}
        self.template_mode.setCurrentIndex(max(0, self.template_mode.findData(template.get("mode"))))
        self.template_path.setText(template.get("path") or "")
        self._model_changed(preserve_preset=True)

    def _template_mode_changed(self):
        external = self.template_mode.currentData() == "file"
        self.template_path.setEnabled(external)
        self.template_browse.setEnabled(external)

    def choose_template(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择聊天模板", "", "Jinja 模板 (*.jinja)")
        if path:
            self.template_path.setText(path)

    def _template_ref(self):
        if self.template_mode.currentData() == "embedded":
            return {"mode": "embedded"}
        path = self.template_path.text().strip()
        if not path.lower().endswith(".jinja") or not Path(path).is_file():
            raise ValueError("外部聊天模板必须是存在的 .jinja 文件")
        return {"mode": "file", "path": str(Path(path).resolve())}

    def _parameters(self):
        values = copy.deepcopy((self.current or {}).get("parameters") or {})
        for key, (widget, spec) in self.fields.items():
            if not widget.isEnabled():
                continue
            if widget.isEditable():
                value = widget.currentText().strip()
                if not value or value == "使用默认":
                    values.pop(key, None)
                    continue
                try:
                    value = float(value) if key in ("temp", "top_p", "min_p", "presence_penalty") else int(value)
                except ValueError:
                    raise ValueError(f"{key} 需要数字")
            else:
                value = widget.currentData()
                if value is None:
                    values.pop(key, None)
                    continue
            values[key] = value
        return values

    def _draft(self):
        model_id = self.model_combo.currentData()
        if not model_id:
            raise ValueError("请先选择模型")
        if not self.capabilities_ready:
            raise ValueError("模型能力仍在读取，请稍后保存")
        if self.current and self.current.get("model_id") != model_id:
            raise ValueError("当前预设属于另一模型，请另存为新预设")
        params = self._parameters()
        self._template_ref()
        return model_id, params

    def validate(self):
        try:
            model_id, params = self._draft()
        except ValueError as exc:
            QMessageBox.warning(self, "参数验证", str(exc))
            return
        self.host._async("preset_validate", lambda: model_capabilities.validate_parameters(
            model_id, params), self._show_validation)

    def _show_validation(self, result):
        self.preview.setPlainText(json.dumps(result, ensure_ascii=False, indent=2))
        self.host._note("参数验证通过。" if result.get("valid") else "参数验证失败，请查看差异预览区。")

    def estimate(self):
        try:
            model_id, params = self._draft()
        except ValueError as exc:
            QMessageBox.warning(self, "显存估算", str(exc))
            return
        self.host._async("estimate", lambda: model_capabilities.estimate_memory(model_id, params),
                         self._show_estimate)

    def _show_estimate(self, result):
        if not isinstance(result, dict):
            return
        value = result.get("estimated_vram_gb")
        text = f"预计显存：{value:.2f} GB" if isinstance(value, (int, float)) else "暂无可靠估算"
        self.estimate_label.setText(text + f" · 置信度：{result.get('confidence', '未知')}\n"
                                    + "；".join(map(str, result.get("notes") or [])) +
                                    "\n实际占用以运行状态为准。")

    def _edited_preset(self, new_id=None, new_name=None):
        model_id, params = self._draft()
        base = copy.deepcopy(self.current or {})
        base.update({"id": new_id or base.get("id") or uuid4().hex,
                     "schema_version": 1, "name": new_name or base.get("name") or "新预设",
                     "model_id": model_id, "parameters": params})
        row = next((x for x in self.model_rows if x.get("id") == model_id), {})
        base["model_path"] = row.get("path") or base.get("model_path") or ""
        base["template"] = self._template_ref()
        return base

    def open_store_location(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(parameter_store.STORE_FILE.parent)))

    def open_template_location(self):
        root = (Path(sys._MEIPASS) if getattr(sys, "frozen", False)
                else Path(__file__).resolve().parent.parent)
        folder = root / "resources" / "parameter-presets"
        if folder.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        else:
            self.host._note("JSON 模板目录未打包，请重新安装应用。")

    def rebind_import_template(self):
        rows = [x for x in self.import_preview_data.get("presets", [])
                if isinstance(x, dict) and x.get("template_rebind_required")]
        if not rows or not self.import_path:
            self.host._note("当前导入预览没有需要重绑的模板。")
            return
        labels = [f"{x.get('name') or x['id']} · {x['id']}" for x in rows]
        choice, ok = QInputDialog.getItem(self, "重绑缺失模板", "选择预设", labels, 0, False)
        if not ok:
            return
        target = rows[labels.index(choice)]
        path, _ = QFileDialog.getOpenFileName(self, "选择新的聊天模板", "", "Jinja 模板 (*.jinja)")
        if not path:
            return
        try:
            source = Path(self.import_path)
            if source.stat().st_size > parameter_store.MAX_IMPORT_BYTES:
                raise ValueError("导入文件过大")
            document = json.loads(source.read_text(encoding="utf-8-sig"))
            for row in document.get("presets", []):
                if row.get("id") == target["id"]:
                    row["template"] = {"mode": "file", "path": str(Path(path).resolve())}
            suggested = str(source.with_name(source.stem + ".rebound.json"))
            output, _ = QFileDialog.getSaveFileName(self, "保存重绑后的导入副本", suggested, "JSON (*.json)")
            if not output:
                return
            Path(output).write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
            self.import_path = output
            self.host._async("preset_import_preview", lambda: parameter_store.import_preview(output),
                             self._show_import_preview)
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            self.host._note(f"模板重绑失败：{exc}")

    def show_diff(self):
        try:
            before = json.dumps(self.current or {}, ensure_ascii=False, indent=2, sort_keys=True).splitlines()
            after = json.dumps(self._edited_preset(), ensure_ascii=False, indent=2, sort_keys=True).splitlines()
        except ValueError as exc:
            QMessageBox.warning(self, "预设差异", str(exc))
            return
        diff = difflib.unified_diff(before, after, fromfile="已保存", tofile="待保存", lineterm="")
        self.preview.setPlainText("\n".join(diff) or "没有修改。")

    def save_as(self):
        name, ok = QInputDialog.getText(self, "另存为", "新预设名称")
        if not ok or not name.strip():
            return
        try:
            preset = self._edited_preset(uuid4().hex, name.strip())
        except ValueError as exc:
            QMessageBox.warning(self, "保存预设", str(exc))
            return
        self._validate_and_save(preset, False)

    def save_replace(self):
        if not self.current:
            self.save_as()
            return
        if self.current.get("source") in ("builtin", "default"):
            self.host._note("内置预设默认另存；请选择“另存为新预设”。")
            return
        try:
            preset = self._edited_preset()
        except ValueError as exc:
            QMessageBox.warning(self, "保存预设", str(exc))
            return
        if QMessageBox.question(self, "覆盖预设", "将保存当前预设的新版本。继续吗？") != QMessageBox.StandardButton.Yes:
            return
        self._validate_and_save(preset, True)

    def _validate_and_save(self, preset, replace):
        def job():
            validation = model_capabilities.validate_parameters(
                preset["model_id"], preset["parameters"])
            if not validation.get("valid"):
                return {"success": False, "message": "参数验证失败：" +
                        "；".join(map(str, validation.get("errors") or []))}
            return parameter_store.save_preset(preset, replace=replace)
        self.host._async("preset_save", job, self._save_done)

    def _save_done(self, result):
        self.host._note((result or {}).get("message", "预设操作完成"))
        self.refresh_all()

    def reset(self):
        preset_id = self.preset_combo.currentData()
        if preset_id:
            self.host._async("preset_reset", lambda: parameter_store.reset_preset(preset_id), self._save_done)

    def restore(self):
        if QMessageBox.question(self, "恢复预设库", "将用上次有效备份恢复整个预设库，覆盖当前全部预设。继续吗？") != QMessageBox.StandardButton.Yes:
            return
        self.host._async("preset_restore", parameter_store.restore_presets, self._save_done)

    def _export(self, ids):
        path, _ = QFileDialog.getSaveFileName(self, "导出预设", "presets.json", "JSON (*.json)")
        if path:
            self.host._async("preset_export", lambda: parameter_store.export_presets(ids, path),
                             lambda result: self.host._note((result or {}).get("message", "导出完成")))

    def export_current(self):
        preset_id = self.preset_combo.currentData()
        if preset_id:
            self._export([preset_id])

    def export_all(self):
        self._export([row["id"] for row in self.presets])

    def preview_import(self):
        path, _ = QFileDialog.getOpenFileName(self, "预览导入", "", "JSON (*.json)")
        if not path:
            return
        self.import_path = path
        self.host._async("preset_import_preview", lambda: parameter_store.import_preview(path),
                         self._show_import_preview)

    def _show_import_preview(self, result):
        self.import_preview_data = result if isinstance(result, dict) else {}
        self.preview.setPlainText(json.dumps(result, ensure_ascii=False, indent=2))
        self.host._note((result or {}).get("message", "导入预览完成"))

    def commit_import(self):
        if not self.import_path or not self.import_preview_data.get("success"):
            self.host._note("请先预览有效的 JSON 文件。")
            return
        rows = self.import_preview_data.get("presets") or self.import_preview_data.get("items") or []
        actions = {}
        for row in rows:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            choice, ok = QInputDialog.getItem(
                self, "导入方式", f"{row.get('name') or row['id']}：请选择处理方式",
                ["另存为新预设", "替换已有", "跳过"], 0, False)
            if not ok:
                return
            actions[row["id"]] = {"另存为新预设": "save_as_new", "替换已有": "replace",
                                   "跳过": "skip"}[choice]
        preview_hash = self.import_preview_data.get("source_sha256")
        if not preview_hash:
            self.host._note("预览缺少文件校验值；请重新预览后导入。")
            return
        self.host._async("preset_import", lambda: parameter_store.import_presets(
            self.import_path, actions, expected_sha256=preview_hash), self._save_done)
