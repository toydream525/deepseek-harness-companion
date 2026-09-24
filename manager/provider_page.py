"""DeepSeek Harness and provider management UI."""

from __future__ import annotations

import json
import threading
from uuid import uuid4

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QScrollArea, QTextEdit, QVBoxLayout, QWidget,
    QDialog, QDialogButtonBox, QListWidget, QListWidgetItem, QTableWidget,
    QTableWidgetItem, QHeaderView,
)
from button_feedback import connect_button


def _btn(label, callback, primary=False):
    result = QPushButton(label)
    result.setObjectName("primaryButton" if primary else "secondaryButton")
    return connect_button(result, callback)


class ProviderSignals(QObject):
    stage = Signal(str)


class ProviderPage(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self.integration = host.integration
        self.providers = []
        self.provider_revision = None
        self.default_revision = None
        self.current_default = {}
        self.current_models = []
        self.current_status = {}
        self.workflow_cancel = threading.Event()
        self.workflow_running = False
        self.local_selection = None
        self._conflict_widgets = []
        self.signals = ProviderSignals()
        self.signals.stage.connect(self._show_stage)
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
        page.setContentsMargins(26, 22, 26, 24)
        page.setSpacing(18)
        heading = QLabel("DSH 与提供方")
        heading.setObjectName("pageTitle")
        page.addWidget(heading)
        page.addWidget(QLabel("检测或连接本机 DSH，管理本地和云端提供方，再明确选择新会话默认模型。"))

        dsh = QFrame()
        dsh.setObjectName("card")
        dsh_layout = QVBoxLayout(dsh)
        dsh_layout.setContentsMargins(20, 18, 20, 18)
        dsh_layout.addWidget(QLabel("DeepSeek Harness"))
        self.dsh_state = QLabel("正在检查…")
        self.dsh_state.setWordWrap(True)
        dsh_layout.addWidget(self.dsh_state)
        first = QHBoxLayout()
        for text, method in (("检测", self.detect_dsh), ("组件设置与官方下载", host.open_components_settings),
                             ("启动", host.start_dsh), ("打开认证界面", host.open_dsh),
                             ("停止本窗口启动实例", host.stop_dsh),
                             ("选择实例强制停止", host.force_stop_dsh)):
            widget = _btn(text, method)
            first.addWidget(widget)
            self._conflict_widgets.append(widget)
        first.addStretch()
        dsh_layout.addLayout(first)
        for caption, attribute in (("DSH 页面地址", "page_link"), ("DSH 认证链接（含令牌）", "verified_link")):
            link_row = QHBoxLayout()
            link_row.addWidget(QLabel(caption))
            link_edit = QLineEdit()
            link_edit.setReadOnly(True)
            link_edit.setPlaceholderText("DSH 未运行" if attribute == "page_link" else "启动或连接 DSH 后显示")
            link_row.addWidget(link_edit, 1)
            link_row.addWidget(_btn("复制", lambda edit=link_edit: self._copy_link(edit)))
            setattr(self, attribute, link_edit)
            dsh_layout.addLayout(link_row)
        attach_row = QHBoxLayout()
        self.auth_link = QLineEdit()
        self.auth_link.setEchoMode(QLineEdit.EchoMode.Password)
        self.auth_link.setPlaceholderText("已有外部 DSH：粘贴其原启动认证链接，仅本次会话使用")
        attach_row.addWidget(self.auth_link, 1)
        attach_btn = _btn("连接现有 DSH", self.attach_dsh)
        attach_row.addWidget(attach_btn)
        self._conflict_widgets.append(attach_btn)
        dsh_layout.addLayout(attach_row)
        page.addWidget(dsh)

        providers = QFrame()
        providers.setObjectName("card")
        provider_layout = QVBoxLayout(providers)
        provider_layout.setContentsMargins(20, 18, 20, 18)
        provider_layout.addWidget(QLabel("模型提供方"))
        select_row = QHBoxLayout()
        self.provider_combo = QComboBox()
        self.provider_combo.currentIndexChanged.connect(self._provider_selected)
        select_row.addWidget(self.provider_combo, 1)
        new_btn = _btn("新建", self.new_provider)
        refresh_btn = _btn("刷新", self.refresh_providers)
        select_row.addWidget(new_btn)
        select_row.addWidget(refresh_btn)
        self._conflict_widgets.extend((new_btn, refresh_btn))
        provider_layout.addLayout(select_row)
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.kind_combo = QComboBox()
        self.kind_combo.addItem("本地模型", "local")
        self.kind_combo.addItem("DeepSeek 云端", "deepseek_cloud")
        self.kind_combo.addItem("自定义兼容接口", "custom")
        self.base_url_edit = QLineEdit()
        self.base_url_edit.setPlaceholderText("https://api.example.com/v1")
        self.api_combo = QComboBox()
        for title, api in (("OpenAI Chat Completions", "openai-completions"),
                           ("OpenAI Responses", "openai-responses"),
                           ("Anthropic Messages", "anthropic-messages")):
            self.api_combo.addItem(title, api)
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("留空则保留已保存的密钥；输入新值只写入凭据存储")
        self.models_table = QTableWidget(0, 4)
        self.models_table.setHorizontalHeaderLabels(["模型 ID", "显示名称", "上下文长度", "最大输出"])
        self.models_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.models_table.setMinimumHeight(135)
        form.addRow("显示名称", self.name_edit)
        form.addRow("类型", self.kind_combo)
        form.addRow("API 地址", self.base_url_edit)
        form.addRow("接口协议", self.api_combo)
        form.addRow("访问密钥", self.key_edit)
        provider_layout.addLayout(form)
        provider_layout.addWidget(QLabel("模型配置（目录继承的提供方可留空，保留 DSH 原设置）"))
        provider_layout.addWidget(self.models_table)
        model_actions = QHBoxLayout()
        model_actions.addWidget(_btn("添加模型行", self.add_model_row))
        model_actions.addWidget(_btn("删除选中模型行", self.remove_model_row))
        model_actions.addStretch()
        provider_layout.addLayout(model_actions)
        operation_row = QHBoxLayout()
        for text, method in (("保存提供方", self.save_provider), ("删除", self.delete_provider),
                             ("发现模型", self.discover_models), ("连接测试", self.test_provider),
                             ("去 DSH 会话生成测试", self.test_generation)):
            widget = _btn(text, method, text == "保存提供方")
            operation_row.addWidget(widget)
            self._conflict_widgets.append(widget)
        operation_row.addStretch()
        provider_layout.addLayout(operation_row)
        self.provider_info = QLabel("密钥不会回显，也不会进入诊断包。")
        self.provider_info.setWordWrap(True)
        provider_layout.addWidget(self.provider_info)
        page.addWidget(providers)

        workflow = QFrame()
        workflow.setObjectName("heroCard")
        workflow_layout = QVBoxLayout(workflow)
        workflow_layout.setContentsMargins(20, 18, 20, 18)
        workflow_layout.addWidget(QLabel("开始 DSH 工作"))
        workflow_layout.addWidget(QLabel(
            "本地：模型就绪 → DSH 就绪 → 注册参数 → 设为新会话默认 → 打开认证界面；云端不加载本地模型。"))
        model_row = QHBoxLayout()
        self.workflow_model = QComboBox()
        self.workflow_model.currentIndexChanged.connect(self._sync_reasoning)
        self.reasoning_combo = QComboBox()
        self.reasoning_combo.addItem("使用模型默认思考设置", None)
        model_row.addWidget(QLabel("目标模型"))
        model_row.addWidget(self.workflow_model, 1)
        model_row.addWidget(self.reasoning_combo)
        workflow_layout.addLayout(model_row)
        output_row = QHBoxLayout()
        self.output_limit_edit = QLineEdit()
        self.output_limit_edit.setPlaceholderText("留空使用应用策略：min(4096, 上下文 / 4)，并遵守已知运行上限")
        output_row.addWidget(QLabel("DSH 请求输出上限"))
        output_row.addWidget(self.output_limit_edit, 1)
        workflow_layout.addLayout(output_row)
        note = QLabel("这是 DSH 单次请求的输出上限，不代表模型原生能力。")
        note.setWordWrap(True)
        workflow_layout.addWidget(note)
        work_actions = QHBoxLayout()
        self.local_work_btn = _btn("将所选本地模型与预设接入 DSH", self.start_local_work)
        self.default_btn = _btn("设为新会话默认", self.set_default)
        self.start_work_btn = _btn("开始 DSH 工作", self.start_work, True)
        self.cancel_work_btn = _btn("取消当前流程", self.cancel_work)
        self.cancel_work_btn.setEnabled(False)
        self._conflict_widgets.append(self.default_btn)
        for widget in (self.local_work_btn, self.default_btn, self.start_work_btn, self.cancel_work_btn):
            work_actions.addWidget(widget)
        work_actions.addStretch()
        workflow_layout.addLayout(work_actions)
        self.default_state = QLabel("正在读取新会话默认模型…")
        workflow_layout.addWidget(self.default_state)
        self.stage = QLabel("请选择提供方与模型。")
        self.stage.setObjectName("statusBadge")
        self.stage.setWordWrap(True)
        workflow_layout.addWidget(self.stage)
        page.addWidget(workflow)
        page.addStretch()
        QTimer.singleShot(0, self.refresh_all)

    def refresh_all(self):
        self.detect_dsh()
        self.refresh_providers()
        self.refresh_default()

    def _status(self, result):
        self.host._note((result or {}).get("message", "操作完成"))
        if isinstance(result, dict) and not result.get("success", True):
            self.provider_info.setText(result.get("message", "操作失败"))

    def detect_dsh(self):
        self.host._async("dsh_detect", self.integration.detect, self._show_detect)

    def _show_detect(self, result):
        self.current_status = result if isinstance(result, dict) else {}
        data = self.current_status.get("data") or {}
        self.dsh_state.setText(
            f"{result.get('message', '状态未知')}\n"
            f"安装：{data.get('installed', '未知')} · 运行：{data.get('running', '未知')} · "
            f"版本：{data.get('version', '未知')}")
        self.host.refresh_dsh()

    def install_dsh(self):
        self.dsh_state.setText("正在检查并安装固定版本，请稍候…")
        self.host._async("dsh_install", self.integration.install,
                         lambda result: (self._status(result), self.detect_dsh()))

    def attach_dsh(self):
        link = self.auth_link.text().strip()
        self.auth_link.clear()
        if not link or not self.host.dsh:
            return
        self.host._async("dsh_attach", lambda: self.host.dsh.attach_authenticated_url(link),
                         lambda result: (self._status(result), self.detect_dsh()))

    def _copy_link(self, edit):
        if edit.text():
            self.host.copy_text(edit.text())
        else:
            self.host._note("当前没有可复制的链接。")

    def update_links(self, status):
        running = bool(status.get("running"))
        self.page_link.setText("http://127.0.0.1:3080/" if running else "")
        self.verified_link.setText(self.host.dsh.authenticated_link(status) if self.host.dsh else "")

    def refresh_providers(self):
        self.host._async("provider_list", self.integration.list_providers, self._show_providers)

    def _show_providers(self, result):
        if not isinstance(result, dict) or not result.get("success"):
            self._status(result or {})
            return
        data = result.get("data") or {}
        rows = data.get("providers") or []
        current = self.provider_combo.currentData()
        self.providers = rows
        self.provider_revision = result.get("revision")
        self.provider_combo.blockSignals(True)
        self.provider_combo.clear()
        for row in rows:
            self.provider_combo.addItem(str(row.get("display_name") or row.get("id")), row.get("id"))
        if current:
            index = self.provider_combo.findData(current)
            if index >= 0:
                self.provider_combo.setCurrentIndex(index)
        self.provider_combo.blockSignals(False)
        self._provider_selected()

    def _selected_provider(self):
        provider_id = self.provider_combo.currentData()
        return next((row for row in self.providers if row.get("id") == provider_id), None)

    def _provider_selected(self):
        row = self._selected_provider()
        if not row:
            return
        self.name_edit.setText(row.get("display_name") or "")
        self.kind_combo.setCurrentIndex(max(0, self.kind_combo.findData(row.get("kind"))))
        self.base_url_edit.setText(row.get("base_url") or "")
        self.api_combo.setCurrentIndex(self.api_combo.findData(row.get("api")))
        self.key_edit.clear()
        self.current_models = row.get("models") or []
        self._fill_model_table(self.current_models)
        self.models_table.setEnabled(bool(row.get("editable")))
        self.provider_info.setText("凭据：" + ("已配置（不可回显）" if row.get("credential_configured")
                                         else "未配置"))
        self._fill_workflow_models()
        self.update_model_control()

    def new_provider(self):
        self.provider_combo.setCurrentIndex(-1)
        self.name_edit.clear()
        self.kind_combo.setCurrentIndex(0)
        self.base_url_edit.clear()
        self.api_combo.setCurrentIndex(0)
        self.key_edit.clear()
        self._fill_model_table([])
        self.models_table.setEnabled(True)
        self.current_models = []
        self._fill_workflow_models()

    def _fill_model_table(self, rows):
        self.models_table.setRowCount(0)
        for row in rows:
            i = self.models_table.rowCount()
            self.models_table.insertRow(i)
            for col, field in enumerate(("id", "name", "context_window", "max_tokens")):
                value = row.get(field)
                self.models_table.setItem(i, col, QTableWidgetItem("" if value is None else str(value)))

    def add_model_row(self):
        i = self.models_table.rowCount()
        self.models_table.insertRow(i)
        for col in range(4):
            self.models_table.setItem(i, col, QTableWidgetItem(""))

    def remove_model_row(self):
        row = self.models_table.currentRow()
        if row >= 0:
            self.models_table.removeRow(row)

    def _table_models(self):
        rows = []
        original = {str(x.get("id")): x for x in self.current_models}
        for row in range(self.models_table.rowCount()):
            def value(col):
                item = self.models_table.item(row, col)
                return item.text().strip() if item else ""
            model_id = value(0)
            if not model_id:
                raise ValueError("模型 ID 不能为空")
            model = dict(original.get(model_id, {"id": model_id}))
            model["id"] = model_id
            if value(1):
                model["name"] = value(1)
            for col, key in ((2, "context_window"), (3, "max_tokens")):
                if value(col):
                    number = int(value(col))
                    if number <= 0:
                        raise ValueError("上下文与输出上限必须大于零")
                    model[key] = number
            rows.append(model)
        return rows

    def _provider_data(self):
        existing = self._selected_provider()
        data = {"id": existing.get("id") if existing else "companion-" + uuid4().hex[:12],
                "kind": self.kind_combo.currentData()}
        for key, value in (("display_name", self.name_edit.text().strip()),
                           ("base_url", self.base_url_edit.text().strip()),
                           ("api", self.api_combo.currentData())):
            if not existing or value != existing.get(key):
                if value or key != "api":
                    data[key] = value
        models = self._table_models()
        if models != self.current_models:
            data["models"] = models
        key = self.key_edit.text().strip()
        if key:
            data["api_key"] = key
        return data

    def save_provider(self):
        try:
            data = self._provider_data()
        except ValueError as exc:
            self.provider_info.setText(str(exc))
            return
        if not self._selected_provider() and (not data.get("display_name") or not data.get("base_url") or not data.get("models")):
            self.provider_info.setText("新建提供方需填写名称、API 地址，并至少添加一个有已核实上下文长度的模型。")
            return
        if not self._selected_provider() and any(not x.get("context_window") for x in data["models"]):
            self.provider_info.setText("新建模型需要明确上下文长度，不能猜测默认值。")
            return
        row = self._selected_provider()
        revision = row.get("revision", self.provider_revision) if row else self.provider_revision
        self.host._async("provider_save", lambda: self.integration.upsert_provider(
            data, expected_revision=revision), self._provider_saved)

    def _provider_saved(self, result):
        self.key_edit.clear()
        self._status(result)
        if result.get("code") == "conflict":
            self.provider_info.setText("提供方设置已被其他窗口修改。当前草稿仍在上方，请刷新后再决定是否重试。")
        elif result.get("success"):
            self.refresh_providers()

    def delete_provider(self):
        row = self._selected_provider()
        if not row:
            return
        if QMessageBox.question(self, "删除提供方", f"删除 {row.get('display_name')}？") != QMessageBox.StandardButton.Yes:
            return
        self.host._async("provider_delete", lambda: self.integration.delete_provider(
            row["id"], expected_revision=row.get("revision", self.provider_revision)), self._provider_saved)

    def discover_models(self):
        row = self._selected_provider()
        if row:
            job = lambda: self.integration.discover_models(row["id"])
        else:
            base_url = self.base_url_edit.text().strip()
            api = self.api_combo.currentData()
            key = self.key_edit.text().strip()
            job = lambda: self.integration.discover_models(None, base_url=base_url,
                                                            api=api, api_key=key or None)
        self.host._async("provider_discover", job, self._models_discovered)

    def _models_discovered(self, result):
        self._status(result)
        if result.get("success"):
            data = result.get("data") or {}
            choices = data.get("models") or []
            dialog = QDialog(self)
            dialog.setWindowTitle("选择要增补的模型")
            layout = QVBoxLayout(dialog)
            layout.addWidget(QLabel("勾选需要增补的模型；现有模型不会被替换。"))
            listing = QListWidget()
            for model in choices:
                item = QListWidgetItem(str(model.get("name") or model.get("id")))
                item.setData(Qt.ItemDataRole.UserRole, model)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
                listing.addItem(item)
            layout.addWidget(listing)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                selected = [listing.item(i).data(Qt.ItemDataRole.UserRole)
                            for i in range(listing.count())
                            if listing.item(i).checkState() == Qt.CheckState.Checked]
                try:
                    present = {x["id"] for x in self._table_models()}
                except ValueError:
                    present = set()
                for model in selected:
                    if model.get("id") not in present:
                        self.add_model_row()
                        row = self.models_table.rowCount() - 1
                        for col, field in enumerate(("id", "name", "context_window", "max_tokens")):
                            value = model.get(field)
                            self.models_table.item(row, col).setText("" if value is None else str(value))
                self.provider_info.setText("已增补所选模型；请核对上下文与输出上限后保存。")

    def test_provider(self):
        self._test(False)

    def test_generation(self):
        if not self.host.dsh_status.get("can_open"):
            self.provider_info.setText("当前没有已验证的 DSH 认证链接；请先连接或启动 DSH。")
            return
        self.provider_info.setText("请在 DSH 会话中主动输入测试消息。可能产生云端费用；此窗口没有发送生成请求。")
        self.host.open_dsh()

    def _test(self, generate):
        row = self._selected_provider()
        if not row:
            self.provider_info.setText("请先保存提供方。")
            return
        self.host._async("provider_test", lambda: self.integration.test_provider(
            row["id"], generate=generate), self._status)

    def _fill_workflow_models(self):
        previous = self.workflow_model.currentData()
        self.workflow_model.clear()
        for row in self.current_models:
            model_id = str(row.get("id") or "")
            if model_id:
                self.workflow_model.addItem(str(row.get("name") or model_id), model_id)
        if previous:
            index = self.workflow_model.findData(previous)
            if index >= 0:
                self.workflow_model.setCurrentIndex(index)
        self._sync_reasoning()

    def _sync_reasoning(self):
        model_id = self.workflow_model.currentData()
        row = next((x for x in self.current_models if x.get("id") == model_id), None) or {}
        choices = row.get("reasoning_efforts")
        self.reasoning_combo.clear()
        self.reasoning_combo.addItem("使用模型默认思考设置", None)
        if isinstance(choices, (list, tuple)):
            for effort in choices:
                if isinstance(effort, str) and effort:
                    self.reasoning_combo.addItem(effort, effort)
        self.reasoning_combo.setEnabled(self.reasoning_combo.count() > 1)

    def _output_limit(self):
        raw = self.output_limit_edit.text().strip()
        if not raw:
            return None
        try:
            value = int(raw)
        except ValueError:
            raise ValueError("DSH 请求输出上限必须为正整数")
        if value <= 0:
            raise ValueError("DSH 请求输出上限必须为正整数")
        return value

    def refresh_default(self):
        self.host._async("provider_default", self.integration.get_new_session_default,
                         self._show_default)

    def _show_default(self, result):
        if not isinstance(result, dict) or not result.get("success"):
            self.default_state.setText((result or {}).get("message", "无法读取新会话默认模型。"))
            return
        self.default_revision = result.get("revision")
        self.current_default = result.get("data") or {}
        data = self.current_default
        self.default_state.setText(
            f"新会话默认：{data.get('provider_id') or '未设置'} / {data.get('model_id') or '未设置'}")

    def set_default(self):
        provider = self._selected_provider()
        model_id = self.workflow_model.currentData()
        if not provider or not model_id:
            self.stage.setText("请先选择已保存的提供方与模型。")
            return
        self.host._async("provider_set_default", lambda: self.integration.set_new_session_default(
            provider["id"], model_id, reasoning_effort=self.reasoning_combo.currentData(),
            expected_revision=self.default_revision),
            lambda result: (self._status(result), self.refresh_default()))

    def start_work(self):
        if self.workflow_running:
            return
        provider = self._selected_provider()
        model_id = self.workflow_model.currentData()
        if not provider or not model_id:
            self.stage.setText("请选择提供方与模型。")
            return
        if provider.get("kind") == "local" and (not self.host.model_session or
                                                  self.host.last_status.get("control_read_only") or
                                                  self.host.last_status.get("state") == "EXTERNAL"):
            self.stage.setText("当前模型由旧管理器控制；请先关闭旧管理器并接管，再使用本地一键流程。")
            return
        try:
            output_limit = self._output_limit()
        except ValueError as exc:
            self.stage.setText(str(exc))
            return
        self.workflow_cancel.clear()
        self.workflow_running = True
        self.host.workflow_error = ""
        self.host.workflow_stage = "正在连接所选提供方与 DSH…"
        self._set_workflow_locked(True)
        self.start_work_btn.setEnabled(False)
        self.cancel_work_btn.setEnabled(True)
        provider_id = provider["id"]
        def progress(event):
            if isinstance(event, dict):
                self.signals.stage.emit(f"{event.get('stage', '')} · {event.get('message', '')}")
        effort = self.reasoning_combo.currentData()
        self.host._async("start_work", lambda: self.integration.start_work(
            provider_id, model_id, progress=progress, cancel=self.workflow_cancel,
            reasoning_effort=effort, output_limit=output_limit), self._work_done)

    def start_local_work(self):
        if self.workflow_running:
            return
        model_id = self.host.model_combo.currentData()
        preset_id = self.host.start_preset_combo.currentData()
        if not model_id:
            self.stage.setText("请先在工作台选择本地模型。")
            return
        if not self.host.model_session or self.host.last_status.get("control_read_only"):
            self.stage.setText(self.host.read_only_reason or "旧管理器正在控制模型，当前只读。")
            return
        try:
            output_limit = self._output_limit()
        except ValueError as exc:
            self.stage.setText(str(exc))
            return
        if self.host.busy:
            self.stage.setText("请等待当前操作结束。")
            return
        self.workflow_cancel.clear()
        self.workflow_running = True
        self.host.workflow_error = ""
        self.host.workflow_stage = "启动模型 → 连接 DSH → 配置本地模型 → 就绪｜当前：检查模型"
        self._set_workflow_locked(True)
        self.cancel_work_btn.setEnabled(True)
        def progress(event):
            if isinstance(event, dict):
                self.signals.stage.emit(f"{event.get('stage', '')} · {event.get('message', '')}")
        self.host._async("start_local_work", lambda: self.integration.start_work(
            "local", model_id, progress=progress, cancel=self.workflow_cancel,
            local_model_id=model_id, preset_id=preset_id,
            output_limit=output_limit), self._work_done)

    def _set_workflow_locked(self, locked):
        self.host.busy = locked
        for widget in self._conflict_widgets:
            widget.setEnabled(not locked)
        self.provider_combo.setEnabled(not locked)
        self.models_table.setEnabled(not locked)
        self.local_work_btn.setEnabled(not locked)
        self.start_work_btn.setEnabled(not locked)
        self.host.library_page.setEnabled(not locked)
        self.host.presets_page.setEnabled(not locked)
        self.host.settings_page.setEnabled(not locked)
        self.host._update_buttons()
        self.update_model_control()

    def update_model_control(self):
        writable = self.host.model_session and not self.host.last_status.get("control_read_only")
        self.local_work_btn.setEnabled(writable and not self.workflow_running and not self.host.quitting)
        selected = self._selected_provider()
        self.start_work_btn.setEnabled(not self.workflow_running and not self.host.quitting and
                                       (writable or not selected or selected.get("kind") != "local"))
        if not writable:
            self.local_work_btn.setToolTip(self.host.read_only_reason or "模型由旧管理器控制")
        else:
            self.local_work_btn.setToolTip("")

    def _show_stage(self, value):
        self.stage.setText(value)

    def cancel_work(self):
        if self.workflow_running:
            self.workflow_cancel.set()
            self.stage.setText("正在取消当前流程；仅回滚本次创建的对象…")

    def _work_done(self, result):
        self.workflow_running = False
        self.host.workflow_stage = ""
        self.host.workflow_error = "" if (result or {}).get("success") else (
            "未完成：" + str((result or {}).get("message", "请检查组件和认证设置后重试。")))
        self.host.workflow_ready = bool((result or {}).get("success"))
        self._set_workflow_locked(False)
        self.cancel_work_btn.setEnabled(False)
        self.stage.setText((result or {}).get("message", "流程已结束"))
        self.host._note((result or {}).get("message", "流程已结束"))
        self.host.refresh_status()
        self.host.refresh_dsh()
        self.refresh_default()
        self.detect_dsh()
        if self.host.exit_after_work:
            self.host.exit_after_work = False
            QTimer.singleShot(0, self.host.request_exit)
