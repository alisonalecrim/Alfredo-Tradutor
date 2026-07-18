"""API local do motor Alfredo (FastAPI)."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from engine.audio.devices import list_audio_devices
from engine.models import LineConfig, SessionConfig
from engine.session import SessionManager

app = FastAPI(title="Alfredo Engine", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

manager = SessionManager()


class HealthResponse(BaseModel):
    status: str
    version: str
    engine: str
    session_running: bool
    mode: str | None = None


class LineConfigIn(BaseModel):
    enabled: bool = True
    input_device: int | None = None
    output_device: int | None = None
    source_lang: str = "en"
    target_lang: str = "pt"
    label: str = "A"


class StartRequest(BaseModel):
    mode: str = Field(
        default="passthrough",
        description="passthrough | translate",
    )
    line_a: LineConfigIn
    line_b: LineConfigIn


class StatusResponse(BaseModel):
    running: bool
    mode: str | None = None
    line_a: dict[str, Any] = Field(default_factory=dict)
    line_b: dict[str, Any] = Field(default_factory=dict)
    captions: list[dict[str, str]] = Field(default_factory=list)
    error: str | None = None


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    snap = manager.snapshot()
    return HealthResponse(
        status="ok",
        version="0.1.0",
        engine="alfredo-python",
        session_running=snap["running"],
        mode=snap.get("mode"),
    )


@app.get("/devices")
def devices() -> dict[str, Any]:
    return list_audio_devices()


@app.get("/status", response_model=StatusResponse)
def status() -> StatusResponse:
    snap = manager.snapshot()
    return StatusResponse(**snap)


@app.post("/session/start", response_model=StatusResponse)
def session_start(body: StartRequest) -> StatusResponse:
    if body.mode not in {"passthrough", "translate"}:
        raise HTTPException(400, "mode deve ser passthrough ou translate")

    cfg = SessionConfig(
        mode=body.mode,
        line_a=LineConfig(
            enabled=body.line_a.enabled,
            input_device=body.line_a.input_device,
            output_device=body.line_a.output_device,
            source_lang=body.line_a.source_lang,
            target_lang=body.line_a.target_lang,
            label="A",
        ),
        line_b=LineConfig(
            enabled=body.line_b.enabled,
            input_device=body.line_b.input_device,
            output_device=body.line_b.output_device,
            source_lang=body.line_b.source_lang,
            target_lang=body.line_b.target_lang,
            label="B",
        ),
    )
    try:
        manager.start(cfg)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, str(exc)) from exc
    return StatusResponse(**manager.snapshot())


@app.post("/session/stop", response_model=StatusResponse)
def session_stop() -> StatusResponse:
    manager.stop()
    return StatusResponse(**manager.snapshot())
