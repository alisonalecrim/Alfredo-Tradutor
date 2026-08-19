"""Worker de uma linha de áudio isolada, otimizado para baixa latência."""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

import numpy as np

from engine.models import LineConfig


class LineWorker:
    """Linha isolada: captura → STT/tradução/TTS → reprodução.

    No modo translate, captura, processamento e playback trabalham de forma
    desacoplada para impedir que TTS ou reprodução criem backlog crescente.
    """

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
        self._latency_ms = 0
        self._lock = threading.Lock()
        self._sample_rate = 16000
        self._block = 512

        # Filas curtas por design: em conversa ao vivo é melhor descartar áudio
        # velho do que acumular vários segundos de atraso.
        self._audio_q: queue.Queue[np.ndarray] = queue.Queue(maxsize=24)
        self._segment_q: queue.Queue[np.ndarray] = queue.Queue(maxsize=2)
        self._playback_q: queue.Queue[tuple[np.ndarray, int]] = queue.Queue(maxsize=3)

    def start(self) -> None:
        self._stop.clear()
        self._drain_queue(self._audio_q)
        self._drain_queue(self._segment_q)
        self._drain_queue(self._playback_q)
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
                "latency_ms": self._latency_ms,
                "source_lang": self.config.source_lang,
                "target_lang": self.config.target_lang,
            }

    def _set(self, **kwargs: Any) -> None:
        with self._lock:
            for k, v in kwargs.items():
                setattr(self, f"_{k}" if not k.startswith("_") else k, v)

    @staticmethod
    def _drain_queue(q: queue.Queue[Any]) -> None:
        while True:
            try:
                q.get_nowait()
            except queue.Empty:
                return

    @staticmethod
    def _put_latest(q: queue.Queue[Any], item: Any) -> None:
        """Insere preservando os dados mais recentes quando a fila enche."""
        try:
            q.put_nowait(item)
            return
        except queue.Full:
            pass
        try:
            q.get_nowait()
        except queue.Empty:
            pass
        try:
            q.put_nowait(item)
        except queue.Full:
            pass

    def _run(self) -> None:
        import sounddevice as sd

        try:
            self._set(status="starting", error=None)
            in_dev = self.config.input_device
            out_dev = self.config.output_device
            info = sd.query_devices(in_dev)
            rate = int(info.get("default_samplerate") or 48000)
            self._sample_rate = 16000 if self.mode == "translate" else rate

            if self.mode == "passthrough":
                self._run_passthrough(sd, in_dev, out_dev)
            else:
                self._run_translate(sd, in_dev, out_dev)
        except Exception as exc:  # noqa: BLE001
            self._set(status="error", error=str(exc))

    def _run_passthrough(self, sd: Any, in_dev: int | None, out_dev: int | None) -> None:
        self._set(status="running")

        def callback(indata, outdata, frames, time_info, status):  # noqa: ANN001
            mono = indata[:, 0].copy() if indata.ndim > 1 else indata.copy()
            rms = float(np.sqrt(np.mean(np.square(mono))) + 1e-12)
            self._level = min(1.0, rms * 8.0)
            out = mono.reshape(-1, 1)
            if outdata.shape[1] > 1:
                outdata[:] = np.repeat(out, outdata.shape[1], axis=1)
            else:
                outdata[:] = out

        with sd.Stream(
            device=(in_dev, out_dev),
            samplerate=self._sample_rate,
            blocksize=self._block,
            channels=(1, 1),
            dtype="float32",
            callback=callback,
        ):
            while not self._stop.is_set():
                time.sleep(0.05)

        self._set(status="stopped", level=0.0)

    def _run_translate(self, sd: Any, in_dev: int | None, out_dev: int | None) -> None:
        from engine.pipelines.speech import SpeechPipeline

        pipeline = SpeechPipeline(
            source_lang=self.config.source_lang,
            target_lang=self.config.target_lang,
        )
        self._set(status="loading_models")
        pipeline.load()
        self._set(status="running")

        processor = threading.Thread(
            target=self._processor_loop,
            args=(pipeline,),
            name=f"alfredo-stt-{self.config.label}",
            daemon=True,
        )
        player = threading.Thread(
            target=self._playback_loop,
            args=(sd, out_dev),
            name=f"alfredo-playback-{self.config.label}",
            daemon=True,
        )
        processor.start()
        player.start()

        # Endpointing leve para reduzir o antigo chunk fixo de 2,5 s.
        pre_roll = deque(maxlen=4)
        utterance: list[np.ndarray] = []
        active = False
        speech_samples = 0
        silence_samples = 0
        total_samples = 0
        noise_floor = 0.003

        min_speech = int(self._sample_rate * 0.30)
        end_silence = int(self._sample_rate * 0.28)
        max_utterance = int(self._sample_rate * 1.60)

        def callback(indata, frames, time_info, status):  # noqa: ANN001
            mono = indata[:, 0].copy() if indata.ndim > 1 else indata.copy().reshape(-1)
            rms = float(np.sqrt(np.mean(np.square(mono))) + 1e-12)
            self._level = min(1.0, rms * 8.0)
            self._put_latest(self._audio_q, mono)

        with sd.InputStream(
            device=in_dev,
            samplerate=self._sample_rate,
            blocksize=self._block,
            channels=1,
            dtype="float32",
            callback=callback,
        ):
            while not self._stop.is_set():
                try:
                    part = self._audio_q.get(timeout=0.15)
                except queue.Empty:
                    continue

                rms = float(np.sqrt(np.mean(np.square(part))) + 1e-12)
                threshold = max(0.006, noise_floor * 2.8)
                is_speech = rms >= threshold

                if not active:
                    noise_floor = (noise_floor * 0.97) + (min(rms, 0.02) * 0.03)
                    pre_roll.append(part)
                    if not is_speech:
                        continue
                    active = True
                    utterance = list(pre_roll)
                    speech_samples = len(part)
                    silence_samples = 0
                    total_samples = sum(len(x) for x in utterance)
                    pre_roll.clear()
                    continue

                utterance.append(part)
                total_samples += len(part)
                if is_speech:
                    speech_samples += len(part)
                    silence_samples = 0
                else:
                    silence_samples += len(part)

                ended = silence_samples >= end_silence and speech_samples >= min_speech
                forced = total_samples >= max_utterance
                if not ended and not forced:
                    continue

                segment = np.concatenate(utterance)
                if ended and silence_samples > 0 and segment.size > silence_samples:
                    # Mantém apenas ~60 ms de cauda para não mandar silêncio ao STT.
                    keep_tail = int(self._sample_rate * 0.06)
                    trim = max(0, silence_samples - keep_tail)
                    if trim:
                        segment = segment[:-trim]

                if segment.size >= min_speech:
                    self._put_latest(self._segment_q, segment)

                utterance = []
                speech_samples = 0
                silence_samples = 0
                total_samples = 0
                active = forced and is_speech

        self._stop.set()
        processor.join(timeout=2.0)
        player.join(timeout=2.0)
        self._set(status="stopped", level=0.0)

    def _processor_loop(self, pipeline: Any) -> None:
        while not self._stop.is_set():
            try:
                segment = self._segment_q.get(timeout=0.15)
            except queue.Empty:
                continue

            started = time.perf_counter()
            try:
                text, translated, tts_audio, tts_rate = pipeline.process(
                    segment, self._sample_rate
                )
            except Exception as exc:  # noqa: BLE001
                self._set(error=str(exc))
                continue

            latency_ms = int((time.perf_counter() - started) * 1000)
            self._set(latency_ms=latency_ms)

            if text:
                self._set(last_text=text, last_translation=translated or "")
                if self.on_caption:
                    self.on_caption(self.config.label, text, translated or "")

            if tts_audio is not None and tts_audio.size > 0:
                self._put_latest(self._playback_q, (tts_audio, tts_rate))

    def _playback_loop(self, sd: Any, out_dev: int | None) -> None:
        while not self._stop.is_set():
            try:
                audio, rate = self._playback_q.get(timeout=0.15)
            except queue.Empty:
                continue
            if audio.size == 0:
                continue
            try:
                with sd.OutputStream(
                    device=out_dev,
                    samplerate=rate,
                    channels=1,
                    dtype="float32",
                    blocksize=0,
                ) as stream:
                    stream.write(audio.astype(np.float32, copy=False).reshape(-1, 1))
            except Exception as exc:  # noqa: BLE001
                self._set(error=f"Playback: {exc}")
