"""Modelos de configuração do motor."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LineConfig:
    enabled: bool = True
    input_device: int | None = None
    output_device: int | None = None
    source_lang: str = "en"
    target_lang: str = "pt"
    label: str = "A"


@dataclass
class SessionConfig:
    mode: str = "passthrough"  # passthrough | translate
    line_a: LineConfig = field(default_factory=lambda: LineConfig(label="A"))
    line_b: LineConfig = field(default_factory=lambda: LineConfig(label="B"))
