"""Pipeline fala → texto → tradução → fala (CPU), otimizado para baixa latência."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

import numpy as np


@lru_cache(maxsize=1)
def _shared_whisper():
    """Carrega uma única instância do Whisper para todas as linhas.

    Evita duplicar memória/modelo quando A e B estão ativos ao mesmo tempo.
    O CTranslate2 pode atender mais de uma requisição com num_workers > 1.
    """
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

    def process(
        self, audio: np.ndarray, sample_rate: int
    ) -> tuple[str, str, np.ndarray | None, int]:
        if not self._ready or self._whisper is None:
            raise RuntimeError("Pipeline não carregado")

        if audio.size == 0:
            return "", "", None, sample_rate

        # O LineWorker já faz endpointing/VAD leve. Aqui priorizamos velocidade.
        segments, _info = self._whisper.transcribe(
            audio,
            language=_whisper_lang(self.source_lang),
            vad_filter=False,
            beam_size=1,
            best_of=1,
            temperature=0.0,
            condition_on_previous_text=False,
            without_timestamps=True,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        if not text:
            return "", "", None, sample_rate

        translated = translate_text(text, self.source_lang, self.target_lang)
        tts_audio, tts_rate = synthesize(translated, self.target_lang)
        return text, translated, tts_audio, tts_rate


def _whisper_lang(code: str) -> str:
    return {"pt": "pt", "en": "en", "es": "es", "fr": "fr"}.get(code, code)


def translate_text(text: str, source: str, target: str) -> str:
    """Tradução via deep-translator com instância reutilizada por par de idiomas."""
    if source == target:
        return text
    try:
        return _translator(source, target).translate(text)
    except Exception:
        # Mantém o áudio funcional mesmo se o serviço de tradução falhar.
        return text


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
                [
                    espeak,
                    "-v",
                    voice,
                    "-s",
                    "175",
                    "-w",
                    str(wav_path),
                    text,
                ],
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
