"""Gerenciador de sessão com duas linhas isoladas (A e B)."""

from __future__ import annotations

import threading
from typing import Any

from engine.models import LineConfig, SessionConfig
from engine.pipelines.line_worker import LineWorker

__all__ = ["LineConfig", "SessionConfig", "SessionManager"]


class SessionManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running = False
        self._mode: str | None = None
        self._error: str | None = None
        self._captions: list[dict[str, str]] = []
        self._workers: dict[str, LineWorker] = {}

    def start(self, cfg: SessionConfig) -> None:
        with self._lock:
            if self._running:
                self._stop_unlocked()

            self._error = None
            self._captions = []
            self._mode = cfg.mode
            self._workers = {}

            for line_cfg in (cfg.line_a, cfg.line_b):
                if not line_cfg.enabled:
                    continue
                if line_cfg.input_device is None or line_cfg.output_device is None:
                    raise ValueError(
                        f"Linha {line_cfg.label}: selecione entrada e saída de áudio"
                    )
                worker = LineWorker(
                    config=line_cfg,
                    mode=cfg.mode,
                    on_caption=self._append_caption,
                )
                worker.start()
                self._workers[line_cfg.label] = worker

            if not self._workers:
                raise ValueError("Ative pelo menos uma linha (A ou B)")

            self._running = True

    def stop(self) -> None:
        with self._lock:
            self._stop_unlocked()

    def _stop_unlocked(self) -> None:
        for worker in self._workers.values():
            worker.stop()
        self._workers = {}
        self._running = False
        self._mode = None

    def _append_caption(self, line: str, source: str, translation: str) -> None:
        with self._lock:
            self._captions.append(
                {"line": line, "source": source, "translation": translation}
            )
            self._captions = self._captions[-40:]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            line_a = self._workers.get("A").snapshot() if "A" in self._workers else {}
            line_b = self._workers.get("B").snapshot() if "B" in self._workers else {}
            return {
                "running": self._running,
                "mode": self._mode,
                "line_a": line_a,
                "line_b": line_b,
                "captions": list(self._captions),
                "error": self._error,
            }
