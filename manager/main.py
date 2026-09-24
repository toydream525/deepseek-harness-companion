# -*- coding: utf-8 -*-
"""DSH Companion: native local model and API console."""

from __future__ import annotations

import copy
import json
import multiprocessing
import platform
import re
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal, QEvent, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPushButton, QScrollArea, QSplitter, QStackedWidget,
    QSystemTrayIcon, QTextEdit, QTextBrowser, QVBoxLayout, QWidget, QMenu, QInputDialog, QDialog,
    QTabWidget,
)

import autostart
import backend
import model_catalog
import parameter_store
from button_feedback import active_button, begin_pending, connect_button, end_pending
try:
    import recipe_importer
except ImportError:
    recipe_importer = None
try:
    import network_proxy
except ImportError:
    network_proxy = None
from chat_client import ChatStream
from library_page import LibraryPage
from presets_page import PresetsPage
from provider_page import ProviderPage
try:
    import runtime_manager
except ImportError:
    runtime_manager = None
try:
    from component_updates import ComponentUpdateService
except ImportError:
    ComponentUpdateService = None
try:
    from dsh_service import DSHController
except ImportError:
    DSHController = None
try:
    from dsh_integration import DSHIntegration
except ImportError:
    DSHIntegration = None

try:
    from styles import app_icon, app_stylesheet
except ImportError:
    from PySide6.QtGui import QIcon
    def app_icon():
        return QIcon()
    def app_stylesheet():
        return ""


APP_VERSION = "0.1.0"
TITLE = f"DSH 伴航 · Harness Companion v{APP_VERSION}"
INSTANCE_NAME = "DSH_Companion_Native_SingleInstance"
FIELDS = [
    ("name", "名称", str), ("alias", "模型 ID", str),
    ("model_path", "模型文件", str), ("template_path", "模板文件", str),
    ("ctx_size", "上下文", int), ("ngl", "GPU 层数", int),
    ("batch_size", "批大小", int), ("ubatch_size", "微批大小", int),
    ("cache_type_k", "K 缓存", str), ("cache_type_v", "V 缓存", str),
    ("reasoning_effort", "推理强度", str), ("reasoning_budget", "推理预算", int),
    ("temp", "温度", float), ("top_p", "Top P", float),
]


class Bridge(QObject):
    done = Signal(str, object)
    progress = Signal(str)


def button(text: str, handler, kind: str = "secondaryButton") -> QPushButton:
    widget = QPushButton(text)
    widget.setObjectName(kind)
    return connect_button(widget, handler)


def card(title: str = "") -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(12)
    if title:
        heading = QLabel(title)
        heading.setObjectName("cardTitle")
        layout.addWidget(heading)
    return frame, layout


def scroll_page() -> tuple[QScrollArea, QWidget, QVBoxLayout]:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    page = QWidget()
    page.setObjectName("pageSurface")
    layout = QVBoxLayout(page)
    layout.setContentsMargins(26, 22, 26, 24)
    layout.setSpacing(18)
    scroll.setWidget(page)
    return scroll, page, layout


class RecipeBindingDialog(QDialog):
    """Collect only missing local file bindings for a portable recipe."""

    _ROLE_LABELS = {
        "uncensored_iq3": "Huihui IQ3_S（含直接回答档）",
        "original_iq3": "Original IQ3_S",
        "writing_iq4": "Huihui IQ4_XS 写作档",
    }

    def __init__(self, parent: QWidget, preview: dict):
        super().__init__(parent)
        self.setWindowTitle("导入原提示词配置 · 绑定本机资源")
        self.setMinimumSize(740, 520)
        self.setStyleSheet(app_stylesheet())
        self.model_inputs: dict[str, QComboBox] = {}
        self.template_inputs: dict[str, QLineEdit] = {}
        layout = QVBoxLayout(self)
        intro = QLabel("选择已入库的 GGUF 和 froggeric 模板。导入只保存预设，不下载、启动模型或修改密钥。")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        if not preview.get("engine_ready"):
            engine = QLabel("运行引擎尚未通过检测：可以先保存待就绪预设，启动前须到“设置与诊断 → 运行环境”选择并验证引擎。")
            engine.setWordWrap(True)
            layout.addWidget(engine)
        scroller = QScrollArea()
        scroller.setWidgetResizable(True)
        content = QWidget()
        scroller.setWidget(content)
        column = QVBoxLayout(content)
        for slot in preview.get("slots") or []:
            role = slot["role"]
            panel, body = card(self._ROLE_LABELS.get(role, slot.get("label") or role))
            resource = slot.get("model_resource") or {}
            body.addWidget(QLabel(f"预期文件：{resource.get('filename', '未知')} · 阶段：{slot.get('phase', '未知')}"))
            row = QHBoxLayout()
            combo = QComboBox()
            combo.addItem("请选择已入库模型", "")
            for candidate in slot.get("candidates") or []:
                if candidate.get("valid_binding"):
                    marker = "已精准匹配" if candidate.get("exact_identity") else "请核实来源"
                    combo.addItem(f"{candidate.get('name') or candidate.get('filename')} · {marker}",
                                  candidate["model_id"])
            index = combo.findData(slot.get("matched_model_id"))
            if index >= 0:
                combo.setCurrentIndex(index)
            self.model_inputs[role] = combo
            row.addWidget(combo, 1)
            row.addWidget(button("添加 GGUF 文件", lambda _=False, r=role: self._add_model(r)))
            body.addLayout(row)
            template_row = QHBoxLayout()
            template = QLineEdit(slot.get("template_path") or "")
            template.setPlaceholderText("选择来自配套指南固定版本的 .jinja 模板；程序会自动核验")
            self.template_inputs[role] = template
            template_row.addWidget(template, 1)
            template_row.addWidget(button("选择模板", lambda _=False, r=role: self._choose_template(r)))
            body.addLayout(template_row)
            for missing in slot.get("missing") or []:
                hint = QLabel(str(missing.get("message") or missing.get("code") or "资源待绑定"))
                hint.setWordWrap(True)
                body.addWidget(hint)
            column.addWidget(panel)
        column.addStretch()
        layout.addWidget(scroller, 1)
        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(button("取消", self.reject))
        actions.addWidget(button("验证并导入", self.accept, "primaryButton"))
        layout.addLayout(actions)

    def _add_model(self, role: str):
        path, _ = QFileDialog.getOpenFileName(self, "选择已下载的 GGUF", "", "GGUF 模型 (*.gguf)")
        if not path:
            return
        result = model_catalog.add_model(path)
        if not result.get("success"):
            QMessageBox.warning(self, "模型入库失败", result.get("message", "GGUF 无法验证"))
            return
        model = result["model"]
        combo = self.model_inputs[role]
        index = combo.findData(model["id"])
        if index < 0:
            combo.addItem(model.get("name") or Path(path).name, model["id"])
            index = combo.count() - 1
        combo.setCurrentIndex(index)

    def _choose_template(self, role: str):
        path, _ = QFileDialog.getOpenFileName(self, "选择 froggeric 聊天模板", "", "Jinja 模板 (*.jinja)")
        if path:
            self.template_inputs[role].setText(path)

    def bindings(self) -> dict[str, str]:
        return {role: combo.currentData() for role, combo in self.model_inputs.items()
                if combo.currentData()}

    def templates(self) -> dict[str, str]:
        return {role: edit.text().strip() for role, edit in self.template_inputs.items()
                if edit.text().strip()}


