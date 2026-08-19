"""Listagem amigável e multiplataforma de dispositivos de áudio."""

from __future__ import annotations

import platform
import re
from typing import Any

WINDOWS_LOOPBACK_BASE = 100_000
_WINDOWS_LOOPBACK_IDS: dict[int, Any] = {}

_TECHNICAL_NAME = re.compile(
    r"^(sysdefault|front|surround\d*|hdmi|dmix|default|pipewire)$"
    r"|^\s*HDA Intel.*\(hw:"
    r"|^hdmi\b",
    re.IGNORECASE,
)


def _is_windows() -> bool:
    return platform.system().lower() == "windows"


def is_windows_loopback_device(index: int | None) -> bool:
    return bool(_is_windows() and index is not None and index >= WINDOWS_LOOPBACK_BASE)


def _windows_loopbacks() -> list[dict[str, Any]]:
    """Retorna endpoints WASAPI loopback usando SoundCard.

    SoundCard expõe saídas do Windows como microfones virtuais quando
    include_loopback=True. Usamos índices sintéticos para manter compatibilidade
    com o contrato atual da UI/API, que trabalha com índices inteiros.
    """
    if not _is_windows():
        return []

    try:
        import soundcard as sc
    except Exception:
        return []

    try:
        normal = sc.all_microphones(include_loopback=False)
        normal_ids = {str(getattr(m, "id", "")) for m in normal}
        all_mics = sc.all_microphones(include_loopback=True)
        loopbacks = [
            m for m in all_mics
            if str(getattr(m, "id", "")) not in normal_ids
        ]

        _WINDOWS_LOOPBACK_IDS.clear()
        result: list[dict[str, Any]] = []
        for ordinal, mic in enumerate(loopbacks):
            index = WINDOWS_LOOPBACK_BASE + ordinal
            backend_id = getattr(mic, "id", None)
            _WINDOWS_LOOPBACK_IDS[index] = backend_id
            name = str(getattr(mic, "name", None) or f"Saída do sistema {ordinal + 1}")
            result.append(
                {
                    "index": index,
                    "name": name,
                    "label": f"Som do computador — {name}",
                    "kind": "system_loopback",
                    "kind_label": "Som do sistema (WASAPI)",
                    "technical": False,
                    "recommended_for": ["a_input"],
                    "max_input_channels": 2,
                    "max_output_channels": 0,
                    "default_samplerate": 48000.0,
                    "hostapi": -1,
                    "hostapi_name": "Windows WASAPI loopback",
                    "backend": "soundcard_loopback",
                }
            )
        return result
    except Exception:
        return []


def get_windows_loopback_microphone(index: int):
    """Resolve um índice sintético para um microfone loopback do SoundCard."""
    if not is_windows_loopback_device(index):
        raise ValueError(f"Dispositivo {index} não é um loopback WASAPI do Alfredo")

    if index not in _WINDOWS_LOOPBACK_IDS:
        _windows_loopbacks()
    backend_id = _WINDOWS_LOOPBACK_IDS.get(index)
    if backend_id is None:
        raise RuntimeError("Loopback WASAPI não encontrado; atualize a lista de dispositivos")

    import soundcard as sc

    mic = sc.get_microphone(backend_id, include_loopback=True)
    if mic is None:
        raise RuntimeError("Não foi possível abrir o loopback WASAPI selecionado")
    return mic


def list_audio_devices() -> dict[str, Any]:
    try:
        import sounddevice as sd

        raw = sd.query_devices()
        hostapis = sd.query_hostapis()
        host_names = {i: str(h.get("name") or "") for i, h in enumerate(hostapis)}

        devices: list[dict[str, Any]] = []
        for i, d in enumerate(raw):
            name = str(d.get("name") or f"Dispositivo {i}")
            hostapi = int(d.get("hostapi") or 0)
            host = host_names.get(hostapi, "")
            max_in = int(d.get("max_input_channels") or 0)
            max_out = int(d.get("max_output_channels") or 0)
            kind = _classify(name, max_in, max_out, host)
            technical = _is_technical(name, host)
            devices.append(
                {
                    "index": i,
                    "name": name,
                    "label": _friendly_label(name, kind),
                    "kind": kind,
                    "kind_label": _kind_label(kind),
                    "technical": technical,
                    "recommended_for": _recommended_roles(kind),
                    "max_input_channels": max_in,
                    "max_output_channels": max_out,
                    "default_samplerate": float(d.get("default_samplerate") or 0),
                    "hostapi": hostapi,
                    "hostapi_name": host,
                    "backend": "sounddevice",
                }
            )

        # No Windows adiciona endpoints de reprodução como fontes WASAPI loopback.
        devices.extend(_windows_loopbacks())

        default_in, default_out = sd.default.device
        default_input = int(default_in) if default_in is not None else None
        default_output = int(default_out) if default_out is not None else None

        inputs = [d for d in devices if d["max_input_channels"] > 0]
        outputs = [d for d in devices if d["max_output_channels"] > 0]
        inputs_simple = [d for d in inputs if not d["technical"]]
        outputs_simple = [d for d in outputs if not d["technical"]]

        if not inputs_simple:
            inputs_simple = inputs
        if not outputs_simple:
            outputs_simple = outputs

        suggestions = _build_suggestions(
            inputs_simple,
            outputs_simple,
            default_input,
            default_output,
        )

        system = platform.system()
        if _is_windows():
            guide_a = (
                "Linha A = o que a outra pessoa fala. "
                "Escolha 'Som do computador' (WASAPI loopback) e ouça nos seus fones."
            )
            platform_hint = "Windows: captura do sistema via WASAPI loopback."
        else:
            guide_a = (
                "Linha A = o que a outra pessoa fala. "
                "Capture o som do computador (monitor) e ouça nos seus fones."
            )
            platform_hint = "Linux: captura do sistema via PipeWire/Pulse/ALSA."

        return {
            "platform": system,
            "platform_hint": platform_hint,
            "default_input": default_input,
            "default_output": default_output,
            "hostapis": [{"index": i, "name": n} for i, n in host_names.items()],
            "devices": devices,
            "inputs": inputs,
            "outputs": outputs,
            "inputs_simple": inputs_simple,
            "outputs_simple": outputs_simple,
            "suggestions": suggestions,
            "guide": {
                "line_a": guide_a,
                "line_b": (
                    "Linha B = o que você fala. Capture seu microfone e envie a tradução "
                    "para a saída escolhida; depois, use um microfone virtual da call."
                ),
            },
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "error": str(exc),
            "platform": platform.system(),
            "default_input": None,
            "default_output": None,
            "hostapis": [],
            "devices": [],
            "inputs": [],
            "outputs": [],
            "inputs_simple": [],
            "outputs_simple": [],
            "suggestions": {},
            "guide": {},
        }


