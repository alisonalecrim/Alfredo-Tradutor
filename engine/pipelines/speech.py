"""Pipeline fala → texto → tradução → fala (CPU), otimizado para baixa latência."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np


class TranslationError(RuntimeError):
    """Falha explícita de tradução: nunca deve cair silenciosamente no texto original."""


@dataclass(frozen=True)
class SpeechResult:
    text: str
    translated: str
    tts_audio: np.ndarray | None
    tts_rate: int
    stt_ms: int = 0
    translation_ms: int = 0
    tts_ms: int = 0
    confidence: float | None = None

    @property
    def total_ms(self) -> int:
        return self.stt_ms + self.translation_ms + self.tts_ms


@lru_cache(maxsize=1)
def _shared_whisper():
    """Carrega uma única instância do Whisper para todas as linhas."""
    from faster_whisper import WhisperModel

    cpu_threads = max(1, min(4, os.cpu_count() or 1))
    return WhisperModel(
        "base",
        device="cpu",
        compute_type="int8",
        cpu_threads=cpu_threads,
        num_workers=2,
    )


@lru_cache(maxsize=16)
def _translator(source: str, target: str):
    from deep_translator import GoogleTranslator

    return GoogleTranslator(source=source, target=target)


class SpeechPipeline:
    def __init__(self, source_lang: str, target_lang: str) -> None:
        self.source_lang = source_lang
        self.target_lang = target_lang
        self._whisper = None
        self._ready = False

    def load(self) -> None:
        self._whisper = _shared_whisper()
        self._ready = True

    def process(self, audio: np.ndarray, sample_rate: int) -> SpeechResult:
        if not self._ready or self._whisper is None:
            raise RuntimeError("Pipeline não carregado")
        if audio.size == 0:
            return SpeechResult("", "", None, sample_rate)

        started = time.perf_counter()
        segments_iter, _info = self._whisper.transcribe(
            audio,
            language=_whisper_lang(self.source_lang),
            vad_filter=False,
            beam_size=1,
            best_of=1,
            temperature=0.0,
            condition_on_previous_text=False,
            without_timestamps=True,
        )
        segments = list(segments_iter)
        stt_ms = int((time.perf_counter() - started) * 1000)

        text = " ".join(s.text.strip() for s in segments).strip()
        if not text:
            return SpeechResult("", "", None, sample_rate, stt_ms=stt_ms)

        confidence = _estimate_confidence(segments)
        # Filtro conservador: evita traduzir alucinações muito evidentes sem
        # descartar fala normal em ambientes ruidosos.
        if confidence is not None and confidence < 0.12:
            return SpeechResult("", "", None, sample_rate, stt_ms=stt_ms, confidence=confidence)

        started = time.perf_counter()
        translated = translate_text(text, self.source_lang, self.target_lang)
        translation_ms = int((time.perf_counter() - started) * 1000)

        started = time.perf_counter()
        tts_audio, tts_rate = synthesize(translated, self.target_lang)
        tts_ms = int((time.perf_counter() - started) * 1000)

        return SpeechResult(
            text=text,
            translated=translated,
            tts_audio=tts_audio,
            tts_rate=tts_rate,
            stt_ms=stt_ms,
            translation_ms=translation_ms,
            tts_ms=tts_ms,
            confidence=confidence,
        )


def _estimate_confidence(segments: list[object]) -> float | None:
    if not segments:
        return None
    scores: list[float] = []
    for segment in segments:
        avg_logprob = getattr(segment, "avg_logprob", None)
        no_speech_prob = getattr(segment, "no_speech_prob", None)
        if avg_logprob is None:
            continue
        # Converte log-probabilidade em uma escala útil de 0..1 e penaliza
        # segmentos que o modelo considera provável silêncio.
        score = float(np.exp(max(-5.0, min(0.0, float(avg_logprob)))))
        if no_speech_prob is not None:
            score *= max(0.0, 1.0 - float(no_speech_prob))
        scores.append(score)
    return float(sum(scores) / len(scores)) if scores else None


def _whisper_lang(code: str) -> str:
    return {"pt": "pt", "en": "en", "es": "es", "fr": "fr"}.get(code, code)


def translate_text(text: str, source: str, target: str) -> str:
    """Traduz sem fallback silencioso para o idioma original."""
    if source == target:
        return text
    try:
        translated = _translator(source, target).translate(text)
    except Exception as exc:  # noqa: BLE001
        raise TranslationError(f"Falha na tradução {source}→{target}: {exc}") from exc

    translated = (translated or "").strip()
    if not translated:
        raise TranslationError(f"Tradução {source}→{target} retornou vazia")
    return translated


def synthesize(text: str, lang: str) -> tuple[np.ndarray | None, int]:
    """TTS via espeak-ng (CPU, sem custo)."""
    if not text.strip():
        return None, 22050
    espeak = shutil.which("espeak-ng") or shutil.which("espeak")
    if not espeak:
        return None, 22050

    voice = {
        "pt": "pt-br",
        "en": "en",
        "es": "es",
        "fr": "fr",
    }.get(lang, "en")

    with tempfile.TemporaryDirectory() as tmp:
        wav_path = Path(tmp) / "out.wav"
        try:
            subprocess.run(
                [espeak, "-v", voice, "-s", "175", "-w", str(wav_path), text],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            return None, 22050
        return _read_wav(wav_path)


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    import wave

    with wave.open(str(path), "rb") as wf:
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
        width = wf.getsampwidth()
        channels = wf.getnchannels()

    if width == 2:
        data = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    else:
        data = np.frombuffer(frames, dtype=np.uint8).astype(np.float32)
        data = (data - 128.0) / 128.0

    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data, rate
