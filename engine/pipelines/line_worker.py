"""Worker de uma linha de áudio isolada."""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from typing import Any

import numpy as np

from engine.models import LineConfig


class LineWorker:
    """Linha isolada: captura → (opcional STT/trad/TTS) → reprodução."""

    def __init__(
        self,
        config: LineConfig,
        mode: str,
        on_caption: Callable[[str, str, str], None] | None = None,
    ) -> None:
        self.config = config
        self.mode = mode
        self.on_caption = on_caption
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._level = 0.0
        self._status = "idle"
        self._error: str | None = None
        self._last_text = ""
        self._last_translation = ""
        self._lock = threading.Lock()
        self._sample_rate = 16000
        self._block = 1024
        self._audio_q: queue.Queue[np.ndarray] = queue.Queue(maxsize=64)

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"alfredo-line-{self.config.label}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._thread = None
        with self._lock:
            self._status = "stopped"

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": True,
                "label": self.config.label,
                "level": round(self._level, 4),
                "status": self._status,
                "error": self._error,
                "last_text": self._last_text,
                "last_translation": self._last_translation,
                "source_lang": self.config.source_lang,
                "target_lang": self.config.target_lang,
            }

    def _set(self, **kwargs: Any) -> None:
        with self._lock:
            for k, v in kwargs.items():
                setattr(self, f"_{k}" if not k.startswith("_") else k, v)

    def _run(self) -> None:
        import sounddevice as sd

        try:
            self._set(status="starting", error=None)
            in_dev = self.config.input_device
            out_dev = self.config.output_device
            info = sd.query_devices(in_dev)
            rate = int(info.get("default_samplerate") or 48000)
            # Prefer 16k for STT in translate mode; passthrough keeps device rate
            if self.mode == "translate":
                self._sample_rate = 16000
            else:
                self._sample_rate = rate

            channels_in = 1
            channels_out = 1

            if self.mode == "passthrough":
                self._run_passthrough(sd, in_dev, out_dev, channels_in, channels_out)
            else:
                self._run_translate(sd, in_dev, out_dev, channels_in, channels_out)
        except Exception as exc:  # noqa: BLE001
            self._set(status="error", error=str(exc))

    def _run_passthrough(
        self,
        sd: Any,
        in_dev: int | None,
        out_dev: int | None,
        channels_in: int,
        channels_out: int,
    ) -> None:
        self._set(status="running")

        def callback(indata, outdata, frames, time_info, status):  # noqa: ANN001
            if status:
                pass
            mono = indata[:, 0].copy() if indata.ndim > 1 else indata.copy()
            rms = float(np.sqrt(np.mean(np.square(mono))) + 1e-12)
            self._level = min(1.0, rms * 8.0)
            # Isolamento: só esta linha escreve no seu outdata
            out = mono.reshape(-1, 1)
            if outdata.shape[1] > 1:
                outdata[:] = np.repeat(out, outdata.shape[1], axis=1)
            else:
                outdata[:] = out

        with sd.Stream(
            device=(in_dev, out_dev),
            samplerate=self._sample_rate,
            blocksize=self._block,
            channels=(channels_in, channels_out),
            dtype="float32",
            callback=callback,
        ):
            while not self._stop.is_set():
                time.sleep(0.05)

        self._set(status="stopped", level=0.0)

    def _run_translate(
        self,
        sd: Any,
        in_dev: int | None,
        out_dev: int | None,
        channels_in: int,
        channels_out: int,
    ) -> None:
        from engine.pipelines.speech import SpeechPipeline

        pipeline = SpeechPipeline(
            source_lang=self.config.source_lang,
            target_lang=self.config.target_lang,
        )
        self._set(status="loading_models")
        pipeline.load()
        self._set(status="running")

        buffer = np.zeros(0, dtype=np.float32)
        # ~2.5s chunks for CPU STT
        chunk_samples = self._sample_rate * 25 // 10

        def callback(indata, frames, time_info, status):  # noqa: ANN001
            if status:
                pass
            mono = indata[:, 0].copy() if indata.ndim > 1 else indata.copy().reshape(-1)
            rms = float(np.sqrt(np.mean(np.square(mono))) + 1e-12)
            self._level = min(1.0, rms * 8.0)
            try:
                self._audio_q.put_nowait(mono)
            except queue.Full:
                pass

        def playback(audio: np.ndarray, rate: int) -> None:
            if audio.size == 0:
                return
            sd.play(audio, samplerate=rate, device=out_dev, blocking=True)

        with sd.InputStream(
            device=in_dev,
            samplerate=self._sample_rate,
            blocksize=self._block,
            channels=channels_in,
            dtype="float32",
            callback=callback,
        ):
            while not self._stop.is_set():
                try:
                    part = self._audio_q.get(timeout=0.2)
                except queue.Empty:
                    continue
                buffer = np.concatenate([buffer, part])
                if buffer.size < chunk_samples:
                    continue
                chunk = buffer[:chunk_samples]
                buffer = buffer[chunk_samples // 4 :]  # overlap leve

                # VAD simples: ignora silêncio
                if float(np.sqrt(np.mean(np.square(chunk)))) < 0.01:
                    continue

                try:
                    text, translated, tts_audio, tts_rate = pipeline.process(chunk, self._sample_rate)
                except Exception as exc:  # noqa: BLE001
                    self._set(error=str(exc))
                    continue

                if text:
                    self._set(last_text=text, last_translation=translated or "")
                    if self.on_caption:
                        self.on_caption(self.config.label, text, translated or "")

                if tts_audio is not None and tts_audio.size > 0:
                    playback(tts_audio, tts_rate)

        self._set(status="stopped", level=0.0)
