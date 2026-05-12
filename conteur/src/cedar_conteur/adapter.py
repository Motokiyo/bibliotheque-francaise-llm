"""Portable OpenAI Realtime adapter for Cedar storyteller.

Extracted and cleaned from reachy_care/app/conv_app_v2/llm/openai_realtime.py.
No Pi-specific code, no upsample (browser sends 24kHz native via AudioWorklet).
Keeps: semantic_vad eagerness=low, interrupt_response=True, noise_reduction=far_field,
keepalive 5s, French transcription. Adds: reasoning_effort exposed.
"""

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Awaitable

import websockets.asyncio.client as _ws_client_mod
_original_ws_connect = _ws_client_mod.connect


def _ws_connect_with_keepalive(*args, **kwargs):
    kwargs.setdefault("ping_interval", 10)
    kwargs.setdefault("ping_timeout", 60)
    return _original_ws_connect(*args, **kwargs)


_ws_client_mod.connect = _ws_connect_with_keepalive

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

_SILENCE_200MS_24K = b"\x00" * 9600
_KEEPALIVE_INTERVAL = 5
_MAX_SESSION_SECONDS = 55 * 60


@dataclass
class SessionConfig:
    instructions: str = ""
    voice: str = "cedar"
    model: str = "gpt-realtime-2"
    speed: float = 1.0
    reasoning_effort: str = "medium"
    language: str = "fr"
    enable_preambles: bool = True
    vad_eagerness: str = "low"
    tools: list[dict] = field(default_factory=list)