class WelcomeDialog(QDialog):
    """Short first-run orientation; all detailed steps remain in the built-in guide."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setWindowTitle("欢迎使用 DSH 伴航")
        self.setWindowIcon(app_icon())
        self.setMinimumSize(680, 560)
        self.setStyleSheet(app_stylesheet())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(13)
        heading = QLabel("把本地模型接入 DSH，从这里开始")
        heading.setObjectName("pageTitle")
        layout.addWidget(heading)
        intro = QLabel("DSH 伴航是独立的 Windows 本地模型与 API 控制台，也能管理 DSH 提供方。它不是 DeepSeek Harness 官方产品。")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        panel, body = card("先准备什么")
        for item in (
            "GGUF 模型文件：可复用已有文件，或在“模型库与下载”从 Hugging Face 选择完整量化文件。",
            "llama.cpp 运行环境：从官方发布页下载适合本机的 Windows 版本，完整解压，包含 llama-server.exe 和配套 DLL；先用 CPU 版也可以。",
            "仅在使用 DSH 时准备 Node.js 与 DeepSeek Harness；在“DSH 组件”选择现有路径并检测。第三方程序和权重不包含在本包中。",
        ):
            label = QLabel("• " + item)
            label.setWordWrap(True)
            body.addWidget(label)
        layout.addWidget(panel)
        steps, steps_body = card("三步开始")
        for item in (
            "1  选择引擎和模型文件，检测路径与能力；若文件缺失，先按内置指南下载或复制。",
            "2  按需导入内置 Qwen3.8 参考配置，并匹配对应模型和 froggeric 模板；也可以为其他 GGUF 自建预设。导入不自动下载或启动。",
            "3  检查参数并启动模型，在“试聊”验证回答；需要 Agent 时再连接 DSH。",
        ):
            label = QLabel(item)
            label.setWordWrap(True)
            steps_body.addWidget(label)
        layout.addWidget(steps)
        layout.addStretch()
        self.dismiss_future = QCheckBox("不再自动显示此说明（可随时在设置中重开）")
        self.dismiss_future.setChecked(True)
        layout.addWidget(self.dismiss_future)
        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(button("查看完整配置指南", self._open_guide))
        actions.addWidget(button("开始使用", self.accept, "primaryButton"))
        layout.addLayout(actions)

    def _open_guide(self):
        self.accept()
        QTimer.singleShot(0, self.parent().open_setup_guide)


class MainWindow(QMainWindow):
    def __init__(self, model_session: bool = True, read_only_reason: str = ""):
        super().__init__()
        self.setObjectName("mainWindow")
        self.setWindowTitle(TITLE)
        self.setWindowIcon(app_icon())
        self.resize(1120, 760)
        self.setMinimumSize(840, 580)
        self.cfg = backend.load_config()
        self.model_session = model_session
        self.read_only_reason = read_only_reason
        self.selected = self.cfg.get("active_profile", "")
        self.last_status: dict = {}
        self.component_info: dict = {}
        self.busy = False
        self.status_pending = False
        self.quitting = False
        self._allow_close = False
        self.chat: ChatStream | None = None
        self.chat_cancel_pending = False
        self.chat_history: list[dict] = []
        self.chat_model = ""
        self.pending_clear = False
        self.exit_after_work = False
        self.exit_after_critical = False
        self._critical_requests: set[str] = set()
        self.dsh = DSHController() if DSHController else None
        self.integration = DSHIntegration(dsh=self.dsh, model_backend=backend) if DSHIntegration else None
        self.component_updates = ComponentUpdateService(self.integration) if ComponentUpdateService else None
        self.update_prepared: dict[str, str] = {}
        self.component_update_busy = False
        self.dsh_status = {}
        self.bridge = Bridge()
        self.bridge.done.connect(self._worker_done)
        self.bridge.progress.connect(self._show_runtime_progress)
        self.callbacks = {}
        self._pending_button_requests: dict[str, QPushButton] = {}
        self.workflow_stage = ""
        self.workflow_error = ""
        self.workflow_ready = False
        self._request_seq = 0
        self.dsh_pending = False
        self._build_ui()
        self._setup_tray()
        self._update_buttons()
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.refresh_status)
        self.poll_timer.timeout.connect(self.refresh_dsh)
        self.poll_timer.start(5000)
        self.refresh_status()
        self.refresh_dsh()
        if not self.cfg.get("onboarding_seen", False):
            QTimer.singleShot(350, self.show_welcome_dialog)
        if self.model_session and self.cfg.get("autostart_model_on_manager_open"):
            QTimer.singleShot(500, self.start_model)

    def _build_ui(self):
        shell = QWidget()
        shell.setObjectName("appShell")
        self.setCentralWidget(shell)
        row = QHBoxLayout(shell)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(215)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(16, 25, 16, 18)
        side.setSpacing(11)
        brand = QLabel("DSH 伴航")
        brand.setObjectName("sidebarTitle")
        side.addWidget(brand)
        tagline = QLabel("本地模型与 API 控制台")
        tagline.setObjectName("sidebarSubtitle")
        side.addWidget(tagline)
        side.addSpacing(22)
        self.nav = QListWidget()
        self.nav.setObjectName("navList")
        for title in ("首页", "工作台", "模型库与下载", "参数预设", "DSH 与提供方", "试聊", "设置与诊断"):
            self.nav.addItem(QListWidgetItem(title))
        self.nav.currentRowChanged.connect(self._navigate)
        side.addWidget(self.nav, 1)
        side.addWidget(QLabel("本地运行 · 数据留在本机"))
        row.addWidget(sidebar)
        self.stack = QStackedWidget()
        row.addWidget(self.stack, 1)
        self._overview_page()
        self._workbench_page()
        self.library_page = LibraryPage(self)
        self.library_page.selected_model_changed.connect(
            lambda model_id: self.refresh_start_choices(model_id) if model_id else None)
        self.stack.addWidget(self.library_page)
        self.presets_page = PresetsPage(self)
        self.stack.addWidget(self.presets_page)
        self.provider_page = ProviderPage(self)
        self.provider_page.signals.stage.connect(self._note)
        self.provider_page.signals.stage.connect(self._show_home_work_stage)
        self.stack.addWidget(self.provider_page)
        self._chat_page()
        self._settings_page()
        self.nav.setCurrentRow(0)
        self.setStyleSheet(app_stylesheet())

    def _navigate(self, index: int):
        if index >= 0:
            self.stack.setCurrentIndex(index)
            if index == 6:
                self.refresh_logs()

    def _settings_page(self):
        """Combine existing network and startup controls in one settings page."""
        self._network_page()
        network = self.stack.widget(self.stack.count() - 1)
        self.stack.removeWidget(network)
        self._tools_page()
        tools = self.stack.widget(self.stack.count() - 1)
        self.stack.removeWidget(tools)
        tabs = QTabWidget()
        tabs.addTab(network, "接口与网络")
        tabs.addTab(tools, "启动与日志")
        diagnostic = self._diagnostics_tab()
        tabs.addTab(diagnostic, "诊断导出")
        tabs.addTab(self._runtime_tab(), "运行环境")
        tabs.addTab(self._components_tab(), "DSH 组件")
        tabs.addTab(self._proxy_tab(), "网络代理")
        tabs.addTab(self._updates_tab(), "组件更新")
        self.settings_tabs = tabs
        self.settings_page = tabs
        self.stack.addWidget(tabs)

    def _diagnostics_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(26, 22, 26, 24)
        self._title(layout, "诊断", "导出仅包含状态、版本与数量，不包含密钥、认证链接、聊天内容或日志原文。")
        panel, panel_layout = card("本机状态")
        self.diagnostic_info = QLabel("状态加载后可导出诊断摘要。")
        self.diagnostic_info.setWordWrap(True)
        panel_layout.addWidget(self.diagnostic_info)
        panel_layout.addWidget(button("导出脱敏诊断", self.export_diagnostics, "primaryButton"))
        layout.addWidget(panel)
        layout.addWidget(button("重新打开首次使用说明", self.show_welcome_dialog))
        license_panel, license_layout = card("开源许可")
        license_note = QLabel(
            f"DSH 伴航 v{APP_VERSION} 是社区独立工具，不是 DeepSeek Harness 官方产品。原创代码：MIT；"
            "Python、PySide6/Qt 和其他第三方依赖分别遵循各自许可。"
            "详见发布包中的 LICENSE 与 THIRD-PARTY-NOTICES.md。<br>"
            "项目主页与源码：<a href='https://github.com/toydream525/deepseek-harness-companion'>"
            "github.com/toydream525/deepseek-harness-companion</a> · "
            "官方 Harness：<a href='https://github.com/deepseek-ai/deepseek-harness'>"
            "github.com/deepseek-ai/deepseek-harness</a>")
        license_note.setWordWrap(True)
        license_note.setOpenExternalLinks(True)
        license_layout.addWidget(license_note)
        license_layout.addWidget(button("查看许可与第三方声明", self.open_license_notice))
        layout.addWidget(license_panel)
        layout.addStretch()
        return page

    def open_license_notice(self):
        root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
        notice = root / "THIRD-PARTY-NOTICES.md"
        if not notice.is_file():
            QMessageBox.warning(self, TITLE, "许可声明文件缺失，请重新解压完整发布包。")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(notice)))

    def show_welcome_dialog(self):
        if self.quitting:
            return
        dialog = WelcomeDialog(self)
        dialog.exec()
        updated = backend.load_config()
        updated["onboarding_seen"] = bool(dialog.dismiss_future.isChecked())
        if backend.save_config(updated):
            self.cfg = updated
        else:
            QMessageBox.warning(self, TITLE, "无法保存使用说明的显示设置，下次启动可能再次显示。")

    def _runtime_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(26, 22, 26, 24)
        self._title(layout, "运行环境", "从 llama.cpp 官方发布页下载引擎，解压后选择 llama-server.exe；本控制台不内置第三方引擎。")
        layout.addWidget(button("打开从零配置指南", self.open_setup_guide))
        panel, body = card("llama.cpp 引擎与硬件")
        self.runtime_state = QLabel("正在检测已选择的引擎…")
        self.runtime_state.setWordWrap(True)
        body.addWidget(self.runtime_state)
        self.runtime_progress = QLabel("不确定选哪个版本时，可先使用 CPU 版本；GPU 可用性须以实际推理验证。")
        self.runtime_progress.setWordWrap(True)
        body.addWidget(self.runtime_progress)
        chooser = QHBoxLayout()
        self.runtime_path_edit = QLineEdit()
        self.runtime_path_edit.setPlaceholderText("选择已解压的 llama-server.exe，或包含它的文件夹；支持空格路径")
        chooser.addWidget(self.runtime_path_edit, 1)
        chooser.addWidget(button("选择 EXE", self.choose_runtime_executable))
        chooser.addWidget(button("选择文件夹", self.choose_runtime_folder))
        body.addLayout(chooser)
        controls = QHBoxLayout()
        self.runtime_check_btn = button("重新检测", self.refresh_runtime)
        self.runtime_channel = QComboBox()
        for label, value in (("CPU", "cpu"), ("NVIDIA CUDA", "cuda"),
                             ("Vulkan", "vulkan"), ("AMD ROCm", "rocm")):
            self.runtime_channel.addItem(label, value)
        self.runtime_download_btn = button("打开官方下载页", self.open_runtime_download)
        self.runtime_select_btn = button("检测并使用所选引擎", self.select_runtime_executable, "primaryButton")
        for widget in (self.runtime_check_btn, self.runtime_channel,
                       self.runtime_download_btn, self.runtime_select_btn):
            controls.addWidget(widget)
        controls.addStretch()
        body.addLayout(controls)
        layout.addWidget(panel)
        layout.addStretch()
        QTimer.singleShot(0, self.refresh_runtime)
        return page

    def _proxy_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(26, 22, 26, 24)
        self._title(layout, "网络代理", "按组件分别应用代理；本机 127.0.0.1、localhost 与 ::1 管理连接始终直连。")
        panel, body = card("出站连接")
        self.proxy_mode = QComboBox()
        for label, value in (("直连", "direct"), ("使用系统代理", "system"), ("自定义代理", "custom")):
            self.proxy_mode.addItem(label, value)
        body.addWidget(self.proxy_mode)
        self.proxy_http = QLineEdit()
        self.proxy_https = QLineEdit()
        self.proxy_socks = QLineEdit()
        for label, edit, hint in (("HTTP", self.proxy_http, "http://地址:端口"),
                                  ("HTTPS", self.proxy_https, "http://地址:端口"),
                                  ("SOCKS5", self.proxy_socks, "socks5://地址:端口")):
            edit.setPlaceholderText(hint)
            body.addWidget(QLabel(label))
            body.addWidget(edit)
        body.addWidget(QLabel("自定义代理只能选择 HTTP/HTTPS 或 SOCKS5；本机管理接口始终直连。"))
        auth = QHBoxLayout()
        self.proxy_user = QLineEdit()
        self.proxy_user.setPlaceholderText("代理用户名（仅新设置时填写）")
        self.proxy_pass = QLineEdit()
        self.proxy_pass.setPlaceholderText("代理密码（不会回显）")
        self.proxy_pass.setEchoMode(QLineEdit.EchoMode.Password)
        auth.addWidget(self.proxy_user)
        auth.addWidget(self.proxy_pass)
        body.addLayout(auth)
        self.proxy_clear_auth = QCheckBox("清除已保存的代理认证")
        body.addWidget(self.proxy_clear_auth)
        actions = QHBoxLayout()
        actions.addWidget(button("保存代理设置", self.save_proxy, "primaryButton"))
        actions.addWidget(button("测试连接", self.test_proxy))
        actions.addWidget(button("重新读取", self.refresh_proxy))
        actions.addStretch()
        body.addLayout(actions)
        self.proxy_state = QLabel("正在读取代理设置与组件支持状态…")
        self.proxy_state.setWordWrap(True)
        body.addWidget(self.proxy_state)
        layout.addWidget(panel)
        layout.addStretch()
        self.proxy_loaded = False
        QTimer.singleShot(0, self.refresh_proxy)
        return page

    def _updates_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(26, 22, 26, 24)
        self._title(layout, "组件更新", "仅在你点击后准备已审核版本；切换前检查占用，旧版本保留可回滚。目标版不代表上游最新版本。")
        panel, body = card("llama.cpp 与 DeepSeek Harness")
        self.update_state = QLabel("正在读取本机版本和内置清单…")
        self.update_state.setWordWrap(True)
        body.addWidget(self.update_state)
        row = QHBoxLayout()
        self.update_component = QComboBox()
        self.update_component.addItem("llama.cpp 引擎", "engine")
        self.update_component.addItem("DeepSeek Harness", "dsh")
        self.update_channel = QComboBox()
        for label, key in (("CPU", "cpu"), ("NVIDIA CUDA", "cuda"),
                           ("Vulkan", "vulkan"), ("AMD ROCm", "rocm")):
            self.update_channel.addItem(label, key)
        self.update_component.currentIndexChanged.connect(
            lambda: self.update_channel.setEnabled(self.update_component.currentData() == "engine"))
        row.addWidget(QLabel("组件"))
        row.addWidget(self.update_component)
        row.addWidget(QLabel("引擎构建"))
        row.addWidget(self.update_channel)
        row.addStretch()
        body.addLayout(row)
        actions = QHBoxLayout()
        self.update_check_btn = button("检查已审核版本", self.check_component_updates)
        self.update_prepare_btn = button("准备新版本", self.prepare_component_update)
        self.update_activate_btn = button("切换到已准备版本", self.activate_component_update, "primaryButton")
        self.update_rollback_btn = button("回滚到上一路径", self.rollback_component_update)
        for control in (self.update_check_btn, self.update_prepare_btn,
                        self.update_activate_btn, self.update_rollback_btn):
            actions.addWidget(control)
        actions.addStretch()
        body.addLayout(actions)
        self.update_stage = QLabel("准备操作会在新目录下载并验证；不会覆盖现有引擎、DSH 配置或凭据。")
        self.update_stage.setWordWrap(True)
        body.addWidget(self.update_stage)
        layout.addWidget(panel)
        guide, guide_body = card("Node.js、驱动和模板")
        guide_text = QLabel(
            "Node.js 与系统驱动请在各自官方页面更新，完成后回到组件设置重新检测。"
            "固定 Qwen 模板与参考预设有版本关联；新模板须作为新版本显式绑定，不会覆盖旧预设。")
        guide_text.setWordWrap(True)
        guide_body.addWidget(guide_text)
        links = QHBoxLayout()
        for title, url in (("Node.js 官方下载", "https://nodejs.org/en/download"),
                           ("NVIDIA 驱动", "https://www.nvidia.com/en-us/drivers/"),
                           ("AMD 驱动", "https://www.amd.com/en/support/download/drivers.html"),
                           ("Intel 驱动", "https://www.intel.com/content/www/us/en/download-center/home.html")):
            links.addWidget(button(title, lambda u=url: QDesktopServices.openUrl(QUrl(u))))
        links.addStretch()
        guide_body.addLayout(links)
        layout.addWidget(guide)
        layout.addStretch()
        self.update_component.currentIndexChanged.connect(lambda: self._show_update_selection())
        QTimer.singleShot(0, self.check_component_updates)
        return page

    def _show_update_selection(self):
        component = self.update_component.currentData()
        prepared = self.update_prepared.get(component)
        self.update_activate_btn.setEnabled(bool(prepared) and not self.component_update_busy)
        self.update_channel.setEnabled(component == "engine" and not self.component_update_busy)

    def check_component_updates(self):
        if self.component_updates is None:
            self.update_state.setText("更新模块缺失，请重新解压完整发布包。")
            return
        if self.component_update_busy or self.quitting:
            return
        self.update_state.setText("正在核对本机路径与内置已审核版本…")
        self._async("component_update_check", self.component_updates.check_updates,
                    self._show_component_updates)

    def _show_component_updates(self, result):
        if not isinstance(result, dict) or not result.get("success"):
            self.update_state.setText((result or {}).get("message", "组件版本检查失败。"))
            return
        data = result.get("data") or {}
        engine, dsh = data.get("engine") or {}, data.get("dsh") or {}
        self.update_state.setText(
            f"引擎：当前 {engine.get('current') or '未检测到'}；已审核目标 {engine.get('target') or '未知'}"
            f"（{'受管理' if engine.get('managed') else '外部安装/未选择'}）\n"
            f"DSH：当前 {dsh.get('current') or '未检测到'}；已审核目标 {dsh.get('target') or '未知'}"
            f"（{'受管理' if dsh.get('managed') else '外部安装/未选择'}）\n"
            "外部或全局安装不会被原位覆盖；准备成功后需另行点击切换。")
        self._show_update_selection()

    def _run_component_update(self, key, job, complete):
        if self.component_updates is None or self.component_update_busy or self.busy or self.quitting or (
                getattr(self.provider_page, "workflow_running", False)):
            return
        self.component_update_busy = True
        self.stack.setEnabled(False)
        self.update_stage.setText("正在执行组件操作，请等待完成；不会自动停止运行中的服务。")
        self._show_update_selection()
        def done(result):
            self.component_update_busy = False
            if not self.quitting:
                self.stack.setEnabled(True)
            self.update_stage.setText((result or {}).get("message", "组件操作没有返回结果。"))
            complete(result or {})
            self._show_update_selection()
            self.refresh_runtime()
            self.refresh_components()
            self.check_component_updates()
        self._async(key, job, done)

    def prepare_component_update(self):
        component = self.update_component.currentData()
        channel = self.update_channel.currentData()
        label = "引擎" if component == "engine" else "DSH"
        extra = ("\nDSH 准备会在全新的受管理目录中执行锁定依赖的第三方安装脚本；"
                 "不会在现有 DSH 目录运行。" if component == "dsh" else "")
        if QMessageBox.question(self, TITLE,
                f"准备内置清单中的已审核 {label} 版本？将下载到独立目录并验证，现有版本不变。{extra}") != (
                QMessageBox.StandardButton.Yes):
            return
        def complete(result):
            if result.get("success"):
                self.update_prepared[component] = (result.get("data") or {}).get("prepared_id", "")
            else:
                QMessageBox.warning(self, TITLE, result.get("message", "更新准备失败。"))
        self._run_component_update("component_update_prepare",
                                   lambda: self.component_updates.prepare(component, channel), complete)

    def activate_component_update(self):
        component = self.update_component.currentData()
        prepared_id = self.update_prepared.get(component)
        if not prepared_id:
            self.update_stage.setText("请先准备并校验新版本。")
            return
        if QMessageBox.question(self, TITLE,
                "仅在对应服务已停止且目标身份核实后切换组件路径；不会自动停止外部或本应用服务。继续吗？") != (
                QMessageBox.StandardButton.Yes):
            return
        def complete(result):
            if result.get("success"):
                self.update_prepared.pop(component, None)
                self.cfg = backend.load_config()
                self.refresh_start_choices()
            else:
                QMessageBox.warning(self, TITLE, result.get("message", "切换失败；当前路径保持不变。"))
        self._run_component_update("component_update_activate",
                                   lambda: self.component_updates.activate(component, prepared_id), complete)

    def rollback_component_update(self):
        component = self.update_component.currentData()
        if QMessageBox.question(self, TITLE,
                "将检查运行占用并切回上一路径；已准备的版本不会删除。继续吗？") != (
                QMessageBox.StandardButton.Yes):
            return
        def complete(result):
            if result.get("success"):
                self.cfg = backend.load_config()
                self.refresh_start_choices()
            else:
                QMessageBox.warning(self, TITLE, result.get("message", "回滚失败；当前路径保持不变。"))
        self._run_component_update("component_update_rollback",
                                   lambda: self.component_updates.rollback(component), complete)

    def refresh_proxy(self):
        if network_proxy is None:
            self.proxy_state.setText("代理模块未安装。")
            return
        def work():
            return network_proxy.get_settings(), network_proxy.component_status()
        self._async("proxy_read", work, self._show_proxy)

    def _show_proxy(self, results):
        if not isinstance(results, tuple) or len(results) != 2:
            self.proxy_state.setText("读取代理设置失败。")
            return
        settings, status = results
        if not settings.get("success"):
            self.proxy_state.setText(settings.get("message", "读取代理设置失败。"))
            return
        data = settings.get("data") or {}
        if not self.proxy_loaded:
            self.proxy_mode.setCurrentIndex(max(0, self.proxy_mode.findData(data.get("mode"))))
            self.proxy_http.setText(data.get("http_url") or "")
            self.proxy_https.setText(data.get("https_url") or "")
            self.proxy_socks.setText(data.get("socks_url") or "")
            self.proxy_loaded = True
        capability = status.get("data") or {}
        dsh = capability.get("owned_dsh")
        dsh_label = {"unsupported_socks5": "SOCKS5 不支持；请选 HTTP/HTTPS 或直连",
                     "requires_restart": "本程序启动的 DSH 重启后生效",
                     "direct": "直连"}.get(dsh, "状态未知")
        self.proxy_state.setText(
            f"当前模式：{data.get('mode', '未知')} · 代理认证：{'已保存' if data.get('auth_configured') else '未设置'}\n"
            f"Hugging Face：{'可用' if capability.get('hugging_face') == 'supported' else '当前不可用'}；"
            f"本程序启动的 DSH：{dsh_label}。外部 DSH 请在其自身环境中设置代理。")

    def save_proxy(self):
        if network_proxy is None or self.busy or self.quitting:
            return
        value = {"mode": self.proxy_mode.currentData(),
                 "http_url": self.proxy_http.text().strip(),
                 "https_url": self.proxy_https.text().strip(),
                 "socks_url": self.proxy_socks.text().strip(),
                 "clear_auth": self.proxy_clear_auth.isChecked()}
        if self.proxy_user.text() or self.proxy_pass.text():
            value.update(username=self.proxy_user.text(), password=self.proxy_pass.text())
        def after(result):
            self.proxy_pass.clear()
            self.proxy_user.clear()
            self.proxy_state.setText(result.get("message", "代理设置失败。"))
            if result.get("success"):
                self.proxy_clear_auth.setChecked(False)
                self.proxy_loaded = False
                self.refresh_proxy()
        self._async("proxy_save", lambda: network_proxy.save_settings(value), after)

    def test_proxy(self):
        if network_proxy is None or self.quitting:
            return
        self.proxy_state.setText("正在按已保存设置测试 Hugging Face 连接…")
        self._async("proxy_test", network_proxy.test_connection,
                    lambda result: self.proxy_state.setText(result.get("message", "连接测试失败。")))

    def _components_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(26, 22, 26, 24)
        self._title(layout, "DSH 组件", "可选择已有 Node.js 与 DSH；也可先保存 Node 路径，再在「组件更新」主动准备受管理的 DSH。")
        panel, body = card("已有安装")
        self.component_state = QLabel("正在检测 Node.js 与 DSH…")
        self.component_state.setWordWrap(True)
        body.addWidget(self.component_state)
        dsh_row = QHBoxLayout()
        self.dsh_root_edit = QLineEdit()
        self.dsh_root_edit.setPlaceholderText("选择已有 DSH 安装根目录；可包含空格")
        dsh_row.addWidget(QLabel("DSH 目录"))
        dsh_row.addWidget(self.dsh_root_edit, 1)
        dsh_row.addWidget(button("浏览", self.choose_dsh_root))
        body.addLayout(dsh_row)
        node_row = QHBoxLayout()
        self.node_path_edit = QLineEdit()
        self.node_path_edit.setPlaceholderText("选择已有 node.exe")
        node_row.addWidget(QLabel("Node 程序"))
        node_row.addWidget(self.node_path_edit, 1)
        node_row.addWidget(button("浏览", self.choose_node_executable))
        body.addLayout(node_row)
        actions = QHBoxLayout()
        for label, method in (("重新检测", self.refresh_components),
                              ("检测并使用所选路径", self.save_components),
                              ("打开 Node.js 官方下载", lambda: self.open_component_download("node")),
                              ("打开 DSH 官方来源", lambda: self.open_component_download("dsh"))):
            actions.addWidget(button(label, method))
        actions.addStretch()
        body.addLayout(actions)
        layout.addWidget(panel)
        layout.addStretch()
        QTimer.singleShot(0, self.refresh_components)
        return page

    def open_components_settings(self):
        self.nav.setCurrentRow(6)
        self.settings_tabs.setCurrentIndex(4)

    def import_original_prompt_recipe(self):
        self._preview_prompt_recipe(None)

    def import_external_recipe(self):
        if self.busy or self.quitting:
            return
        path, _ = QFileDialog.getOpenFileName(self, "选择 DSH 伴航配置包", "", "JSON 配置包 (*.json)")
        if path:
            self._preview_prompt_recipe(path)

    def _preview_prompt_recipe(self, path: str | None):
        if recipe_importer is None:
            QMessageBox.critical(self, TITLE, "配套配置模块缺失，请重新解压完整发布包。")
            return
        if self.busy or self.quitting:
            self._note("请等待当前操作完成。")
            return
        self._note("正在核对配置包、本机模型和模板…")
        preview = (recipe_importer.preview_builtin_recipe if path is None else
                   lambda: recipe_importer.preview_recipe(path))
        self._async("recipe_preview", preview,
                    lambda result: self._handle_recipe_preview(path, result))

    def _handle_recipe_preview(self, path: str | None, preview: dict):
        if self.quitting:
            return
        if not isinstance(preview, dict) or not preview.get("success"):
            QMessageBox.warning(self, "配置包校验失败", (preview or {}).get("message", "配置包无法预览"))
            return
        complete = (not preview.get("unresolved") and
                    preview.get("validated_count") == preview.get("selected_count"))
        if complete:
            self._commit_prompt_recipe(path, {}, {}, preview)
            return
        dialog = RecipeBindingDialog(self, preview)
        if dialog.exec() != QDialog.DialogCode.Accepted or self.quitting:
            return
        bindings, templates = dialog.bindings(), dialog.templates()
        recheck = (lambda: recipe_importer.preview_builtin_recipe(bindings, templates) if path is None
                   else recipe_importer.preview_recipe(path, bindings, templates))
        self._async("recipe_recheck", recheck,
                    lambda result: self._confirm_bound_recipe(path, bindings, templates, result))

    def _confirm_bound_recipe(self, path: str | None, bindings: dict,
                              templates: dict, preview: dict):
        if self.quitting:
            return
        if not isinstance(preview, dict) or not preview.get("success"):
            QMessageBox.warning(self, "配置包校验失败", (preview or {}).get("message", "参数无效"))
            return
        unresolved = preview.get("unresolved") or []
        if unresolved:
            detail = "\n".join(f"{slot.get('label') or slot.get('role')}：" +
                               "；".join(x.get("message", "资源待绑定") for x in slot.get("missing") or [])
                               for slot in unresolved)
            count = preview.get("validated_count", 0)
            if not count:
                QMessageBox.warning(self, "仍需绑定资源", detail)
                return
            answer = QMessageBox.question(
                self, "只导入已匹配档位？",
                f"已有 {count} 个预设可导入，其余资源待绑定：\n{detail}\n\n仅导入已匹配档位吗？")
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._commit_prompt_recipe(path, bindings, templates, preview)

    def _commit_prompt_recipe(self, path: str | None, bindings: dict,
                              templates: dict, preview: dict):
        digest = preview.get("source_sha256") or ""
        def work():
            if path is None:
                return recipe_importer.import_builtin_recipe(bindings, templates, digest)
            return recipe_importer.import_recipe(path, bindings, templates, digest)
        def done(result):
            if not isinstance(result, dict) or not result.get("success"):
                return
            self.presets_page.refresh_all()
            self.refresh_start_choices()
            created = result.get("created") or []
            rebound = result.get("rebound") or []
            skipped = result.get("skipped") or []
            unresolved = result.get("unresolved") or []
            readiness = ("运行引擎已通过校验" if result.get("engine_ready") else
                         "运行引擎尚未就绪；预设已保存，启动前仍须选择并验证引擎")
            QMessageBox.information(self, "配套配置导入结果",
                                    f"新建 {len(created)} 个 · 重新绑定 {len(rebound)} 个 · "
                                    f"保留已有 {len(skipped)} 个 · 仍缺 {len(unresolved)} 组资源。\n"
                                    f"{readiness}\n高级实验档位未自动启用。")
        self._action(work, done)

    def export_current_recipe(self):
        if recipe_importer is None:
            QMessageBox.critical(self, TITLE, "配套配置模块缺失，请重新解压完整发布包。")
            return
        if self.busy or self.quitting:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出当前配套配置", "Qwen3.8-配套配置.json",
                                              "JSON 配置包 (*.json)")
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        self._action(lambda: recipe_importer.export_recipe(path),
                     lambda result: QMessageBox.information(
                         self, "配置包已导出", f"已导出 {result.get('count', 0)} 个预设：\n{result.get('path', path)}\n"
                         "不包含密钥、聊天、日志或模型权重。") if result.get("success") else None)

    def open_setup_guide(self):
        root = Path(getattr(sys, "_MEIPASS", backend.BASE_DIR))
        path = root / "resources" / "guides" / "从零配置指南.html"
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            QMessageBox.critical(self, TITLE, "内置指南缺失，请重新解压完整发布包。")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("DSH 伴航 · 从零配置")
        dialog.resize(900, 700)
        layout = QVBoxLayout(dialog)
        browser = QTextBrowser(dialog)
        browser.setOpenExternalLinks(True)
        browser.setHtml(content)
        layout.addWidget(browser)
        layout.addWidget(button("关闭指南", dialog.accept))
        dialog.exec()

    def refresh_components(self):
        if not self.integration or not hasattr(self.integration, "component_status"):
            self.component_state.setText("组件检测正在准备中。")
            return
        self._async("dsh_components", self.integration.component_status, self._show_components)

    def _show_components(self, result):
        if not isinstance(result, dict):
            self.component_state.setText("组件检测失败。")
            return
        data = result.get("data") or {}
        self.component_info = data
        missing = data.get("missing") or []
        self.component_state.setText(
            f"DSH：{data.get('dsh_version') or '未检测到'} · Node：{data.get('node_version') or '未检测到'}\n"
            f"缺少：{'、'.join(map(str, missing)) if missing else '无'}\n"
            + (result.get("message") or "")
            + "\n步骤：①准备 Node.js；②选择已有 DSH 或在「组件更新」主动准备已审核版；"
              "③选择对应路径并重新检测。")
        if (not self.dsh_root_edit.text().strip() and data.get("dsh_installed")
                and data.get("dsh_root")):
            self.dsh_root_edit.setText(str(data["dsh_root"]))
        if not self.node_path_edit.text().strip() and data.get("node_executable"):
            self.node_path_edit.setText(str(data["node_executable"]))
        self._update_buttons()

    def choose_dsh_root(self):
        path = QFileDialog.getExistingDirectory(self, "选择已有 DSH 安装目录", "")
        if path:
            self.dsh_root_edit.setText(path)

    def choose_node_executable(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 node.exe", "", "node.exe (node.exe)")
        if path:
            self.node_path_edit.setText(path)

    def save_components(self):
        if not self.integration or not hasattr(self.integration, "configure_components"):
            return
        if self.busy or self.quitting:
            return
        dsh_root = self.dsh_root_edit.text().strip() or None
        node_path = self.node_path_edit.text().strip() or None
        if not dsh_root and not node_path:
            self.component_state.setText("请至少选择一个已有安装路径。")
            return
        self._async("dsh_configure", lambda: self.integration.configure_components(
            dsh_root=dsh_root, node_executable=node_path),
            lambda result: (self.component_state.setText((result or {}).get("message", "检测失败")),
                            self.refresh_components(), self.refresh_dsh()))

    def open_component_download(self, component: str):
        info = getattr(self, "component_info", {}).get("downloads") or {}
        item = info.get(component) or {}
        url = item.get("url") if isinstance(item, dict) else item
        if not isinstance(url, str) or not url.startswith("https://"):
            self.component_state.setText("官方来源尚未检测到可用链接，请刷新组件状态。")
            return
        QDesktopServices.openUrl(QUrl(url))

    def refresh_runtime(self):
        if runtime_manager is None:
            self.runtime_state.setText("运行环境管理模块尚未安装。")
            return
        self._async("runtime_check", runtime_manager.check_runtime, self._show_runtime)

    def _show_runtime(self, result):
        if not isinstance(result, dict):
            self.runtime_state.setText("运行环境检查失败。")
            return
        selected = result.get("selected") or "尚未选择"
        recommended = "、".join(result.get("recommended") or []) or "CPU"
        current_backend = result.get("backend") or "未知"
        self.runtime_state.setText(
            f"当前引擎：{selected}\n检测：{result.get('message') or '状态未知'}\n"
            f"版本：{result.get('version') or '未知'} · 后端：{current_backend} · 下载候选：{recommended}")
        if selected and selected != "尚未选择" and not self.runtime_path_edit.text().strip():
            self.runtime_path_edit.setText(selected)
        steps = result.get("steps") or []
        if steps:
            self.runtime_progress.setText("\n".join(str(x) for x in steps))

    def _show_runtime_progress(self, message: str):
        if hasattr(self, "runtime_progress"):
            self.runtime_progress.setText(message)

    def _runtime_write_allowed(self):
        if not self.model_session or self.last_status.get("control_read_only"):
            self.runtime_progress.setText(self.read_only_reason or "旧管理器正在控制模型，请先关闭后再更改运行环境。")
            return False
        if self.last_status.get("primary_pid") or self.busy or self.quitting:
            self.runtime_progress.setText("请先停止当前模型并等待其他操作结束。")
            return False
        return True

    def choose_runtime_executable(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 llama-server.exe", "", "llama-server.exe (llama-server.exe)")
        if path:
            self.runtime_path_edit.setText(path)

    def choose_runtime_folder(self):
        path = QFileDialog.getExistingDirectory(self, "选择已解压的 llama.cpp 文件夹", "")
        if path:
            self.runtime_path_edit.setText(path)

    def open_runtime_download(self):
        if runtime_manager is None:
            return
        channel = self.runtime_channel.currentData() or "cpu"
        self._async("runtime_link", lambda: runtime_manager.open_official_download(channel),
                    lambda result: self.runtime_progress.setText((result or {}).get("message", "无法打开官方页面")))

    def select_runtime_executable(self):
        if runtime_manager is None or not self._runtime_write_allowed():
            return
        path = self.runtime_path_edit.text().strip()
        if not path:
            self.runtime_progress.setText("请先选择下载并解压后的 llama-server.exe 或所在文件夹。")
            return
        self._async("runtime_action", lambda: runtime_manager.select_executable(path),
                    lambda result: (self.runtime_progress.setText((result or {}).get("message", "检测失败")),
                                    self.refresh_runtime()))

    def export_diagnostics(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出脱敏诊断", "DSH-Companion-diagnostics.json", "JSON (*.json)")
        if not path:
            return
        status = self.last_status
        dsh = self.dsh_status
        detected_dsh = (getattr(getattr(self, "provider_page", None), "current_status", {})
                        .get("data") or {})
        safe_params = {}
        for key in ("ctx_size", "ngl", "batch_size", "ubatch_size", "parallel",
                    "cache_type_k", "cache_type_v", "flash_attn", "jinja", "temp",
                    "top_p", "top_k", "min_p", "presence_penalty", "reasoning_effort",
                    "reasoning_budget"):
            value = (status.get("active_effective_params") or {}).get(key)
            if type(value) in (str, int, float, bool) and len(str(value)) <= 80:
                safe_params[key] = value
        def scrub(value):
            text = str(value)[:300]
            text = re.sub(r"(?i)(bearer\s+)\S+", r"\1[REDACTED]", text)
            text = re.sub(r"(?i)((?:api[_-]?key|token|secret|password)\s*[:=]\s*)\S+",
                          r"\1[REDACTED]", text)
            text = re.sub(r"(?i)(https?://[^\s?#]+)[?#][^\s]+", r"\1?[REDACTED]", text)
            return text
        payload = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "application": TITLE,
            "python_version": platform.python_version(),
            "system": platform.system(),
            "dsh_version": detected_dsh.get("version") if isinstance(detected_dsh.get("version"), str) else None,
            "model": {
                "state": status.get("state"),
                "api_online": bool(status.get("api_online")),
                "port": status.get("port"),
                "instance_count": len(status.get("model_instances") or []),
                "crash_protected": bool(status.get("crash_protected")),
                "control_read_only": bool(status.get("control_read_only")),
                "engine_version": status.get("engine_version") if isinstance(status.get("engine_version"), str) else None,
                "effective_parameters": safe_params,
                "last_error": scrub(status.get("state_text", "")) if status.get("state") == "ERROR" else None,
                "last_exit_code": status.get("exit_code") if type(status.get("exit_code")) is int else None,
            },
            "dsh": {
                "running": bool(dsh.get("running")),
                "owned": bool(dsh.get("owned")),
                "port": dsh.get("port"),
                "instance_count": len(dsh.get("instances") or []),
            },
            "settings": {
                "lan_enabled": bool(self.cfg.get("lan_access", {}).get("enabled")),
                "api_key_enabled": bool(self.cfg.get("api_key", {}).get("enabled")),
                "startup_enabled": autostart.is_enabled(),
                "auto_model_enabled": bool(self.cfg.get("autostart_model_on_manager_open")),
                "start_in_tray": bool(self.cfg.get("start_minimized_to_tray")),
            },
        }
        payload["recent_model_logs"] = "未导出：当前日志可能含请求正文或认证参数"
        try:
            Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            self._note("脱敏诊断已导出。")
        except OSError as exc:
            QMessageBox.critical(self, TITLE, f"诊断导出失败：{exc}")

    def _title(self, layout: QVBoxLayout, title: str, subtitle: str):
        label = QLabel(title)
        label.setObjectName("pageTitle")
        layout.addWidget(label)
        sub = QLabel(subtitle)
        sub.setObjectName("pageSubtitle")
        sub.setWordWrap(True)
        layout.addWidget(sub)

    def _overview_page(self):
        scroll, _, layout = scroll_page()
        self.stack.addWidget(scroll)
        self._title(layout, "首页", "本地模型与 DeepSeek Harness 的运行状态，一眼看清。")
        hero, hero_layout = card()
        hero.setObjectName("heroCard")
        hero.setMinimumHeight(560)
        hero_layout.setContentsMargins(34, 30, 34, 30)
        hero_layout.setSpacing(26)
        hero_top = QHBoxLayout()
        hero_top.setSpacing(28)
        compass = QLabel()
        compass.setPixmap(app_icon().pixmap(110, 110))
        compass.setFixedSize(124, 124)
        compass.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hero_top.addWidget(compass)
        status_column = QVBoxLayout()
        status_column.setSpacing(10)
        status_column.addStretch()
        self.state_label = QLabel("正在读取状态…")
        self.state_label.setObjectName("homeHeroTitle")
        status_column.addWidget(self.state_label)
        self.detail_label = QLabel("正在连接本地状态…")
        self.detail_label.setObjectName("homeHeroDetail")
        self.detail_label.setWordWrap(True)
        status_column.addWidget(self.detail_label)
        status_column.addStretch()
        hero_top.addLayout(status_column, 1)
        hero_layout.addLayout(hero_top)

        summary = QFrame()
        summary.setObjectName("homeSummary")
        summary_layout = QVBoxLayout(summary)
        summary_layout.setContentsMargins(20, 14, 20, 14)
        summary_layout.setSpacing(0)
        model_row = QHBoxLayout()
        model_row.setContentsMargins(0, 6, 0, 13)
        model_title = QLabel("当前模型")
        model_title.setObjectName("homeRowLabel")
        model_title.setFixedWidth(116)
        model_row.addWidget(model_title)
        self.model_combo = QComboBox()
        self.model_combo.addItem("正在读取模型库…", None)
        self.model_combo.currentIndexChanged.connect(self._refresh_start_presets)
        self.model_combo.setObjectName("homeModelSelect")
        self.model_combo.setMinimumHeight(44)
        model_row.addWidget(self.model_combo, 1)
        summary_layout.addLayout(model_row)
        divider1 = QFrame()
        divider1.setFrameShape(QFrame.Shape.HLine)
        divider1.setObjectName("homeDivider")
        summary_layout.addWidget(divider1)
        running_row = QHBoxLayout()
        running_row.setContentsMargins(0, 13, 0, 13)
        running_title = QLabel("运行状态")
        running_title.setObjectName("homeRowLabel")
        running_title.setFixedWidth(116)
        running_row.addWidget(running_title)
        self.home_model_state = QLabel("正在检测…")
        self.home_model_state.setObjectName("homeRowValue")
        running_row.addWidget(self.home_model_state, 1)
        summary_layout.addLayout(running_row)
        divider2 = QFrame()
        divider2.setFrameShape(QFrame.Shape.HLine)
        divider2.setObjectName("homeDivider")
        summary_layout.addWidget(divider2)
        dsh_row = QHBoxLayout()
        dsh_row.setContentsMargins(0, 13, 0, 6)
        dsh_title = QLabel("DSH 连接")
        dsh_title.setObjectName("homeRowLabel")
        dsh_title.setFixedWidth(116)
        dsh_row.addWidget(dsh_title)
        self.quick_dsh_state = QLabel("正在检测…")
        self.quick_dsh_state.setObjectName("homeRowValue")
        dsh_row.addWidget(self.quick_dsh_state, 1)
        summary_layout.addLayout(dsh_row)
        hero_layout.addWidget(summary)

        actions = QHBoxLayout()
        actions.setSpacing(14)
        self.start_btn = button("一键启动", self.start_local_work_from_overview, "primaryButton")
        self.start_btn.setToolTip("启动或复用所选本地模型，接入 DSH 并打开认证界面。")
        self.stop_btn = button("停止本应用服务", self.stop_owned_services)
        self.dsh_open_btn = button("打开 DSH", self.open_dsh)
        self.chat_nav_btn = button("试聊", lambda: self.nav.setCurrentRow(5))
        for widget in (self.start_btn, self.stop_btn, self.dsh_open_btn, self.chat_nav_btn):
            widget.setProperty("homeAction", True)
            widget.setMinimumHeight(56)
            actions.addWidget(widget, 1)
        hero_layout.addLayout(actions)
        self.prep_hint = QLabel("正在检查所需组件…")
        self.prep_hint.setObjectName("homeHint")
        self.prep_hint.setWordWrap(True)
        hero_layout.addWidget(self.prep_hint)
        self.complete_setup_btn = button("去工作台完成配置", self.toggle_advanced_controls)
        self.complete_setup_btn.setObjectName("homeSetupButton")
        hero_layout.addWidget(self.complete_setup_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.note_label = QLabel("模型启动后可直接试聊。")
        self.note_label.setObjectName("homeNote")
        self.note_label.setWordWrap(True)
        hero_layout.addWidget(self.note_label)
        layout.addWidget(hero)
        layout.addStretch()

        self.advanced_controls = QWidget()
        self.advanced_controls.setObjectName("pageSurface")
        advanced_layout = QVBoxLayout(self.advanced_controls)
        advanced_layout.setContentsMargins(26, 22, 26, 24)
        advanced_layout.setSpacing(14)
        self._title(advanced_layout, "工作台", "在这里选择预设、导入导出配置，并分别控制模型与 DSH。")
        setup_card, setup_layout = card("模型参数与配置包")
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("所选模型的参数预设"))
        self.start_preset_combo = QComboBox()
        self.start_preset_combo.addItem("使用模型默认参数", None)
        preset_row.addWidget(self.start_preset_combo, 1)
        setup_layout.addLayout(preset_row)
        setup_actions = QHBoxLayout()
        for label, handler in (("运行环境与 DSH", self.open_components_settings),
                               ("从零配置指南", self.open_setup_guide),
                               ("导入 Qwen 参考配置", self.import_original_prompt_recipe),
                               ("导入本地配置包", self.import_external_recipe),
                               ("导出当前配套配置", self.export_current_recipe)):
            setup_actions.addWidget(button(label, handler))
        setup_actions.addStretch()
        setup_layout.addLayout(setup_actions)
        advanced_layout.addWidget(setup_card)
        self.take_control_btn = button("接管模型控制", self.take_control)
        advanced_layout.addWidget(self.take_control_btn)
        grid, grid_layout = card("模型详情与单独控制")
        self.metrics_label = QLabel("等待状态数据")
        self.metrics_label.setWordWrap(True)
        grid_layout.addWidget(self.metrics_label)
        model_actions = QHBoxLayout()
        self.model_only_start_btn = button("仅启动模型", self.start_model)
        self.switch_btn = button("切换并启动所选模型", self.switch_and_start)
        self.model_only_stop_btn = button("仅停止模型", self.stop_model)
        self.restart_btn = button("重启模型", self.restart_model)
        self.force_model_btn = button("强制停止指定实例", self.force_stop_model, "dangerButton")
        self.model_combo.currentIndexChanged.connect(self._update_buttons)
        for widget in (self.model_only_start_btn, self.switch_btn,
                       self.model_only_stop_btn, self.restart_btn,
                       self.force_model_btn, button("刷新状态", self.refresh_status)):
            model_actions.addWidget(widget)
        model_actions.addStretch()
        grid_layout.addLayout(model_actions)
        advanced_layout.addWidget(grid)
        dsh_card, dsh_layout = card("DeepSeek Harness")
        self.dsh_state_label = QLabel("正在读取状态…")
        self.dsh_state_label.setObjectName("statusBadge")
        self.dsh_state_label.setWordWrap(True)
        dsh_layout.addWidget(self.dsh_state_label)
        dsh_actions = QHBoxLayout()
        self.dsh_start_btn = button("仅启动 DSH", self.start_dsh)
        self.dsh_stop_btn = button("停止本窗口启动的 DSH", self.stop_dsh)
        self.dsh_force_btn = button("强制停止指定实例", self.force_stop_dsh, "dangerButton")
        for widget in (self.dsh_start_btn, self.dsh_stop_btn, self.dsh_force_btn):
            dsh_actions.addWidget(widget)
        dsh_actions.addStretch()
        dsh_layout.addLayout(dsh_actions)
        advanced_layout.addWidget(dsh_card)
        advanced_layout.addStretch()
        QTimer.singleShot(0, self.refresh_start_choices)

    def _workbench_page(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.advanced_controls)
        self.stack.addWidget(scroll)

    def toggle_advanced_controls(self):
        self.nav.setCurrentRow(1)

    def start_local_work_from_overview(self):
        if self.busy or self.quitting:
            return
        if not self.start_btn.isEnabled():
            self._note(self.prep_hint.text())
            return
        self.workflow_error = ""
        self.workflow_ready = False
        self.workflow_stage = "启动模型 → 连接 DSH → 配置本地模型 → 就绪：正在检查所需组件…"
        self.prep_hint.setText(self.workflow_stage)
        self.prep_hint.show()
        self._note(self.workflow_stage)
        self.provider_page.start_local_work()
        if not self.provider_page.workflow_running:
            self.workflow_stage = ""
            self.workflow_error = "未启动：" + self.provider_page.stage.text()
            self._update_buttons()

    def _show_home_work_stage(self, message: str):
        if not self.provider_page.workflow_running:
            return
        stage = message.split(" · ", 1)[0]
        labels = {
            "detect": "检查组件", "model": "启动模型", "dsh": "连接 DSH",
            "provider": "配置本地模型", "default": "设为新会话默认",
            "open": "打开 DSH", "ready": "就绪",
        }
        current = labels.get(stage, stage)
        self.workflow_stage = f"启动模型 → 连接 DSH → 配置本地模型 → 就绪｜当前：{current}"
        self.prep_hint.setText(self.workflow_stage)
        self.prep_hint.show()
        self.state_label.setText(f"正在准备：{current}")

    def stop_owned_services(self):
        if self.busy or self.quitting or self.provider_page.workflow_running:
            return
        self.cancel_chat()
        def work():
            messages = []
            if self.dsh:
                dsh_result = self.dsh.cleanup()
                if not dsh_result.get("success"):
                    return dsh_result
                messages.append("本窗口启动的 DSH 已停止；外部实例保留")
            if self.model_session and not self.last_status.get("control_read_only"):
                model_result = backend.stop_model()
                if not model_result.get("success"):
                    return model_result
                messages.append("本应用管理的模型已停止")
            return {"success": True, "message": "；".join(messages) or "本应用没有需要停止的服务"}
        self._action(work, lambda _result: self.refresh_dsh())

    def refresh_start_choices(self, preferred_model: str | None = None):
        def fetch():
            return {"models": model_catalog.list_models(), "presets": parameter_store.list_presets()}
        self._async("start_choices", fetch,
                    lambda result: self._show_start_choices(result, preferred_model))

    def _show_start_choices(self, result, preferred_model):
        if not isinstance(result, dict) or "models" not in result:
            self._note((result or {}).get("message", "模型库读取失败"))
            return
        current = preferred_model or self.model_combo.currentData()
        self._start_presets = result.get("presets") or []
        models = result.get("models") or []
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        active_profile = self.cfg.get("active_profile")
        if not any(row.get("id") == active_profile for row in models):
            self.model_combo.addItem("尚未选择模型" if active_profile == "select_model"
                                     else "当前配置档位", active_profile)
        for row in models:
            title = row.get("name") or row.get("id")
            suffix = (" · 投影文件/不可单独启动" if row.get("startable") is False else
                      " · 文件不完整" if not row.get("complete") else "")
            self.model_combo.addItem(f"{title}{suffix}", row.get("id"))
            if not row.get("complete") or row.get("startable") is False:
                self.model_combo.model().item(self.model_combo.count() - 1).setEnabled(False)
        index = self.model_combo.findData(current)
        self.model_combo.setCurrentIndex(index if index >= 0 else 0)
        self.model_combo.blockSignals(False)
        self._refresh_start_presets()
        self._update_buttons()

    def _refresh_start_presets(self):
        model_id = self.model_combo.currentData()
        previous = self.start_preset_combo.currentData()
        self.start_preset_combo.clear()
        self.start_preset_combo.addItem("使用模型默认参数", None)
        for preset in getattr(self, "_start_presets", []):
            if preset.get("model_id") == model_id:
                self.start_preset_combo.addItem(preset.get("name") or preset.get("id"), preset.get("id"))
        if previous is None:
            previous = (self.cfg.get("profiles", {}).get(model_id) or {}).get("last_preset_id")
        index = self.start_preset_combo.findData(previous)
        if index >= 0:
            self.start_preset_combo.setCurrentIndex(index)

    def _chat_page(self):
        page = QWidget()
        page.setObjectName("pageSurface")
        self.stack.addWidget(page)
        outer = QVBoxLayout(page)
        outer.setContentsMargins(26, 22, 26, 24)
        outer.setSpacing(15)
        self._title(outer, "模型试聊", "直接与当前运行的模型对话；内容只保留在本次管理器会话中。")
        self.chat_info = QLabel("模型未运行。启动模型后即可发送消息。")
        self.chat_info.setObjectName("statusBadge")
        outer.addWidget(self.chat_info)
        self.chat_parameters = QLabel("有效参数：等待模型状态…")
        self.chat_parameters.setObjectName("statusBadge")
        self.chat_parameters.setWordWrap(True)
        outer.addWidget(self.chat_parameters)
        self.system_prompt = QTextEdit()
        self.system_prompt.setPlaceholderText("可选：本次对话的系统提示词；仅保留在当前窗口内")
        self.system_prompt.setFixedHeight(66)
        outer.addWidget(self.system_prompt)
        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("单次生成上限（max_tokens）"))
        self.chat_output_limit = QLineEdit("8192")
        self.chat_output_limit.setFixedWidth(100)
        self.chat_output_limit.setToolTip("这是试聊请求允许的最多输出 token；与上下文长度和 DSH 请求上限分别设置。")
        output_row.addWidget(self.chat_output_limit)
        output_row.addStretch()
        outer.addLayout(output_row)
        self.chat_area = QScrollArea()
        self.chat_area.setWidgetResizable(True)
        self.chat_area.setFrameShape(QFrame.Shape.NoFrame)
        self.chat_body = QWidget()
        self.chat_body.setObjectName("pageSurface")
        self.chat_layout = QVBoxLayout(self.chat_body)
        self.chat_layout.setContentsMargins(8, 8, 8, 8)
        self.chat_layout.setSpacing(12)
        self.chat_layout.addStretch()
        self.chat_area.setWidget(self.chat_body)
        outer.addWidget(self.chat_area, 1)
        self.chat_input = QTextEdit()
        self.chat_input.setPlaceholderText("输入消息后点击发送。")
        self.chat_input.setFixedHeight(100)
        outer.addWidget(self.chat_input)
        actions = QHBoxLayout()
        self.send_btn = button("发送消息", self.send_chat, "primaryButton")
        self.cancel_btn = button("停止生成", self.cancel_chat)
        self.cancel_btn.setEnabled(False)
        actions.addWidget(self.send_btn)
        actions.addWidget(self.cancel_btn)
        actions.addWidget(button("复制回答", self.copy_answer))
        actions.addWidget(button("清空对话", self.clear_chat))
        actions.addStretch()
        outer.addLayout(actions)
        self._add_bubble("欢迎", "启动模型后，可在这里快速验证它的回答。", "bubbleAssistant")

    def _add_bubble(self, heading: str, content: str, role: str) -> QLabel:
        frame = QFrame()
        frame.setObjectName(role)
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(16, 12, 16, 12)
        name = QLabel(heading)
        name.setObjectName("bubbleHeading")
        frame_layout.addWidget(name)
        body = QLabel(content)
        body.setTextFormat(Qt.TextFormat.PlainText)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setWordWrap(True)
        frame_layout.addWidget(body)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, frame)
        QTimer.singleShot(0, lambda: self.chat_area.verticalScrollBar().setValue(
            self.chat_area.verticalScrollBar().maximum()))
        return body

    def _profiles_page(self):
        scroll, _, layout = scroll_page()
        self.stack.addWidget(scroll)
        self._title(layout, "模型档位", "选择模型并调整加载参数；运行中的模型需要重启后应用修改。")
        profile_card, profile_layout = card("选择档位")
        bar = QHBoxLayout()
        self.profile_combo = QComboBox()
        self.profile_combo.currentIndexChanged.connect(self._selected_profile)
        bar.addWidget(self.profile_combo, 1)
        bar.addWidget(button("切换并加载", self.switch_profile, "primaryButton"))
        bar.addWidget(button("复制", self.copy_profile))
        bar.addWidget(button("删除", self.delete_profile))
        profile_layout.addLayout(bar)
        layout.addWidget(profile_card)
        parameters, parameters_layout = card("模型参数")
        form = QFormLayout()
        form.setSpacing(10)
        self.profile_fields = {}
        for key, label, _ in FIELDS:
            entry = QLineEdit()
            self.profile_fields[key] = entry
            if key in ("model_path", "template_path"):
                path_row = QWidget()
                row = QHBoxLayout(path_row)
                row.setContentsMargins(0, 0, 0, 0)
                row.addWidget(entry, 1)
                row.addWidget(button("浏览", lambda _=False, k=key: self.choose_file(self.profile_fields[k])))
                form.addRow(label, path_row)
            else:
                form.addRow(label, entry)
        self.flash_check = QCheckBox("Flash Attention")
        form.addRow("", self.flash_check)
        parameters_layout.addLayout(form)
        save_row = QHBoxLayout()
        save_row.addWidget(button("保存当前参数", self.save_profile, "primaryButton"))
        save_row.addWidget(button("撤销修改", self._load_profile))
        save_row.addStretch()
        parameters_layout.addLayout(save_row)
        layout.addWidget(parameters)
        layout.addStretch()
        self._refresh_profiles()

    def _network_page(self):
        scroll, _, layout = scroll_page()
        self.stack.addWidget(scroll)
        self._title(layout, "接口与网络", "配置本地 API 地址和局域网访问。")
        api, api_layout = card("服务设置")
        form = QFormLayout()
        self.port_edit = QLineEdit(str(self.cfg.get("default_port", 24548)))
        self.server_edit = QLineEdit(self.cfg.get("server_executable", ""))
        form.addRow("模型 API 端口", self.port_edit)
        file_row = QWidget()
        file_layout = QHBoxLayout(file_row)
        file_layout.setContentsMargins(0, 0, 0, 0)
        file_layout.addWidget(self.server_edit, 1)
        file_layout.addWidget(button("浏览", lambda: self.choose_file(self.server_edit)))
        form.addRow("服务程序", file_row)
        self.lan_check = QCheckBox("允许局域网访问")
        self.lan_check.setChecked(bool(self.cfg.get("lan_access", {}).get("enabled")))
        form.addRow("", self.lan_check)
        self.ip_combo = QComboBox()
        self.ip_combo.setEditable(False)
        form.addRow("网卡 IP", self.ip_combo)
        self.key_check = QCheckBox("启用 API Key")
        self.key_check.setChecked(bool(self.cfg.get("api_key", {}).get("enabled")))
        form.addRow("", self.key_check)
        self.key_edit = QLineEdit(self.cfg.get("api_key", {}).get("key", ""))
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("API Key", self.key_edit)
        api_layout.addLayout(form)
        key_row = QHBoxLayout()
        key_row.addWidget(button("显示 / 隐藏", self.toggle_key))
        key_row.addWidget(button("生成密钥", self.generate_key))
        key_row.addWidget(button("复制密钥", lambda: self.copy_text(self.key_edit.text())))
        key_row.addWidget(button("刷新网卡", self.refresh_ips))
        key_row.addStretch()
        api_layout.addLayout(key_row)
        api_layout.addWidget(button("保存接口设置", self.save_network, "primaryButton"))
        layout.addWidget(api)
        address, address_layout = card("连接信息")
        self.local_url_edit = QLineEdit()
        self.lan_url_edit = QLineEdit()
        for caption, edit in (("本机模型 API", self.local_url_edit), ("局域网模型 API", self.lan_url_edit)):
            row = QHBoxLayout()
            row.addWidget(QLabel(caption))
            edit.setReadOnly(True)
            row.addWidget(edit, 1)
            row.addWidget(button("复制", lambda field=edit: self.copy_text(field.text()) if field.text() else self._note("当前没有可复制的地址。")))
            address_layout.addLayout(row)
        layout.addWidget(address)
        layout.addStretch()
        self.refresh_ips()
        self._update_urls()

    def _tools_page(self):
        scroll, _, layout = scroll_page()
        self.stack.addWidget(scroll)
        self._title(layout, "工具与设置", "查看日志、运行检查脚本，以及设置管理器的启动方式。")
        startup, startup_layout = card("开机启动")
        self.startup_check = QCheckBox("登录 Windows 后启动管理器")
        self.startup_check.setChecked(autostart.is_enabled())
        self.startup_check.toggled.connect(self.set_startup)
        startup_layout.addWidget(self.startup_check)
        self.auto_model_check = QCheckBox("启动管理器后自动加载所选模型")
        self.auto_model_check.setChecked(bool(self.cfg.get("autostart_model_on_manager_open")))
        self.auto_model_check.toggled.connect(lambda enabled: self.save_start_option(
            "autostart_model_on_manager_open", enabled, self.auto_model_check))
        startup_layout.addWidget(self.auto_model_check)
        self.start_tray_check = QCheckBox("启动时收起到托盘")
        self.start_tray_check.setChecked(bool(self.cfg.get("start_minimized_to_tray", False)))
        self.start_tray_check.toggled.connect(lambda enabled: self.save_start_option(
            "start_minimized_to_tray", enabled, self.start_tray_check))
        startup_layout.addWidget(self.start_tray_check)
        self.minimize_tray_check = QCheckBox("最小化时收起到托盘（默认留在任务栏）")
        self.minimize_tray_check.setChecked(bool(self.cfg.get("minimize_to_tray", False)))
        self.minimize_tray_check.toggled.connect(lambda enabled: self.save_start_option(
            "minimize_to_tray", enabled, self.minimize_tray_check))
        startup_layout.addWidget(self.minimize_tray_check)
        close_row = QHBoxLayout()
        close_row.addWidget(QLabel("点击关闭按钮时"))
        self.close_action_combo = QComboBox()
        for label, value in (("每次询问", "ask"), ("退出并停止本应用服务", "exit"),
                             ("收起到托盘，继续运行", "tray")):
            self.close_action_combo.addItem(label, value)
        selected = self.close_action_combo.findData(self.cfg.get("close_action", "ask"))
        self.close_action_combo.setCurrentIndex(max(selected, 0))
        self.close_action_combo.currentIndexChanged.connect(self.save_close_action)
        close_row.addWidget(self.close_action_combo, 1)
        startup_layout.addLayout(close_row)
        startup_layout.addWidget(QLabel("托盘不可用时窗口留在任务栏；退出只停止本应用启动的模型与 DSH。"))
        layout.addWidget(startup)
        scripts, scripts_layout = card("一次性检查脚本")
        self.script_combo = QComboBox()
        self.script_names = {}
        for item in backend.list_scripts():
            self.script_combo.addItem(f"{item['title']} · {item['filename']}", item["filename"])
        scripts_layout.addWidget(self.script_combo)
        scripts_layout.addWidget(button("运行选中脚本", self.run_script))
        self.script_output = QTextEdit()
        self.script_output.setReadOnly(True)
        self.script_output.setFixedHeight(130)
        scripts_layout.addWidget(self.script_output)
        layout.addWidget(scripts)
        logs, logs_layout = card("运行日志")
        logs_layout.addWidget(button("刷新日志", self.refresh_logs))
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setFixedHeight(170)
        logs_layout.addWidget(self.log_output)
        layout.addWidget(logs)
        layout.addStretch()

    def _setup_tray(self):
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip(TITLE)
        menu = QMenu(self)
        menu.addAction("打开管理器", self.restore_window)
        menu.addAction("停止模型", self.stop_model)
        menu.addSeparator()
        menu.addAction("完全退出", self.request_exit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.restore_window() if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick) else None)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def restore_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def changeEvent(self, event):
        super().changeEvent(event)
        if (event.type() == QEvent.Type.WindowStateChange and self.isMinimized()
                and self.cfg.get("minimize_to_tray", False)):
            if hasattr(self, "tray") and self.tray.isVisible():
                QTimer.singleShot(0, self.hide)
            else:
                self._note("系统托盘不可用，窗口保持最小化。")

    def closeEvent(self, event: QCloseEvent):
        if self._allow_close:
            event.accept()
        else:
            event.ignore()
            action = self.cfg.get("close_action", "ask")
            if action not in ("exit", "tray"):
                choice = QMessageBox(self)
                choice.setWindowTitle(TITLE)
                choice.setText("关闭 DSH 伴航后，如何处理本应用启动的服务？")
                choice.setInformativeText("退出会停止本应用启动的模型与 DSH；收起到托盘会继续运行。外部服务不受影响。")
                exit_button = choice.addButton("退出并停止本应用服务", QMessageBox.ButtonRole.AcceptRole)
                tray_button = choice.addButton("收起到托盘，继续运行", QMessageBox.ButtonRole.ActionRole)
                tray_button.setEnabled(hasattr(self, "tray") and self.tray.isVisible())
                choice.addButton("取消", QMessageBox.ButtonRole.RejectRole)
                remember = QCheckBox("记住我的选择（以后可在设置中修改）")
                choice.setCheckBox(remember)
                choice.exec()
                if choice.clickedButton() is exit_button:
                    action = "exit"
                elif choice.clickedButton() is tray_button:
                    action = "tray"
                else:
                    return
                if remember.isChecked():
                    self._save_close_action_value(action)
            if action == "tray" and hasattr(self, "tray") and self.tray.isVisible():
                self.hide()
                self._note("已收起到托盘；模型与 DSH 继续运行。")
            elif action == "tray":
                self._note("系统托盘不可用，窗口保持打开。")
            else:
                self.request_exit()

    def _async(self, key: str, job, callback):
        self._request_seq += 1
        request_key = f"{key}#{self._request_seq}"
        self.callbacks[request_key] = callback
        clicked_button = active_button()
        if clicked_button is not None:
            self._pending_button_requests[request_key] = clicked_button
            begin_pending(clicked_button)
        if key in {"action", "dsh_install", "dsh_attach", "provider_save", "provider_delete",
                   "provider_set_default", "hf_start", "hf_action", "hf_token_save",
                   "hf_token_clear", "preset_reset", "preset_restore", "preset_import",
                   "preset_save", "preset_export", "library_scan", "library_scan_dir",
                   "library_add", "favorite", "library_rebind", "start_work",
                   "start_local_work", "take_control", "runtime_action", "dsh_configure",
                   "recipe_import", "recipe_export", "component_update_prepare",
                   "component_update_activate", "component_update_rollback"}:
            self._critical_requests.add(request_key)
        def work():
            try:
                result = job()
            except Exception as exc:
                result = {"success": False, "message": str(exc)}
            try:
                self.bridge.done.emit(request_key, result)
            except RuntimeError:
                pass
        threading.Thread(target=work, name=f"Companion-{key}", daemon=True).start()
        return request_key

    def _worker_done(self, key: str, result):
        self._critical_requests.discard(key)
        clicked_button = self._pending_button_requests.pop(key, None)
        if clicked_button is not None:
            end_pending(clicked_button, not (isinstance(result, dict) and result.get("success") is False))
        callback = self.callbacks.pop(key, None)
        if callback is not None:
            callback(result)
        if self.exit_after_critical and not self._critical_requests and not self.busy and not (
                getattr(getattr(self, "provider_page", None), "workflow_running", False)):
            self.exit_after_critical = False
            QTimer.singleShot(0, self.request_exit)

    def _note(self, message: str):
        self.note_label.setText(message)

    def _action(self, job, after=None):
        if self.busy or self.quitting:
            self._note("请等待当前操作完成。")
            return
        self.busy = True
        self._note("正在处理，请稍候…")
        self._update_buttons()
        def done(result):
            self.busy = False
            self._note(result.get("message", "操作完成"))
            if not result.get("success"):
                QMessageBox.critical(self, TITLE, result.get("message", "操作失败"))
            if after:
                after(result)
            self.refresh_status()
            self._update_buttons()
        self._async("action", job, done)

    def start_model(self):
        if not self.model_session:
            self._note(self.read_only_reason or "当前为只读模式，请先接管模型控制。")
            return
        if self.busy or getattr(self.provider_page, "workflow_running", False):
            self._note("请等待当前流程结束。")
            return
        selected_model = self.model_combo.currentData()
        preset_id = self.start_preset_combo.currentData()
        if not selected_model:
            self._note("请先选择模型。")
            return
        def work():
            instances = backend.list_model_instances()
            if instances:
                details = ", ".join(f"PID {x['pid']} / 端口 {x['port']}" for x in instances)
                return {"success": False, "message": f"检测到本项目模型已在运行：{details}。请先检查或强制停止指定实例。"}
            return backend.start_model(selected_model, preset_id=preset_id)
        self._action(work)

    def switch_and_start(self):
        if not self.model_session or self.busy or self.quitting or getattr(self.provider_page, "workflow_running", False):
            return
        target = self.model_combo.currentData()
        preset_id = self.start_preset_combo.currentData()
        if not target:
            self._note("请先选择要切换到的模型。")
            return
        current = self.last_status.get("catalog_model_id") or self.last_status.get("active_profile")
        if target == current:
            self._note("当前已是所选模型；如需重新加载，请使用“重启模型”。")
            return
        if QMessageBox.question(self, TITLE,
                "将取消正在生成的回答，停止当前模型，等待端口释放，再启动所选模型。继续吗？") != QMessageBox.StandardButton.Yes:
            return
        self.cancel_chat()
        def work():
            stopped = backend.stop_model()
            if not stopped.get("success"):
                return {"success": False, "message": stopped.get("message", "当前模型停止失败；未启动新模型。")}
            if backend.list_model_instances():
                return {"success": False, "message": "当前模型实例仍在运行；未启动新模型。"}
            return backend.start_model(target, preset_id=preset_id)
        self._action(work)

    def force_stop_model(self):
        if not self.model_session:
            self._note(self.read_only_reason or "当前为只读模式。")
            return
        instances = self.last_status.get("model_instances") or []
        if not instances:
            self._note("没有找到可识别的本项目模型实例。")
            return
        labels = [f"PID {x['pid']} · 端口 {x['port']} · {Path(x['model']).name} · "
                  f"{'管理器内' if x.get('managed') else '外部'}" for x in instances]
        choice, ok = QInputDialog.getItem(self, "选择模型实例", "精确选择要强制停止的进程", labels, 0, False)
        if not ok:
            return
        target = instances[labels.index(choice)]
        confirmation = (f"将强制停止 PID {target['pid']}\n"
                        f"模型：{target['model']}\n端口：{target['port']}\n"
                        f"类型：{'管理器内' if target.get('managed') else '外部'}\n"
                        "请确认目标正确。")
        if QMessageBox.question(self, TITLE, confirmation) != QMessageBox.StandardButton.Yes:
            return
        self.cancel_chat()
        self._action(lambda: backend.force_stop_model(target["pid"], target["created_at"]))

    def stop_model(self):
        if not self.model_session:
            self._note(self.read_only_reason or "当前为只读模式。")
            return
        self.cancel_chat()
        self._action(backend.stop_model)

    def restart_model(self):
        if not self.model_session:
            self._note(self.read_only_reason or "当前为只读模式。")
            return
        self.cancel_chat()
        target = self.last_status.get("catalog_model_id") or self.last_status.get("active_profile") or self.cfg.get("active_profile")
        def work():
            stopped = backend.stop_model()
            return backend.start_model(target) if stopped.get("success") else stopped
        self._action(work)

    def request_exit(self):
        if getattr(self.provider_page, "workflow_running", False):
            self.exit_after_work = True
            self.provider_page.cancel_work()
            self._note("正在等待 DSH 工作流程取消并回滚，然后退出。")
            self.stack.setEnabled(False)
            self.restore_window()
            return
        if self._critical_requests or self.busy:
            self.exit_after_critical = True
            self._note("正在等待当前写入或启动操作结束，然后退出。")
            self.stack.setEnabled(False)
            self.restore_window()
            return
        if self.quitting:
            return
        self.quitting = True
        self.stack.setEnabled(False)
        self.restore_window()
        self.cancel_chat()
        self._note("正在停止生成与模型，确认端口释放后退出…")
        self._update_buttons()
        def work():
            try:
                import hf_downloads
                downloads = hf_downloads.shutdown_downloads(wait=True)
                if isinstance(downloads, dict) and not downloads.get("success", False):
                    return downloads
            except ImportError:
                pass
            if self.dsh:
                cleanup = self.dsh.cleanup()
                if not cleanup.get("success"):
                    return cleanup
            if not self.model_session:
                return {"success": True, "message": "只读窗口已关闭"}
            end = getattr(backend, "end_ui_session", None)
            if end:
                return end()
            return backend.stop_model()
        def done(result):
            if result.get("success"):
                self.poll_timer.stop()
                self.tray.hide()
                self._allow_close = True
                self.close()
                QApplication.instance().quit()
            else:
                self.quitting = False
                self.stack.setEnabled(True)
                remaining = result.get("model_instances") or []
                detail = result.get("message", "退出前停止模型失败")
                if remaining:
                    targets = "; ".join(
                        f"PID {x['pid']} / 端口 {x['port']} / {Path(x['model']).name}"
                        for x in remaining)
                    detail += f"\n仍有本项目模型：{targets}\n请在工作台中选择目标强制停止，然后重新退出。"
                self._note(detail)
                self.nav.setCurrentRow(0)
                self.refresh_status()
                QMessageBox.critical(self, TITLE, detail)
                self._update_buttons()
        self._async("exit", work, done)

    def refresh_status(self):
        if self.status_pending or self.quitting:
            return
        self.status_pending = True
        self._async("status", backend.get_status, self._show_status)

    def _show_status(self, data):
        self.status_pending = False
        if not isinstance(data, dict):
            return
        self.cfg = backend.load_config()
        self.last_status = data
        if data.get("control_read_only"):
            self.read_only_reason = data.get("read_only_reason") or "旧管理器正在控制模型；当前只读。"
        running = bool(data.get("primary_pid"))
        model = data.get("active_model_name") or self.cfg.get("profiles", {}).get(
            self.cfg.get("active_profile"), {}).get("alias", "")
        self.detail_label.setText(f"{model} · 本地模型正在运行" if running
                                  else "尚未加载本地模型；请选择下方模型开始。")
        self._update_overview_summary()
        gpu, ram = data.get("gpu", {}), data.get("ram", {})
        self.metrics_label.setText(
            f"API 端口：{data.get('port', self.cfg.get('default_port'))}     "
            f"GPU：{gpu.get('used_gb', '?')} / {gpu.get('total_gb', '?')} GB     "
            f"内存：{ram.get('used_gb', '?')} / {ram.get('total_gb', '?')} GB\n"
            f"崩溃保护：{'已启用' if data.get('crash_protected') else '未启用'}     "
            f"可识别实例：{len(data.get('model_instances') or [])}")
        if data.get("duplicates_detected"):
            self._note("发现重复模型实例，请先选择并处理，避免端口和显存冲突。")
        if data.get("restart_required"):
            self._note("配置已更改，重启模型后生效。")
        elif not self.model_session:
            self._note(self.read_only_reason or "当前为只读模式；关闭旧管理器后可接管。")
        new_chat_model = model if running else ""
        if self.chat_model and new_chat_model != self.chat_model:
            self.clear_chat()
            self._note("模型已切换，对话历史已清空。")
        self.chat_model = new_chat_model
        self.chat_info.setText(f"当前模型：{model} · 本机端口 {data.get('port', '?')}" if running
                               else "模型未运行。启动模型后即可发送消息。")
        if running:
            effective = data.get("active_effective_params") or {}
            parts = [f"上下文 {data.get('active_ctx_size', '未知')}",
                     f"量化 {data.get('active_ftype', '未知')}"]
            for field, label in (("temp", "温度"), ("top_p", "Top P"),
                                 ("cache_type_k", "K 缓存"), ("cache_type_v", "V 缓存"),
                                 ("reasoning_effort", "思考")):
                if field in effective:
                    parts.append(f"{label} {effective[field]}")
            self.chat_parameters.setText("生效参数：" + " · ".join(parts) +
                                         (" · 其余参数未验证" if len(effective) < 5 else ""))
        else:
            self.chat_parameters.setText("生效参数：模型未运行")
        self._update_urls()
        if hasattr(self, "diagnostic_info"):
            self.diagnostic_info.setText(
                f"模型：{data.get('state_text', '未知')} · 可识别实例 {len(data.get('model_instances') or [])}\n"
                f"DSH：{'运行中' if self.dsh_status.get('running') else '未运行'} · "
                f"{'只读' if data.get('control_read_only') else '可控制'}")
        self._update_buttons()
        self.provider_page.update_model_control()

    def _update_overview_summary(self):
        if not hasattr(self, "quick_dsh_state"):
            return
        state = self.last_status.get("state")
        model_ready = state == "RUNNING" and bool(self.last_status.get("api_online"))
        dsh_running = bool(self.dsh_status.get("running"))
        dsh_ready = dsh_running and bool(self.dsh_status.get("can_open"))
        if not (model_ready and dsh_ready):
            self.workflow_ready = False
        if getattr(getattr(self, "provider_page", None), "workflow_running", False):
            heading = "正在接入 DSH…"
        elif self.workflow_error:
            heading = "接入未完成"
        elif model_ready and dsh_ready and self.workflow_ready:
            heading = "DSH 工作已就绪"
        elif model_ready and dsh_ready:
            heading = "模型与 DSH 均已运行"
        elif model_ready:
            heading = "本地模型已就绪"
        elif state == "LOADING":
            heading = "正在启动模型…"
        elif state == "ERROR":
            heading = "需要处理模型状态"
        else:
            heading = "等待准备"
        self.state_label.setText(heading)
        self.home_model_state.setText(
            "● 运行中 · 本地 API 已就绪" if model_ready else
            "● 正在启动" if state == "LOADING" else
            "● 需要处理" if state == "ERROR" else
            "○ 未运行")
        self.quick_dsh_state.setText(
            "● 已连接 · 可打开" if dsh_ready else
            "● 已运行 · 需原认证链接" if dsh_running else
            "○ 尚未运行")

    def _update_buttons(self):
        ready = bool(self.last_status)
        running = bool(self.last_status.get("primary_pid"))
        instances = self.last_status.get("model_instances") or []
        writable = self.model_session and not self.last_status.get("control_read_only")
        working = bool(getattr(getattr(self, "provider_page", None), "workflow_running", False))
        selected_model = self.model_combo.currentData()
        can_start_selected = bool(selected_model and selected_model != "select_model"
                                  and self.cfg.get("server_executable"))
        selected_is_active = (not running or selected_model in (
            self.last_status.get("catalog_model_id"), self.last_status.get("active_profile")))
        components_ready = bool(self.component_info) and not self.component_info.get("missing")
        dsh_needs_auth = bool(self.dsh_status.get("running")) and not self.dsh_status.get("can_open")
        dsh_ready = bool(self.dsh_status.get("running") and self.dsh_status.get("can_open"))
        one_click = (ready and writable and can_start_selected and selected_is_active
                     and components_ready and not dsh_needs_auth
                     and not self.last_status.get("duplicates_detected")
                     and self.last_status.get("state") not in ("LOADING", "ERROR", "EXTERNAL")
                     and not self.busy and not working and not self.quitting)
        self.start_btn.setEnabled(one_click)
        # An already-running model/DSH is not proof that its local provider and
        # new-session default were registered. Keep the idempotent full flow
        # available so users can attach or repair that last step.
        self.start_btn.setVisible(True)
        self.model_only_start_btn.setEnabled(ready and writable and can_start_selected and not running and not instances and not self.busy and not working and not self.quitting)
        self.switch_btn.setEnabled(ready and writable and can_start_selected and running and not self.busy and not working and not self.quitting)
        self.stop_btn.setEnabled(ready and ((writable and running) or bool(self.dsh_status.get("owned")))
                                 and not self.busy and not working and not self.quitting)
        self.stop_btn.setVisible(running or bool(self.dsh_status.get("owned")))
        self.model_only_stop_btn.setEnabled(ready and writable and running and not self.busy and not working and not self.quitting)
        self.restart_btn.setEnabled(ready and writable and running and not self.busy and not working and not self.quitting)
        self.force_model_btn.setEnabled(ready and writable and bool(instances) and not self.busy and not working and not self.quitting)
        if not writable:
            guidance = self.read_only_reason or "模型由另一管理器控制；请先接管。"
        elif not can_start_selected:
            guidance = "请先选择可用的 GGUF 模型和推理引擎。"
        elif not components_ready:
            guidance = "DSH 或 Node.js 尚未就绪。"
        elif dsh_needs_auth:
            guidance = "现有 DSH 需要原认证链接。"
        elif not selected_is_active:
            guidance = "当前运行的是另一模型，请在独立控制中切换。"
        elif self.last_status.get("duplicates_detected"):
            guidance = "发现重复模型实例，请先核对。"
        elif working or self.busy:
            guidance = self.workflow_stage or "正在准备服务，请等待结果。"
        elif self.workflow_error:
            guidance = self.workflow_error
        elif one_click:
            guidance = "准备就绪，可一键启动。"
        else:
            guidance = "正在读取状态，稍后即可操作。"
        self.prep_hint.setText(guidance)
        self.prep_hint.setVisible(not one_click or bool(self.workflow_error))
        if not self.start_btn.property("busy") == "true":
            self.start_btn.setToolTip(
                "启动或复用所选模型，接入 DSH 并设置后续新会话默认模型。"
                if one_click else guidance)
        self.complete_setup_btn.setVisible(not one_click)
        self.take_control_btn.setVisible(not self.model_session)
        self.take_control_btn.setEnabled(not self.busy and not self.quitting)
        self.send_btn.setText("正在生成…" if self.chat is not None else "发送消息")
        self.send_btn.setToolTip("请等待当前回答完成，或点停止生成。" if self.chat is not None else "")
        self.send_btn.setEnabled(self.last_status.get("state") == "RUNNING" and
                                 self.chat is None and not self.busy and not working and not self.quitting)
        self.cancel_btn.setEnabled(self.chat is not None)
        self.cancel_btn.setText("正在停止…" if self.chat_cancel_pending else "停止生成")
        self._update_dsh_buttons()

    def take_control(self):
        if self.model_session:
            return
        def done(result):
            if result.get("success") and not result.get("control_read_only"):
                self.model_session = True
                self.read_only_reason = ""
                self._note("已接管模型控制。")
            else:
                self.read_only_reason = result.get("read_only_reason") or result.get("message", "仍无法接管模型控制。")
                self._note(self.read_only_reason)
            self.refresh_status()
            self._update_buttons()
        self._async("take_control", backend.begin_ui_session, done)

    def refresh_dsh(self):
        if self.dsh is None or self.quitting or self.dsh_pending:
            return
        self.dsh_pending = True
        def done(data):
            self.dsh_pending = False
            self._show_dsh_status(data)
        self._async("dsh_status", self.dsh.status, done)

    def _show_dsh_status(self, data):
        if not isinstance(data, dict):
            return
        self.dsh_status = data
        if hasattr(self, "provider_page"):
            self.provider_page.update_links(data)
        if data.get("running"):
            owner = "本窗口启动" if data.get("owned") else "外部启动"
            count = len(data.get("instances") or [])
            self.dsh_state_label.setText(
                f"运行中 · {owner} · PID {data.get('pid', '?')} · 端口 {data.get('port', '?')}\n"
                f"可识别实例：{count}" + (" · 发现重复实例，请检查" if count > 1 else ""))
        else:
            self.dsh_state_label.setText(data.get("message") or "DSH 未运行")
        self._update_overview_summary()
        self._update_dsh_buttons()
        self._update_buttons()

    def _update_dsh_buttons(self):
        if not hasattr(self, "dsh_start_btn"):
            return
        active = self.dsh is not None and bool(self.dsh_status) and not self.busy and not self.quitting and not (
            getattr(getattr(self, "provider_page", None), "workflow_running", False))
        running = bool(self.dsh_status.get("running"))
        self.dsh_start_btn.setEnabled(active and not running and
                                      not self.dsh_status.get("instances") and
                                      not self.dsh_status.get("port_occupied") and
                                      not self.dsh_status.get("error"))
        self.dsh_open_btn.setEnabled(active and running and bool(self.dsh_status.get("can_open")))
        self.dsh_open_btn.setToolTip(
            "打开已认证的 DSH 页面。" if running and self.dsh_status.get("can_open") else
            "现有 DSH 尚未认证：请在“DSH 与提供方”中粘贴其原始认证链接。" if running else
            "请先启动或连接 DSH。")
        self.dsh_stop_btn.setEnabled(active and running and bool(self.dsh_status.get("owned")))
        self.dsh_force_btn.setEnabled(active and bool(self.dsh_status.get("instances")))

    def start_dsh(self):
        if not self.dsh:
            return
        def work():
            status = self.dsh.status()
            if status.get("running"):
                return {"success": False, "message": "DSH 已在运行，请先检查现有实例。"}
            return self.dsh.start()
        self._action(work, lambda _: self.refresh_dsh())

    def open_dsh(self):
        if self.dsh:
            self._action(self.dsh.open_web, lambda _: self.refresh_dsh())

    def stop_dsh(self):
        if self.dsh:
            self._action(self.dsh.stop, lambda _: self.refresh_dsh())

    def force_stop_dsh(self):
        if not self.dsh:
            return
        instances = self.dsh_status.get("instances") or []
        if not instances:
            return
        labels = [f"PID {x['pid']} · 端口 {x.get('port', '?')} · "
                  f"{'本窗口' if x.get('owned') else '外部'} · {Path(x['exe']).name}"
                  for x in instances]
        choice, ok = QInputDialog.getItem(self, "选择 DSH 实例", "精确选择要强制停止的进程", labels, 0, False)
        if not ok:
            return
        target = instances[labels.index(choice)]
        detail = (f"将强制停止 DSH PID {target['pid']}\n"
                  f"程序：{target['exe']}\n端口：{target.get('port', '?')}\n"
                  f"类型：{'本窗口' if target.get('owned') else '外部'}\n请确认目标正确。")
        if QMessageBox.question(self, TITLE, detail) != QMessageBox.StandardButton.Yes:
            return
        self._action(lambda: self.dsh.force_stop(target["pid"], target["created_at"]),
                     lambda _: self.refresh_dsh())

    def send_chat(self):
        prompt = self.chat_input.toPlainText().strip()
        if not prompt or self.chat is not None:
            return
        try:
            output_limit = int(self.chat_output_limit.text().strip())
            if not 1 <= output_limit <= 65536:
                raise ValueError()
        except ValueError:
            self.chat_info.setText("单次生成上限必须是 1 到 65536 的整数。")
            return
        if self.busy or self.quitting or self.last_status.get("state") != "RUNNING":
            self.chat_info.setText("模型尚未就绪，请先在首页启动并等待 API 在线。")
            return
        model = self.chat_model
        if not model:
            self.chat_info.setText("没有可用的模型 ID。")
            return
        self.chat_input.clear()
        self._add_bubble("你", prompt, "bubbleUser")
        self.thinking_toggle = QCheckBox("显示思考过程")
        self.thinking_toggle.setVisible(False)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, self.thinking_toggle)
        self.thinking_label = self._add_bubble("思考过程", "", "bubbleThinking")
        self.thinking_frame = self.thinking_label.parentWidget()
        self.thinking_frame.hide()
        self.thinking_toggle.toggled.connect(self.thinking_frame.setVisible)
        self.answer_label = self._add_bubble("模型", "正在生成…", "bubbleAssistant")
        self.answer_text = ""
        self.thinking_text = ""
        messages = []
        system = self.system_prompt.toPlainText().strip()
        if system:
            messages.append({"role": "system", "content": system})
        messages.extend(self.chat_history)
        messages.append({"role": "user", "content": prompt})
        key_cfg = self.cfg.get("api_key", {})
        key = key_cfg.get("key", "") if key_cfg.get("enabled") else ""
        self.chat = ChatStream(self.last_status.get("port", self.cfg["default_port"]),
                               model, key, messages, max_tokens=output_limit)
        self.chat.chunk.connect(self._chat_chunk)
        self.chat.finished.connect(lambda answer, thinking, error, p=prompt:
                                   self._chat_finished(p, answer, thinking, error))
        self.chat.start()
        self.chat_info.setText("正在流式生成；可随时停止。")
        self._update_buttons()

    def _chat_chunk(self, kind: str, value: str):
        if self.quitting:
            return
        bar = self.chat_area.verticalScrollBar()
        follow = bar.value() >= bar.maximum() - 40
        if kind == "thinking":
            self.thinking_text += value
            self.thinking_toggle.setVisible(True)
            self.thinking_label.setText(self.thinking_text)
        else:
            self.answer_text += value
            self.answer_label.setText(self.answer_text)
        if follow:
            QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))

    def _chat_finished(self, prompt: str, answer: str, thinking: str, error: str):
        self.chat = None
        self.chat_cancel_pending = False
        if self.quitting:
            return
        if self.pending_clear:
            self.pending_clear = False
            self._clear_chat_now()
            self._update_buttons()
            return
        if answer:
            self.answer_label.setText(answer)
            if not error:
                self.chat_history.extend(({"role": "user", "content": prompt},
                                          {"role": "assistant", "content": answer}))
                self.chat_info.setText("回答已完成。")
            else:
                self.chat_info.setText(error)
        elif error:
            self.answer_label.setText(error)
        else:
            self.answer_label.setText("模型没有返回内容。")
        QTimer.singleShot(0, lambda: self.chat_area.verticalScrollBar().setValue(
            self.chat_area.verticalScrollBar().maximum()))
        self._update_buttons()

    def cancel_chat(self):
        if self.chat is not None:
            self.chat_cancel_pending = True
            self.chat.cancel()
            self.cancel_btn.setEnabled(False)
            self.cancel_btn.setText("正在停止…")
            self.chat_info.setText("正在停止生成，请稍候…")

    def copy_answer(self):
        if getattr(self, "answer_text", ""):
            self.copy_text(self.answer_text)

    def clear_chat(self):
        if self.chat is not None:
            self.pending_clear = True
            self.cancel_chat()
            return
        self._clear_chat_now()

    def _clear_chat_now(self):
        self.chat_history.clear()
        self.answer_text = ""
        while self.chat_layout.count() > 1:
            item = self.chat_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _refresh_profiles(self):
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for pid, profile in self.cfg.get("profiles", {}).items():
            self.profile_combo.addItem(f"{profile.get('name', pid)}  ·  {pid}", pid)
            if pid == self.selected:
                self.profile_combo.setCurrentIndex(self.profile_combo.count() - 1)
        self.profile_combo.blockSignals(False)
        self._load_profile()

    def _selected_profile(self, index: int):
        if index >= 0:
            self.selected = self.profile_combo.itemData(index)
            self._load_profile()

    def _load_profile(self):
        profile = self.cfg.get("profiles", {}).get(self.selected, {})
        for key, _, _ in FIELDS:
            self.profile_fields[key].setText(str(profile.get(key, "")))
        self.flash_check.setChecked(bool(profile.get("flash_attn", True)))

    def choose_file(self, entry: QLineEdit):
        path, _ = QFileDialog.getOpenFileName(self, "选择文件", str(backend.BASE_DIR))
        if path:
            try:
                path = str(Path(path).resolve().relative_to(backend.BASE_DIR.resolve()))
            except ValueError:
                pass
            entry.setText(path)

    def save_profile(self):
        if self.busy:
            return
        try:
            updated = copy.deepcopy(self.cfg)
            profile = updated["profiles"][self.selected]
            for key, _, cast in FIELDS:
                profile[key] = cast(self.profile_fields[key].text().strip())
            profile["flash_attn"] = self.flash_check.isChecked()
        except (ValueError, KeyError):
            QMessageBox.warning(self, TITLE, "请检查参数格式。")
            return
        if backend.save_config(updated):
            self.cfg = updated
            self._refresh_profiles()
            self._note("参数已保存；运行中的模型需重启后应用。")
        else:
            QMessageBox.critical(self, TITLE, "配置保存失败。")

    def copy_profile(self):
        if self.busy or self.selected not in self.cfg.get("profiles", {}):
            return
        updated = copy.deepcopy(self.cfg)
        pid, n = self.selected + "_copy", 2
        while pid in updated["profiles"]:
            pid, n = f"{self.selected}_copy{n}", n + 1
        profile = copy.deepcopy(updated["profiles"][self.selected])
        profile["id"], profile["name"] = pid, profile["name"] + " (副本)"
        updated["profiles"][pid] = profile
        if backend.save_config(updated):
            self.cfg, self.selected = updated, pid
            self._refresh_profiles()
        else:
            QMessageBox.critical(self, TITLE, "档位复制失败。")

    def delete_profile(self):
        if self.busy or self.selected == self.cfg.get("active_profile"):
            QMessageBox.information(self, TITLE, "当前档位不能删除，请先切换。")
            return
        if QMessageBox.question(self, TITLE, "只删除档位配置，不删除模型文件。继续吗？") != QMessageBox.StandardButton.Yes:
            return
        updated = copy.deepcopy(self.cfg)
        updated["profiles"].pop(self.selected, None)
        if backend.save_config(updated):
            self.cfg = updated
            self.selected = updated["active_profile"]
            self._refresh_profiles()

    def switch_profile(self):
        target = self.selected
        if self.last_status.get("primary_pid") and QMessageBox.question(
                self, TITLE, "切换档位会停止当前模型并启动新模型。继续吗？") != QMessageBox.StandardButton.Yes:
            return
        self.cancel_chat()
        def work():
            return backend.switch_profile(target)
        def after(result):
            if result.get("success"):
                self.cfg = backend.load_config()
                self._refresh_profiles()
                self.clear_chat()
        self._action(work, after)

    def refresh_ips(self):
        self._async("ips", backend.get_network_ips, self._show_ips)

    def _show_ips(self, data):
        current = self.cfg.get("lan_access", {}).get("selected_ip", "")
        ips = data.get("all_ips", []) if isinstance(data, dict) else []
        self.ip_combo.clear()
        self.ip_combo.addItems(ips)
        if current in ips:
            self.ip_combo.setCurrentText(current)

    def toggle_key(self):
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Normal if
                                  self.key_edit.echoMode() == QLineEdit.EchoMode.Password
                                  else QLineEdit.EchoMode.Password)

    def generate_key(self):
        self.key_edit.setText(backend.generate_secure_api_key())
        self.key_check.setChecked(True)

    def copy_text(self, value: str):
        QApplication.clipboard().setText(value)
        self._note("已复制到剪贴板。")

    def save_network(self):
        if self.busy:
            return
        try:
            port = int(self.port_edit.text())
            if not 1 <= port <= 65535:
                raise ValueError()
        except ValueError:
            QMessageBox.warning(self, TITLE, "端口必须是 1 到 65535 的整数。")
            return
        if self.lan_check.isChecked() and not (self.key_check.isChecked() and self.key_edit.text().strip()):
            QMessageBox.warning(self, TITLE, "开启局域网时必须设置 API Key。")
            return
        if self.lan_check.isChecked() and not self.ip_combo.currentText():
            QMessageBox.warning(self, TITLE, "请选择有效网卡 IP。")
            return
        updated = copy.deepcopy(self.cfg)
        updated["default_port"] = port
        updated["server_executable"] = self.server_edit.text().strip()
        updated["lan_access"] = {"enabled": self.lan_check.isChecked(),
                                  "require_api_key": True, "selected_ip": self.ip_combo.currentText()}
        updated["api_key"] = {"enabled": self.key_check.isChecked(), "key": self.key_edit.text().strip()}
        if backend.save_config(updated):
            self.cfg = updated
            self._update_urls()
            self._note("接口设置已保存；运行中的模型需要重启后应用。")
        else:
            QMessageBox.critical(self, TITLE, "配置保存失败。")

    def _update_urls(self):
        if not hasattr(self, "local_url_edit"):
            return
        port = self.last_status.get("port") or self.cfg.get("default_port", 24548)
        lan = self.cfg.get("lan_access", {})
        address = (f"http://{lan.get('selected_ip')}:{port}/v1" if
                   lan.get("enabled") and lan.get("selected_ip") else "")
        self.local_url_edit.setText(f"http://127.0.0.1:{port}/v1")
        self.lan_url_edit.setText(address)
        self.lan_url_edit.setPlaceholderText("未启用")

    def set_startup(self, enabled: bool):
        if self.busy or self.quitting:
            self.startup_check.blockSignals(True)
            self.startup_check.setChecked(autostart.is_enabled())
            self.startup_check.blockSignals(False)
            return
        old_entry = autostart.read_entry_raw()
        try:
            autostart.set_enabled(enabled)
            updated = copy.deepcopy(self.cfg)
            updated["autostart_windows"] = enabled
            if not backend.save_config(updated):
                raise OSError("配置保存失败")
            self.cfg = updated
            self._note("开机启动设置已生效。")
        except Exception as exc:
            rollback_error = None
            try:
                autostart.restore_entry(old_entry)
            except Exception as restore_exc:
                rollback_error = restore_exc
            actual = autostart.is_enabled()
            self.startup_check.blockSignals(True)
            self.startup_check.setChecked(actual)
            self.startup_check.blockSignals(False)
            detail = f"开机启动设置失败：{exc}"
            if rollback_error:
                detail += f"\n启动项恢复也失败：{rollback_error}。当前实际状态：{'开启' if actual else '关闭'}。"
            QMessageBox.critical(self, TITLE, detail)

    def save_start_option(self, key: str, enabled: bool, checkbox: QCheckBox):
        if self.busy or self.quitting:
            checkbox.blockSignals(True)
            checkbox.setChecked(bool(self.cfg.get(key)))
            checkbox.blockSignals(False)
            return
        updated = copy.deepcopy(self.cfg)
        updated[key] = enabled
        if backend.save_config(updated):
            self.cfg = updated
            self._note("启动设置已保存。")
        else:
            checkbox.blockSignals(True)
            checkbox.setChecked(not enabled)
            checkbox.blockSignals(False)
            QMessageBox.critical(self, TITLE, "启动设置保存失败。")

    def _save_close_action_value(self, value: str) -> bool:
        updated = copy.deepcopy(self.cfg)
        updated["close_action"] = value
        if not backend.save_config(updated):
            self._note("关闭方式保存失败；本次选择仍会执行。")
            return False
        self.cfg = updated
        if hasattr(self, "close_action_combo"):
            self.close_action_combo.blockSignals(True)
            self.close_action_combo.setCurrentIndex(self.close_action_combo.findData(value))
            self.close_action_combo.blockSignals(False)
        return True

    def save_close_action(self, _index: int):
        value = self.close_action_combo.currentData()
        if value not in ("ask", "exit", "tray") or self.busy or self.quitting:
            self.close_action_combo.blockSignals(True)
            self.close_action_combo.setCurrentIndex(self.close_action_combo.findData(self.cfg.get("close_action", "ask")))
            self.close_action_combo.blockSignals(False)
            return
        if self._save_close_action_value(value):
            self._note("关闭方式已保存。")

    def refresh_logs(self):
        if hasattr(self, "log_output"):
            self._async("logs", lambda: backend.get_logs(150),
                        lambda lines: self.log_output.setPlainText("\n".join(lines) if isinstance(lines, list) else ""))

    def run_script(self):
        name = self.script_combo.currentData()
        if not name:
            return
        self._async("script", lambda: backend.run_script(name), self._script_started)

    def _script_started(self, result):
        self._note(result.get("message", ""))
        if result.get("success"):
            self._poll_script()

    def _poll_script(self):
        self._async("script_output", backend.get_script_output, self._show_script)

    def _show_script(self, data):
        self.script_output.setPlainText("\n".join(data.get("output", [])))
        if data.get("running"):
            QTimer.singleShot(1000, self._poll_script)


def activate_existing() -> bool:
    socket = QLocalSocket()
    socket.connectToServer(INSTANCE_NAME)
    if not socket.waitForConnected(300):
        return False
    socket.write(b"show")
    socket.waitForBytesWritten(300)
    socket.disconnectFromServer()
    return True


def run_deploy_cli(arguments: list[str]) -> int:
    """Run the packaged deployment entry without opening Qt or the UI owner."""
    import json
    import os

    values: dict[str, str] = {}
    dry_run = False
    index = 0
    while index < len(arguments):
        item = arguments[index]
        if item == "--dry-run" and not dry_run:
            dry_run = True
            index += 1
            continue
        if item not in ("--deploy-config", "--result-json") or item in values or index + 1 >= len(arguments):
            return 2
        values[item] = arguments[index + 1]
        index += 2
    if set(values) != {"--deploy-config", "--result-json"}:
        return 2
    result_path = Path(values["--result-json"])
    if not result_path.is_absolute() or result_path.suffix.lower() != ".json":
        return 2
    try:
        from deploy_config import execute_deployment
        result = execute_deployment(Path(values["--deploy-config"]), dry_run=dry_run)
        if not isinstance(result, dict) or not isinstance(result.get("success"), bool):
            raise ValueError("部署模块返回了无效结果")
    except Exception as exc:
        result = {"success": False, "message": str(exc), "errors": [str(exc)],
                  "dry_run": dry_run}
    try:
        result_path.parent.mkdir(parents=True, exist_ok=True)
        pending = result_path.with_name(result_path.name + f".{os.getpid()}.pending")
        with pending.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, result_path)
    except OSError:
        return 1
    return 0 if result["success"] else 1


def main() -> int:
    multiprocessing.freeze_support()
    if len(sys.argv) > 1:
        if "--deploy-config" in sys.argv[1:]:
            return run_deploy_cli(sys.argv[1:])
        # The old command-line entry is retained for existing scripts.
        if len(sys.argv) != 3 or sys.argv[1] != "--start-profile":
            return 2
        result = backend.start_model(sys.argv[2])
        return 0 if result.get("success") else 1
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(app_icon())
    try:
        from deploy_config import deployment_mutex
        with deployment_mutex():
            if activate_existing():
                return 0
            server = QLocalServer()
            if not server.listen(INSTANCE_NAME):
                if activate_existing():
                    return 0
                QLocalServer.removeServer(INSTANCE_NAME)
                if not server.listen(INSTANCE_NAME):
                    QMessageBox.critical(None, TITLE, "无法建立单实例通道。")
                    return 1
            model_session = True
            read_only_reason = ""
            begin = getattr(backend, "begin_ui_session", None)
            if begin:
                result = begin()
                if result.get("control_read_only") or result.get("read_only"):
                    model_session = False
                    read_only_reason = result.get("read_only_reason") or result.get("message", "旧管理器正在运行，当前只读。")
                elif not result.get("success"):
                    if result.get("read_only"):
                        model_session = False
                        read_only_reason = result.get("message", "旧管理器正在运行，当前只读。")
                    else:
                        QMessageBox.critical(None, TITLE, result.get("message", "无法建立模型管理会话。"))
                        return 1
            window = MainWindow(model_session, read_only_reason)
    except Exception as exc:
        QMessageBox.critical(None, TITLE, f"无法建立安全的管理会话：{exc}")
        return 1
    def incoming():
        while server.hasPendingConnections():
            peer = server.nextPendingConnection()
            peer.readAll()
            window.restore_window()
            QTimer.singleShot(1000, peer.deleteLater)
    server.newConnection.connect(incoming)
    if window.cfg.get("start_minimized_to_tray") and window.tray.isVisible():
        window.hide()
    else:
        window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
