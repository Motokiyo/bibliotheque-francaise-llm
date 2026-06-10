"""Conteur orchestrator — wires adapter + prompt + annotations + library."""

import logging
from typing import Any, Awaitable, Callable

from .adapter import OpenAIRealtimeAdapter, SessionConfig
from .prompts import build_system_prompt

logger = logging.getLogger(__name__)


class Conteur:
    def __init__(self, api_key: str | None = None, on_event: Callable[[dict], Awaitable[None]] | None = None):
        self.adapter = OpenAIRealtimeAdapter(api_key=api_key, on_event=on_event)
        self._oeuvre: dict[str, Any] = {}
        self._annotations: dict[str, Any] = {}
        self._cfg: SessionConfig | None = None

    async def start(
        self,
        oeuvre: dict[str, Any],
        annotations: dict[str, Any],
        model: str = "gpt-realtime-2",
        voice: str = "cedar",
        speed: float = 0.92,
        reasoning_effort: str = "medium",
        enable_preambles: bool = True,
        extra_instructions: str = "",
        tools: list[dict] | None = None,
    ) -> None:
        self._oeuvre = oeuvre
        self._annotations = annotations
        system_prompt = build_system_prompt(oeuvre, annotations)
        if extra_instructions:
            system_prompt = system_prompt + "\n\n# Notes de session\n" + extra_instructions

        self._cfg = SessionConfig(
            instructions=system_prompt,
            voice=voice,
            model=model,
            speed=speed,
            reasoning_effort=reasoning_effort,
            enable_preambles=enable_preambles,
            tools=tools or [],
        )
        await self.adapter.connect(self._cfg)

    async def push_oeuvre_text(self, scene_text: str) -> None:
        await self.adapter.send_text(
            "Lis intégralement le segment ci-dessous, mot pour mot, dans l'ordre. "
            "N'abrège pas, ne résume pas, ne saute aucun paragraphe, n'ajoute pas de transition. "
            "Commence directement par le texte, sans annoncer le segment ni faire de commentaire. "
            "Si le segment se termine au milieu d'un chapitre, arrête-toi exactement à la fin du segment ; "
            "le segment suivant sera envoyé ensuite. Texte :\n\n" + scene_text
        )

    async def push_user_text(self, text: str) -> None:
        await self.adapter.send_text(text)

    async def send_audio_24k(self, pcm: bytes) -> None:
        await self.adapter.send_audio_24k(pcm)

    async def update_speed(self, speed: float) -> None:
        await self.adapter.update_speed(speed)

    async def cancel(self) -> None:
        await self.adapter.cancel_response()

    async def stop(self) -> None:
        await self.adapter.disconnect()
