"""Pipeline fala → texto → tradução → fala (CPU)."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np


class SpeechPipeline:
    def __init__(self, source_lang: str, target_lang: str) -> None:
        self.source_lang = source_lang
        self.target_lang = target_lang
        self._whisper = None
        self._ready = False

    def load(self) -> None:
        from faster_whisper import WhisperModel

        # CPU int8 — adequado a notebook sem GPU
        self._whisper = WhisperModel("base", device="cpu", compute_type="int8")
        self._ready = True

    def process(
        self, audio: np.ndarray, sample_rate: int
    ) -> tuple[str, str, np.ndarray | None, int]:
        if not self._ready or self._whisper is None:
            raise RuntimeError("Pipeline não carregado")

        segments, _info = self._whisper.transcribe(
            audio,
            language=_whisper_lang(self.source_lang),
            vad_filter=True,
            beam_size=1,
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
    """Tradução via deep-translator (sem instalar PyTorch)."""
    if source == target:
        return text
    try:
        from deep_translator import GoogleTranslator

        return GoogleTranslator(source=source, target=target).translate(text)
    except Exception:
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
                    "160",
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
