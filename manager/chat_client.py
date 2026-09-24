"""Cancelable Qt streaming client for the local OpenAI-compatible API."""

from __future__ import annotations

import json

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkProxy, QNetworkReply, QNetworkRequest


class ChatStream(QObject):
    chunk = Signal(str, str)
    finished = Signal(str, str, str)

    def __init__(self, port: int, model: str, api_key: str, messages: list[dict],
                 max_tokens: int = 8192):
        super().__init__()
        self.port = int(port)
        self.model = model
        self.api_key = api_key
        self.messages = [dict(item) for item in messages]
        self.max_tokens = int(max_tokens)
        self.manager = QNetworkAccessManager(self)
        self.manager.setProxy(QNetworkProxy(QNetworkProxy.ProxyType.NoProxy))
        self.reply: QNetworkReply | None = None
        self.buffer = b""
        self.answer = ""
        self.thinking = ""
        self.error = ""
        self.cancelled = False
        self.done = False
        self.saw_done = False
        self.saw_finish_reason = False
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(self._timed_out)

    def start(self) -> None:
        body = json.dumps({"model": self.model, "messages": self.messages,
                           "stream": True, "max_tokens": self.max_tokens},
                          ensure_ascii=False).encode("utf-8")
        request = QNetworkRequest(QUrl(f"http://127.0.0.1:{self.port}/v1/chat/completions"))
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        request.setRawHeader(b"Accept", b"text/event-stream")
        if self.api_key:
            request.setRawHeader(b"Authorization", b"Bearer " + self.api_key.encode("utf-8"))
        self.reply = self.manager.post(request, body)
        self.reply.readyRead.connect(self._read)
        self.reply.finished.connect(self._finish)
        self.timeout.start(90000)

    def cancel(self) -> None:
        if self.done:
            return
        self.cancelled = True
        if self.reply is not None:
            self.reply.abort()
        else:
            self._finish()

    def _timed_out(self) -> None:
        self.error = "模型响应超时。"
        if self.reply is not None:
            self.reply.abort()
        else:
            self._finish()

    def _read(self) -> None:
        if self.done or self.reply is None:
            return
        self.timeout.start(90000)
        self.buffer += bytes(self.reply.readAll())
        self.buffer = self.buffer.replace(b"\r\n", b"\n")
        while b"\n\n" in self.buffer:
            block, self.buffer = self.buffer.split(b"\n\n", 1)
            self._event(block)

    def _event(self, block: bytes) -> None:
        lines = []
        for line in block.split(b"\n"):
            if line.startswith(b"data:"):
                lines.append(line[5:].lstrip(b" "))
        if not lines:
            return
        payload = b"\n".join(lines).decode("utf-8", errors="replace")
        if payload == "[DONE]":
            self.saw_done = True
            return
        try:
            event = json.loads(payload)
            if not isinstance(event, dict):
                raise ValueError("invalid SSE event")
            if event.get("error"):
                self.error = str(event["error"])
                return
            choice = (event.get("choices") or [{}])[0]
            if not isinstance(choice, dict):
                raise ValueError("invalid SSE choice")
            delta = choice.get("delta") or {}
            if not isinstance(delta, dict):
                raise ValueError("invalid SSE delta")
            if choice.get("finish_reason") is not None:
                self.saw_finish_reason = True
        except (ValueError, TypeError, KeyError, IndexError):
            self.error = "模型返回了无效的流式数据。"
            return
        reason = delta.get("reasoning_content") or ""
        answer = delta.get("content") or ""
        if isinstance(reason, str) and reason:
            self.thinking += reason
            self.chunk.emit("thinking", reason)
        if isinstance(answer, str) and answer:
            self.answer += answer
            self.chunk.emit("answer", answer)

    def _finish(self) -> None:
        if self.done:
            return
        self.done = True
        self.timeout.stop()
        if self.reply is not None:
            self._read_remaining()
            if not self.cancelled and not self.error and self.reply.error() != QNetworkReply.NetworkError.NoError:
                self.error = self.reply.errorString()
            if not self.cancelled and not self.error and not (self.saw_done or self.saw_finish_reason):
                self.error = "模型输出意外中断。"
            self.reply.deleteLater()
            self.reply = None
        self.finished.emit(self.answer, self.thinking,
                           "已停止生成" if self.cancelled else self.error)

    def _read_remaining(self) -> None:
        if self.reply is None or self.cancelled:
            return
        self.buffer += bytes(self.reply.readAll())
        self.buffer = self.buffer.replace(b"\r\n", b"\n")
        if self.buffer:
            for block in self.buffer.split(b"\n\n"):
                if block.strip():
                    self._event(block)
            self.buffer = b""