def _classify(name: str, max_in: int, max_out: int, host: str = "") -> str:
    n = name.lower()
    h = host.lower()
    if "monitor" in n or "loopback" in n or "stereo mix" in n or "mixagem estéreo" in n:
        return "system_loopback"
    if max_in > 0 and max_out == 0:
        return "microphone"
    if max_out > 0 and max_in == 0:
        if any(k in n for k in ("headphone", "fone", "headset")):
            return "headphones"
        if "hdmi" in n:
            return "hdmi"
        return "speakers"
    if max_in > 0 and max_out > 0:
        if "pipewire" in n or n == "default" or "wasapi" in h:
            return "duplex_default"
        return "duplex"
    return "other"


def _kind_label(kind: str) -> str:
    return {
        "microphone": "Microfone",
        "system_loopback": "Som do sistema",
        "speakers": "Alto-falantes",
        "headphones": "Fones",
        "hdmi": "HDMI / TV",
        "duplex_default": "Padrão do sistema",
        "duplex": "Entrada e saída",
        "other": "Outro",
    }.get(kind, "Outro")


def _friendly_label(name: str, kind: str) -> str:
    n = name
    n = re.sub(r"^alsa_input\.", "", n)
    n = re.sub(r"^alsa_output\.", "", n)
    n = re.sub(r"\.analog-stereo\.monitor$", "", n)
    n = re.sub(r"\.analog-stereo$", "", n)
    n = re.sub(r"pci-[0-9a-f_]+", "", n, flags=re.I)
    n = n.replace("_", " ").strip(" .-")
    if not n or n.lower() in {"monitor", "input", "output"}:
        n = "placa de áudio integrada"

    if kind == "system_loopback":
        return f"Som do computador — {n}"
    if kind == "microphone":
        return f"Microfone — {n}"
    if kind == "speakers":
        return f"Saída de áudio — {n}"
    if kind == "headphones":
        return f"Fones — {n}"
    if kind == "hdmi":
        return f"HDMI — {n}"
    if kind == "duplex_default":
        return f"Padrão do sistema — {n}"
    return n or name


def _recommended_roles(kind: str) -> list[str]:
    if kind == "system_loopback":
        return ["a_input"]
    if kind == "microphone":
        return ["b_input"]
    if kind in {"speakers", "headphones", "duplex_default"}:
        return ["a_output", "b_output"]
    return []


def _is_technical(name: str, host: str) -> bool:
    if _is_windows():
        # No Windows, prefira WASAPI e esconda endpoints legados/duplicados.
        h = host.lower()
        if any(x in h for x in ("mme", "directsound", "wdm-ks")):
            return True
        return False

    n = name.strip()
    if _TECHNICAL_NAME.search(n):
        return True
    if "ALSA" in host and "(hw:" in n:
        return True
    if re.match(r"^(front|surround|hdmi|dmix),", n, re.I):
        return True
    return False


def _pick(
    devices: list[dict[str, Any]],
    kinds: tuple[str, ...],
    fallback_index: int | None = None,
) -> int | None:
    for kind in kinds:
        for d in devices:
            if d["kind"] == kind:
                return int(d["index"])
    if fallback_index is not None and any(d["index"] == fallback_index for d in devices):
        return fallback_index
    return int(devices[0]["index"]) if devices else None


def _build_suggestions(
    inputs: list[dict[str, Any]],
    outputs: list[dict[str, Any]],
    default_input: int | None,
    default_output: int | None,
) -> dict[str, Any]:
    a_in = _pick(inputs, ("system_loopback",), None)
    b_in = _pick(inputs, ("microphone",), default_input)
    if a_in is None:
        a_in = _pick(inputs, ("duplex_default", "microphone"), default_input)
    a_out = _pick(outputs, ("headphones", "speakers", "duplex_default"), default_output)
    b_out = _pick(outputs, ("speakers", "headphones", "duplex_default"), default_output)

    return {
        "line_a": {
            "input_device": a_in,
            "output_device": a_out,
            "summary": "Captura o som da call e toca a tradução nos seus fones.",
        },
        "line_b": {
            "input_device": b_in,
            "output_device": b_out,
            "summary": "Captura seu microfone e envia o áudio traduzido pela saída escolhida.",
        },
    }
