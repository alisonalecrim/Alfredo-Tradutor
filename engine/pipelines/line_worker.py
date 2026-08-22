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
    """Linha isolada: captura → STT/tradução/TTS → reprodução."""

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
        self._warning: str | None = None
        self._last_text = ""
        self._last_translation = ""
        self._latency_ms = 0
        self._stt_ms = 0
        self._translation_ms = 0
        self._tts_ms = 0
        self._confidence: float | None = None
        self._dropped_audio_blocks = 0
        self._dropped_segments = 0
        self._dropped_playbacks = 0
        self._lock = threading.Lock()
        self._sample_rate = 16000
        self._block = 512

        self._audio_q: queue.Queue[np.ndarray] = queue.Queue(maxsize=24)
        self._segment_q: queue.Queue[np.ndarray] = queue.Queue(maxsize=3)
        self._playback_q: queue.Queue[tuple[np.ndarray, int]] = queue.Queue(maxsize=3)

    def start(self) -> None:
        self._stop.clear()
        self._drain_queue(self._audio_q)
        self._drain_queue(self._segment_q)
        self._drain_queue(self._playback_q)
        self._set(
            error=None,
            warning=None,
            dropped_audio_blocks=0,
            dropped_segments=0,
            dropped_playbacks=0,
        )
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
        self._set(status="stopped")

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": True,
                "label": self.config.label,
                "level": round(self._level, 4),
                "status": self._status,
                "error": self._error,
                "warning": self._warning,
                "last_text": self._last_text,
                "last_translation": self._last_translation,
                "latency_ms": self._latency_ms,
                "stt_ms": self._stt_ms,
                "translation_ms": self._translation_ms,
                "tts_ms": self._tts_ms,
                "confidence": self._confidence,
                "dropped_audio_blocks": self._dropped_audio_blocks,
                "dropped_segments": self._dropped_segments,
                "dropped_playbacks": self._dropped_playbacks,
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

    def _put_latest(self, q: queue.Queue[Any], item: Any, kind: str) -> bool:
        """Mantém tempo real; sinaliza quando precisou descartar dado antigo."""
        try:
            q.put_nowait(item)
            return False
        except queue.Full:
            pass

        try:
            q.get_nowait()
        except queue.Empty:
            pass

        try:
            q.put_nowait(item)
        except queue.Full:
            return True

        counter = {
            "audio": "dropped_audio_blocks",
            "segment": "dropped_segments",
            "playback": "dropped_playbacks",
        }.get(kind)
        if counter:
            current = getattr(self, f"_{counter}")
            self._set(**{counter: current + 1})
        if kind == "segment":
            self._set(warning="Processamento atrasado: um trecho de fala foi descartado")
        return True

    def _run(self) -> None:
        import sounddevice as sd
        from engine.audio.devices import is_windows_loopback_device

        try:
            self._set(status="starting", error=None)
            in_dev = self.config.input_device
            out_dev = self.config.output_device

            # Captura sempre na taxa nativa do device. Forçar 16 kHz no
            # PortAudio/WASAPI do Windows causa PaErrorCode -9997.
            # O Whisper recebe áudio reamostrado no processador.
            if is_windows_loopback_device(in_dev):
                self._sample_rate = 48000
            else:
                info = sd.query_devices(in_dev)
                self._sample_rate = int(info.get("default_samplerate") or 48000)

            if self.mode == "passthrough":
                if is_windows_loopback_device(in_dev):
                    self._run_windows_loopback_passthrough(sd, int(in_dev), out_dev)
                else:
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
            outdata[:] = np.repeat(out, outdata.shape[1], axis=1) if outdata.shape[1] > 1 else out

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

    @staticmethod
    def _ensure_windows_com() -> None:
        """SoundCard/WASAPI exige COM inicializado na thread atual."""
        import sys

        if sys.platform != "win32":
            return
        import ctypes

        # COINIT_MULTITHREADED — workers daemon não usam STA de UI.
        ctypes.windll.ole32.CoInitializeEx(None, 0x0)

    def _run_windows_loopback_passthrough(
        self, sd: Any, in_dev: int, out_dev: int | None
    ) -> None:
        from engine.audio.devices import get_windows_loopback_microphone

        self._ensure_windows_com()
        mic = get_windows_loopback_microphone(in_dev)
        self._set(status="running")
        with mic.recorder(samplerate=self._sample_rate, blocksize=2048) as recorder:
            with sd.OutputStream(
                device=out_dev,
                samplerate=self._sample_rate,
                channels=1,
                dtype="float32",
                blocksize=0,
            ) as output:
                while not self._stop.is_set():
                    data = recorder.record(numframes=self._block)
                    mono = self._to_mono(data)
                    if mono.size == 0:
                        continue
                    rms = float(np.sqrt(np.mean(np.square(mono))) + 1e-12)
                    self._level = min(1.0, rms * 8.0)
                    output.write(mono.reshape(-1, 1))
        self._set(status="stopped", level=0.0)

    @staticmethod
    def _to_mono(data: Any) -> np.ndarray:
        arr = np.asarray(data, dtype=np.float32)
        if arr.size == 0:
            return np.zeros(0, dtype=np.float32)
        if arr.ndim == 1:
            return arr.reshape(-1)
        # No WASAPI não pedimos canal único: algumas versões do backend têm
        # problemas com captura mono. Fazemos o downmix depois da leitura.
        return arr.mean(axis=1).astype(np.float32, copy=False)

    @staticmethod
    def _resample(audio: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
        if src_rate == dst_rate or audio.size == 0:
            return audio.astype(np.float32, copy=False)
        duration = audio.shape[0] / float(src_rate)
        new_len = max(1, int(round(duration * dst_rate)))
        x_old = np.linspace(0.0, 1.0, num=audio.shape[0], endpoint=False)
        x_new = np.linspace(0.0, 1.0, num=new_len, endpoint=False)
        return np.interp(x_new, x_old, audio.astype(np.float64)).astype(np.float32)

    def _windows_loopback_capture(self, in_dev: int) -> None:
        from engine.audio.devices import get_windows_loopback_microphone

        self._ensure_windows_com()
        mic = get_windows_loopback_microphone(in_dev)
        try:
            with mic.recorder(samplerate=self._sample_rate, blocksize=2048) as recorder:
                while not self._stop.is_set():
                    data = recorder.record(numframes=self._block)
                    mono = self._to_mono(data)
                    if mono.size == 0:
                        continue
                    rms = float(np.sqrt(np.mean(np.square(mono))) + 1e-12)
                    self._level = min(1.0, rms * 8.0)
                    self._put_latest(self._audio_q, mono, "audio")
        except Exception as exc:  # noqa: BLE001
            self._set(error=f"WASAPI loopback: {exc}")
            self._stop.set()

    def _run_translate(self, sd: Any, in_dev: int | None, out_dev: int | None) -> None:
        from engine.audio.devices import is_windows_loopback_device
        from engine.pipelines.speech import SpeechPipeline

        pipeline = SpeechPipeline(self.config.source_lang, self.config.target_lang)
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

        if is_windows_loopback_device(in_dev):
            capture = threading.Thread(
                target=self._windows_loopback_capture,
                args=(int(in_dev),),
                name=f"alfredo-wasapi-{self.config.label}",
                daemon=True,
            )
            capture.start()
            self._endpoint_loop()
            capture.join(timeout=2.0)
        else:
            def callback(indata, frames, time_info, status):  # noqa: ANN001
                mono = indata[:, 0].copy() if indata.ndim > 1 else indata.copy().reshape(-1)
                rms = float(np.sqrt(np.mean(np.square(mono))) + 1e-12)
                self._level = min(1.0, rms * 8.0)
                self._put_latest(self._audio_q, mono, "audio")

            with sd.InputStream(
                device=in_dev,
                samplerate=self._sample_rate,
                blocksize=self._block,
                channels=1,
                dtype="float32",
                callback=callback,
            ):
                self._endpoint_loop()

        self._stop.set()
        processor.join(timeout=2.0)
        player.join(timeout=2.0)
        self._set(status="stopped", level=0.0)

    def _endpoint_loop(self) -> None:
        pre_roll = deque(maxlen=4)
        utterance: list[np.ndarray] = []
        active = False
        speech_samples = 0
        silence_samples = 0
        total_samples = 0
        noise_floor = 0.003

        min_speech = int(self._sample_rate * 0.28)
        end_silence = int(self._sample_rate * 0.32)
        max_utterance = int(self._sample_rate * 2.80)

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
                keep_tail = int(self._sample_rate * 0.06)
                trim = max(0, silence_samples - keep_tail)
                if trim:
                    segment = segment[:-trim]

            if segment.size >= min_speech:
                self._put_latest(self._segment_q, segment, "segment")

            utterance = []
            speech_samples = 0
            silence_samples = 0
            total_samples = 0
            active = forced and is_speech

    def _processor_loop(self, pipeline: Any) -> None:
        from engine.pipelines.speech import TranslationError

        while not self._stop.is_set():
            try:
                segment = self._segment_q.get(timeout=0.15)
            except queue.Empty:
                continue

            try:
                # Whisper espera 16 kHz; a captura pode ser 44.1/48 kHz.
                whisper_audio = self._resample(segment, self._sample_rate, 16000)
                result = pipeline.process(whisper_audio, 16000)
            except TranslationError as exc:
                self._set(error=str(exc), last_translation="")
                continue
            except Exception as exc:  # noqa: BLE001
                self._set(error=f"Falha no processamento de fala: {exc}")
                continue

            self._set(
                error=None,
                latency_ms=result.total_ms,
                stt_ms=result.stt_ms,
                translation_ms=result.translation_ms,
                tts_ms=result.tts_ms,
                confidence=result.confidence,
            )

            if result.text:
                self._set(last_text=result.text, last_translation=result.translated)
                if self.on_caption:
                    self.on_caption(self.config.label, result.text, result.translated)

            if result.tts_audio is not None and result.tts_audio.size > 0:
                self._put_latest(
                    self._playback_q,
                    (result.tts_audio, result.tts_rate),
                    "playback",
                )

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