class OpenAIRealtimeAdapter:
    def __init__(self, api_key: str | None = None, on_event: Callable[[dict], Awaitable[None]] | None = None):
        self._api_key = api_key
        self._client: AsyncOpenAI | None = None
        self._conn = None
        self._connected = False
        self._session_start = 0.0
        self._cfg: SessionConfig | None = None
        self._event_loop_task: asyncio.Task | None = None
        self._keepalive_task: asyncio.Task | None = None
        self._on_event = on_event

    async def connect(self, cfg: SessionConfig) -> None:
        self._cfg = cfg
        self._client = AsyncOpenAI(api_key=self._api_key) if self._api_key else AsyncOpenAI()
        self._conn = await self._client.realtime.connect(model=cfg.model).__aenter__()

        session_payload: dict[str, Any] = {
            "type": "realtime",
            "instructions": cfg.instructions,
            "output_modalities": ["audio"],
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "transcription": {"model": "gpt-4o-transcribe", "language": cfg.language},
                    "turn_detection": {
                        "type": "semantic_vad",
                        "eagerness": cfg.vad_eagerness,
                        "create_response": True,
                        "interrupt_response": True,
                    },
                    "noise_reduction": {"type": "far_field"},
                },
                "output": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "voice": cfg.voice,
                    "speed": cfg.speed,
                },
            },
            "tools": cfg.tools,
            "tool_choice": "auto",
        }

        # V2-only parameters: parallel_tool_calls + reasoning.effort
        # V1 (gpt-realtime) rejects them with "Unsupported option for this model"
        if cfg.model.startswith("gpt-realtime-2"):
            session_payload["reasoning"] = {"effort": cfg.reasoning_effort}
            session_payload["parallel_tool_calls"] = True
        # NB: there is no native "preamble" parameter. Audible preambles
        # are configured via the `# Preambles` section of the system prompt
        # (see prompts.py). enable_preambles flag is kept for prompt switching only.

        await self._conn.session.update(session=session_payload)
        self._connected = True
        self._session_start = time.monotonic()

        # CRITICAL: wait until OpenAI confirms session.updated before letting the caller
        # send audio. Default voice for gpt-realtime-2 is "marin"; if user audio arrives
        # before session.update lands, the voice is locked on the default for the whole
        # session (OpenAI freezes voice on first audio output).
        confirmed_voice = None
        try:
            for _ in range(20):
                event = await asyncio.wait_for(self._conn.recv(), timeout=3.0)
                et = getattr(event, "type", "?")
                if et == "session.updated":
                    s = getattr(event, "session", None)
                    if s and hasattr(s, "model_dump"):
                        confirmed_voice = (s.model_dump().get("audio") or {}).get("output", {}).get("voice")
                    logger.info("session.updated confirmed, voice=%s", confirmed_voice)
                    break
                if et == "error":
                    err = getattr(event, "error", None)
                    logger.error("session.update rejected: %s", err)
                    if self._on_event:
                        await self._on_event({"type": "error", "error": {"message": str(err)}})
                    break
        except asyncio.TimeoutError:
            logger.warning("Timeout waiting for session.updated, proceeding anyway")
            if self._on_event:
                await self._on_event({"type": "error", "error": {"message": "Timeout waiting for session.updated"}})

        self._event_loop_task = asyncio.create_task(self._event_loop())
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())
        logger.info("Connected to %s with voice=%s reasoning=%s", cfg.model, cfg.voice, cfg.reasoning_effort)

    async def send_audio_24k(self, pcm_chunk: bytes) -> None:
        if not self._connected or self._conn is None:
            return
        audio_b64 = base64.b64encode(pcm_chunk).decode("utf-8")
        await self._conn.input_audio_buffer.append(audio=audio_b64)

    async def send_text(self, text: str) -> None:
        if not self._connected or self._conn is None:
            return
        await self._conn.conversation.item.create(item={
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": text}],
        })
        await self._conn.response.create()

    async def update_instructions(self, new_instructions: str) -> None:
        if not self._connected or self._conn is None or self._cfg is None:
            return
        self._cfg.instructions = new_instructions
        await self._conn.session.update(session={
            "type": "realtime",
            "instructions": new_instructions,
        })

    async def update_speed(self, speed: float) -> None:
        if not self._connected or self._conn is None:
            return
        await self._conn.session.update(session={
            "type": "realtime",
            "audio": {"output": {"speed": speed}},
        })

    async def cancel_response(self) -> None:
        if not self._connected or self._conn is None:
            return
        try:
            await self._conn.response.cancel()
        except Exception as exc:
            logger.debug("cancel ignored: %s", exc)

    async def send_tool_result(self, call_id: str, result: dict) -> None:
        """Send a function_call_output back to OpenAI and trigger a response."""
        if not self._connected or self._conn is None:
            return
        try:
            await self._conn.conversation.item.create(item={
                "type": "function_call_output",
                "call_id": call_id,
                "output": json.dumps(result, ensure_ascii=False),
            })
            await self._conn.response.create()
        except Exception as exc:
            logger.warning("send_tool_result failed (%s): %s", call_id, exc)

    async def disconnect(self) -> None:
        self._connected = False
        for task in (self._event_loop_task, self._keepalive_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if self._conn is not None:
            try:
                await self._conn.__aexit__(None, None, None)
            except Exception:
                pass
            self._conn = None
        logger.info("Disconnected")

    async def _event_loop(self) -> None:
        try:
            async for event in self._conn:
                if self._on_event:
                    payload = _event_to_dict(event)
                    try:
                        await self._on_event(payload)
                    except Exception as exc:
                        logger.warning("on_event handler raised: %s", exc)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("Event loop ended: %s", exc)
            self._connected = False

    async def _keepalive_loop(self) -> None:
        try:
            while self._connected:
                await asyncio.sleep(_KEEPALIVE_INTERVAL)
                if not self._connected or self._conn is None:
                    break
                try:
                    audio_b64 = base64.b64encode(_SILENCE_200MS_24K).decode("utf-8")
                    await self._conn.input_audio_buffer.append(audio=audio_b64)
                except Exception as exc:
                    logger.debug("keepalive failed: %s", exc)
                    break
        except asyncio.CancelledError:
            pass


def _event_to_dict(event: Any) -> dict:
    if hasattr(event, "model_dump"):
        try:
            return event.model_dump()
        except Exception:
            pass
    if isinstance(event, dict):
        return event
    return {"type": getattr(event, "type", "unknown")}
