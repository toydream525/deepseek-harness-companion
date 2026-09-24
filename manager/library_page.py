"""Model catalog and explicit Hugging Face download controls."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

import hf_downloads
import model_catalog
from button_feedback import connect_button
from app_paths import MODELS


def _button(text, callback, primary=False):
    result = QPushButton(text)
    result.setObjectName("primaryButton" if primary else "secondaryButton")
    return connect_button(result, callback)


def _card(title):
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(12)
    heading = QLabel(title)
    heading.setObjectName("cardTitle")
    layout.addWidget(heading)
    return frame, layout


class LibraryPage(QWidget):
    selected_model_changed = Signal(str)

    def __init__(self, host):
        super().__init__()
        self.host = host
        self.rows = []
        self.remote = None
        self.task_id = None
        self.model_query_revision = 0
        self.remote_revision = 0
        self.task_poll_pending = False
        self.task_timer = QTimer(self)
        self.task_timer.timeout.connect(self.refresh_task)
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
        title = QLabel("模型库与下载")
        title.setObjectName("pageTitle")
        page.addWidget(title)
        subtitle = QLabel("扫描和引用已有 GGUF；下载前明确选择量化与分片组，完成后只入库。")
        subtitle.setObjectName("pageSubtitle")
        page.addWidget(subtitle)

        catalog, cat_layout = _card("本地模型")
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索模型、架构、量化")
        self.search.textChanged.connect(self.refresh_models)
        self.favorites = QCheckBox("只看收藏")
        self.favorites.toggled.connect(self.refresh_models)
        bar.addWidget(self.search, 1)
        bar.addWidget(self.favorites)
        bar.addWidget(_button("重新扫描", self.scan_models))
        bar.addWidget(_button("扫描指定目录", self.scan_directory))
        bar.addWidget(_button("添加已有 GGUF", self.add_model))
        cat_layout.addLayout(bar)
        self.models = QTreeWidget()
        self.models.setHeaderLabels(["模型", "架构", "量化", "大小", "状态", "来源"])
        self.models.setMinimumHeight(225)
        self.models.itemSelectionChanged.connect(self._selection_changed)
        cat_layout.addWidget(self.models)
        action_row = QHBoxLayout()
        action_row.addWidget(_button("收藏 / 取消收藏", self.toggle_favorite))
        action_row.addWidget(_button("重新绑定文件", self.rebind_model))
        action_row.addStretch()
        cat_layout.addLayout(action_row)
        self.model_detail = QLabel("请选择模型查看文件状态。")
        self.model_detail.setWordWrap(True)
        cat_layout.addWidget(self.model_detail)
        page.addWidget(catalog)

        download, dl_layout = _card("从 Hugging Face 下载 GGUF")
        link_row = QHBoxLayout()
        self.hf_link = QLineEdit()
        self.hf_link.setPlaceholderText("粘贴仓库或文件链接，例如 https://huggingface.co/组织/仓库")
        link_row.addWidget(self.hf_link, 1)
        link_row.addWidget(_button("解析链接", self.parse_remote, True))
        link_row.addWidget(_button("打开官网", lambda: QDesktopServices.openUrl(
            QUrl("https://huggingface.co/models?library=gguf"))))
        dl_layout.addLayout(link_row)
        self.group_combo = QComboBox()
        self.group_combo.setMinimumWidth(380)
        self.group_combo.currentIndexChanged.connect(self._show_group)
        dl_layout.addWidget(self.group_combo)
        self.group_detail = QLabel("选择仓库后可核对量化、分片和下载大小。")
        self.group_detail.setWordWrap(True)
        dl_layout.addWidget(self.group_detail)
        token_row = QHBoxLayout()
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("可选：Hugging Face 访问令牌（仅写入 Windows 凭据管理器）")
        token_row.addWidget(self.token, 1)
        token_row.addWidget(_button("保存令牌", self.save_token))
        token_row.addWidget(_button("删除令牌", self.clear_token))
        dl_layout.addLayout(token_row)
        self.token_state = QLabel("令牌状态：读取中")
        dl_layout.addWidget(self.token_state)
        dest_row = QHBoxLayout()
        self.destination = QLineEdit(str(MODELS))
        dest_row.addWidget(QLabel("下载位置"))
        dest_row.addWidget(self.destination, 1)
        dest_row.addWidget(_button("选择目录", self.choose_destination))
        dl_layout.addLayout(dest_row)
        self.start_download_btn = _button("下载所选文件组", self.start_download, True)
        self.start_download_btn.setEnabled(False)
        dl_layout.addWidget(self.start_download_btn)
        task_row = QHBoxLayout()
        self.task_combo = QComboBox()
        self.task_combo.currentIndexChanged.connect(self._task_selected)
        task_row.addWidget(QLabel("下载任务"))
        task_row.addWidget(self.task_combo, 1)
        task_row.addWidget(_button("刷新任务", self.refresh_tasks))
        dl_layout.addLayout(task_row)
        self.download_state = QLabel("尚无下载任务。")
        self.download_state.setWordWrap(True)
        dl_layout.addWidget(self.download_state)
        task_actions = QHBoxLayout()
        self.pause_btn = _button("暂停", self.pause_download)
        self.resume_btn = _button("恢复", self.resume_download)
        self.cancel_btn = _button("取消", self.cancel_download)
        self.retry_btn = _button("重试", self.retry_download)
        for widget in (self.pause_btn, self.resume_btn, self.cancel_btn, self.retry_btn):
            task_actions.addWidget(widget)
        task_actions.addStretch()
        dl_layout.addLayout(task_actions)
        page.addWidget(download)
        page.addStretch()
        QTimer.singleShot(0, self.refresh_models)
        QTimer.singleShot(0, self.refresh_token_state)
        QTimer.singleShot(0, self.refresh_tasks)
        self._update_task_buttons("")

    def _result(self, value, success_message="操作完成"):
        if isinstance(value, dict):
            self.host._note(value.get("message") or success_message)
        else:
            self.host._note(success_message)

    def refresh_models(self):
        query = self.search.text().strip()
        favorites = self.favorites.isChecked()
        self.model_query_revision += 1
        revision = self.model_query_revision
        self.host._async("library_list", lambda: model_catalog.list_models(query, favorites),
                         lambda rows: self._show_models(rows) if revision == self.model_query_revision else None)

    def _show_models(self, rows):
        if not isinstance(rows, list):
            self._result(rows, "模型列表读取失败")
            return
        previous = self.selected_model_id()
        self.rows = rows
        self.models.clear()
        for row in rows:
            size = row.get("size_bytes")
            size_text = f"{size / (1024 ** 3):.2f} GB" if isinstance(size, (int, float)) else "未知"
            state = ("投影文件 / 不可单独启动" if row.get("startable") is False else
                     "完整" if row.get("complete") else "分片缺失 / 文件异常")
            name = ("★ " if row.get("favorite") else "") + str(row.get("name") or row.get("id"))
            item = QTreeWidgetItem([name, str(row.get("architecture") or "未知"),
                                    str(row.get("quantization") or "未知"), size_text,
                                    state, str(row.get("source") or "本地")])
            item.setData(0, Qt.ItemDataRole.UserRole, row.get("id"))
            self.models.addTopLevelItem(item)
            if row.get("id") == previous:
                self.models.setCurrentItem(item)
        self.models.resizeColumnToContents(0)

    def selected_model_id(self):
        item = self.models.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def _selected_row(self):
        model_id = self.selected_model_id()
        return next((row for row in self.rows if row.get("id") == model_id), None)

    def _selection_changed(self):
        row = self._selected_row()
        if row:
            files = row.get("files") or [row.get("path")]
            self.model_detail.setText(
                f"路径：{row.get('path') or '未知'}\n文件：{', '.join(map(str, files))}\n"
                f"元数据：{row.get('metadata_status') or '未知'}；上下文：{row.get('context_length') or '未知'}")
            self.selected_model_changed.emit(str(row["id"]))
        else:
            self.model_detail.setText("请选择模型查看文件状态。")

    def scan_models(self):
        self.host._async("library_scan", model_catalog.scan_models,
                         lambda result: (self._result(result), self.refresh_models()))

    def scan_directory(self):
        path = QFileDialog.getExistingDirectory(self, "扫描 GGUF 目录", str(MODELS))
        if path:
            self.host._async("library_scan_dir", lambda: model_catalog.scan_models([path]),
                             lambda result: (self._result(result), self.refresh_models()))

    def add_model(self):
        path, _ = QFileDialog.getOpenFileName(self, "引用已有 GGUF", str(MODELS), "GGUF 模型 (*.gguf)")
        if path:
            self.host._async("library_add", lambda: model_catalog.add_model(path),
                             lambda result: (self._result(result), self.refresh_models()))

    def toggle_favorite(self):
        row = self._selected_row()
        if row:
            self.host._async("favorite", lambda: model_catalog.set_favorite(
                row["id"], not bool(row.get("favorite"))),
                lambda result: (self._result(result), self.refresh_models()))

    def rebind_model(self):
        row = self._selected_row()
        if not row:
            return
        path, _ = QFileDialog.getOpenFileName(self, "重新绑定模型文件", str(MODELS), "GGUF 模型 (*.gguf)")
        if path:
            self.host._async("library_rebind", lambda: model_catalog.rebind_model(row["id"], path),
                             lambda result: (self._result(result), self.refresh_models()))

    def parse_remote(self):
        url = self.hf_link.text().strip()
        if url:
            self.remote_revision += 1
            revision = self.remote_revision
            self.remote = None
            self.group_combo.clear()
            self.start_download_btn.setEnabled(False)
            self.group_detail.setText("正在读取仓库文件列表…")
            self.host._async("hf_parse", lambda: hf_downloads.list_remote_gguf(url),
                             lambda result: self._show_remote(result) if revision == self.remote_revision else None)

    def _show_remote(self, result):
        self.remote = result if isinstance(result, dict) and result.get("success") else None
        self.group_combo.clear()
        if self.remote:
            for group in self.remote.get("groups") or []:
                size = group.get("size_bytes") or 0
                label = f"{group.get('id')} · {size / 1024 ** 3:.2f} GB · {len(group.get('files') or [])} 文件"
                self.group_combo.addItem(label, group)
            self._show_group()
            self.start_download_btn.setEnabled(self.group_combo.count() > 0)
        else:
            self.start_download_btn.setEnabled(False)
            self.group_detail.setText((result or {}).get("message", "链接解析失败。"))

    def _show_group(self):
        group = self.group_combo.currentData()
        if isinstance(group, dict):
            files = [str(x.get("path")) for x in group.get("files") or []]
            self.group_detail.setText("将下载：\n" + "\n".join(files) +
                                      "\n下载完成后只入库，不自动启动模型。")

    def refresh_token_state(self):
        self.host._async("hf_token_state", hf_downloads.has_token,
                         self._show_token_state)

    def _show_token_state(self, present):
        if type(present) is bool:
            self.token_state.setText("令牌状态：已保存" if present else "令牌状态：未设置")
        else:
            self.token_state.setText("令牌状态：读取失败")
            self._result(present, "令牌状态读取失败")

    def save_token(self):
        secret = self.token.text().strip()
        self.token.clear()
        if secret:
            self.host._async("hf_token_save", lambda: hf_downloads.set_token(secret),
                             lambda result: (self._result(result), self.refresh_token_state()))

    def clear_token(self):
        self.host._async("hf_token_clear", hf_downloads.clear_token,
                         lambda result: (self._result(result), self.refresh_token_state()))

    def choose_destination(self):
        path = QFileDialog.getExistingDirectory(self, "选择下载目录", self.destination.text())
        if path:
            self.destination.setText(path)

    def start_download(self):
        group = self.group_combo.currentData()
        if not self.remote or not isinstance(group, dict):
            self.download_state.setText("请先解析链接并选择文件组。")
            return
        selection = {"repo_id": self.remote["repo_id"], "commit_sha": self.remote["commit_sha"],
                     "group_id": group["id"], "files": group.get("files") or []}
        destination = self.destination.text().strip() or str(MODELS)
        self.host._async("hf_start", lambda: hf_downloads.start_download(selection, destination),
                         self._download_started)

    def _download_started(self, result):
        self._result(result)
        if isinstance(result, dict) and result.get("success"):
            self.task_id = result.get("task_id")
            self.task_timer.start(1000)
            self.refresh_tasks()

    def refresh_tasks(self):
        self.host._async("hf_tasks", hf_downloads.list_download_tasks, self._show_tasks)

    def _show_tasks(self, tasks):
        if isinstance(tasks, dict) and not tasks.get("success", True):
            self._result(tasks, "任务列表读取失败")
            return
        rows = tasks.get("tasks") if isinstance(tasks, dict) else tasks
        if not isinstance(rows, list):
            return
        current = self.task_id
        self.task_combo.blockSignals(True)
        self.task_combo.clear()
        for row in rows:
            task_id = row.get("id")
            self.task_combo.addItem(f"{task_id} · {row.get('status')}", task_id)
        index = self.task_combo.findData(current)
        if index >= 0:
            self.task_combo.setCurrentIndex(index)
        self.task_combo.blockSignals(False)
        self._task_selected()

    def _task_selected(self):
        self.task_id = self.task_combo.currentData()
        if self.task_id:
            self.refresh_task()
        else:
            self._update_task_buttons("")

    def refresh_task(self):
        if self.task_id and not self.task_poll_pending:
            self.task_poll_pending = True
            selected = self.task_id
            def done(result):
                self.task_poll_pending = False
                if selected == self.task_id:
                    self._show_task(result)
            self.host._async("hf_task", lambda: hf_downloads.get_download_task(selected), done)

    def _show_task(self, task):
        if not isinstance(task, dict):
            return
        if task.get("success") is False or not task.get("status"):
            self.host._note(task.get("message") or "下载操作失败")
            return
        total = task.get("total_bytes") or 0
        done = task.get("progress_bytes") or 0
        speed = task.get("speed_bps") or 0
        percent = f"{done / total:.1%}" if total else "未知"
        self.download_state.setText(
            f"任务 {task.get('id')} · {task.get('status')} · {percent} · "
            f"{done / 1024 ** 2:.1f}/{total / 1024 ** 2:.1f} MB · {speed / 1024 ** 2:.2f} MB/s"
            + (f"\n{task['error']}" if task.get("error") else ""))
        self._update_task_buttons(str(task.get("status")))
        if task.get("status") in ("completed", "canceled", "failed"):
            self.task_timer.stop()
            if task.get("status") == "completed":
                self.refresh_models()
        elif task.get("status") in ("running", "downloading", "queued", "pausing", "canceling"):
            self.task_timer.start(1000)

    def _update_task_buttons(self, status):
        self.pause_btn.setEnabled(status in ("running", "downloading"))
        self.resume_btn.setEnabled(status == "paused")
        self.cancel_btn.setEnabled(status in ("running", "downloading", "queued", "paused", "pausing"))
        self.retry_btn.setEnabled(status in ("failed", "canceled"))

    def _task_action(self, method):
        if self.task_id:
            selected = self.task_id
            def done(result):
                if selected != self.task_id:
                    return
                if not isinstance(result, dict) or not result.get("success"):
                    self.host._note((result or {}).get("message", "下载操作失败"))
                    self.refresh_task()
                    return
                task = result.get("task")
                if isinstance(task, dict):
                    self._show_task(task)
                else:
                    self.refresh_task()
            self.host._async("hf_action", lambda: method(selected), done)

    def pause_download(self):
        self._task_action(hf_downloads.pause_download)

    def resume_download(self):
        self._task_action(hf_downloads.resume_download)
        self.task_timer.start(1000)

    def cancel_download(self):
        self._task_action(hf_downloads.cancel_download)

    def retry_download(self):
        self._task_action(hf_downloads.retry_download)
        self.task_timer.start(1000)
