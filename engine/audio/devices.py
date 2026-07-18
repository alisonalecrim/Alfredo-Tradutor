"""Listagem amigável de dispositivos de áudio via PortAudio/sounddevice."""

from __future__ import annotations

import re
from typing import Any

# Dispositivos ALSA técnicos que confundem o usuário quando há PipeWire/Pulse
_TECHNICAL_NAME = re.compile(
    r"^(sysdefault|front|surround\d*|hdmi|dmix|default|pipewire)$"
    r"|^\s*HDA Intel.*\(hw:"
    r"|^hdmi\b",
    re.IGNORECASE,
)


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
            kind = _classify(name, max_in, max_out)
            technical = _is_technical(name, host)
            item = {
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
            }
            devices.append(item)

        default_in, default_out = sd.default.device
        default_input = int(default_in) if default_in is not None else None
        default_output = int(default_out) if default_out is not None else None

        inputs = [d for d in devices if d["max_input_channels"] > 0]
        outputs = [d for d in devices if d["max_output_channels"] > 0]
        inputs_simple = [d for d in inputs if not d["technical"]]
        outputs_simple = [d for d in outputs if not d["technical"]]

        # Se o filtro remover tudo, volta a lista completa
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

        return {
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
                "line_a": (
                    "Linha A = o que a outra pessoa fala. "
                    "Capture o som do computador (monitor) e ouça nos seus fones."
                ),
                "line_b": (
                    "Linha B = o que você fala. "
                    "Capture seu microfone e envie a tradução para a saída "
                    "(depois: microfone virtual da call)."
                ),
            },
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "error": str(exc),
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


def _classify(name: str, max_in: int, max_out: int) -> str:
    n = name.lower()
    if "monitor" in n:
        return "system_loopback"
    if max_in > 0 and max_out == 0:
        if any(k in n for k in ("mic", "input", "analog", "source")):
            return "microphone"
        return "microphone"
    if max_out > 0 and max_in == 0:
        if any(k in n for k in ("headphone", "fone", "headset")):
            return "headphones"
        if "hdmi" in n:
            return "hdmi"
        if any(k in n for k in ("sink", "speaker", "output", "analog")):
            return "speakers"
        return "speakers"
    if max_in > 0 and max_out > 0:
        if "pipewire" in n or n == "default":
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
    """Nome curto para a UI."""
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
        return "Som do computador (Meet, Zoom, navegador…)"
    if kind == "microphone":
        if "default source" in name.lower():
            return "Microfone padrão do sistema"
        return f"Microfone — {n}"
    if kind == "speakers":
        if "default sink" in name.lower():
            return "Alto-falantes / fones padrão"
        return f"Saída de áudio — {n}"
    if kind == "headphones":
        return f"Fones — {n}"
    if kind == "hdmi":
        return f"HDMI — {n}"
    if kind == "duplex_default":
        return "Padrão do sistema (PipeWire/Pulse)"
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
    n = name.strip()
    if _TECHNICAL_NAME.search(n):
        return True
    # Prefere PulseAudio/PipeWire; esconde hw ALSA cru se host for ALSA
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
    # Evita usar o mesmo mic na linha A se houver monitor
    if a_in is None:
        a_in = _pick(inputs, ("duplex_default", "microphone"), default_input)
    a_out = _pick(outputs, ("headphones", "speakers", "duplex_default"), default_output)
    b_out = _pick(outputs, ("speakers", "headphones", "duplex_default"), default_output)

    return {
        "line_a": {
            "input_device": a_in,
            "output_device": a_out,
            "summary": (
                "Captura o som do PC (Meet/Zoom) e toca nos seus fones/alto-falantes."
            ),
        },
        "line_b": {
            "input_device": b_in,
            "output_device": b_out,
            "summary": (
                "Captura seu microfone e envia o áudio traduzido pela saída escolhida."
            ),
        },
    }
