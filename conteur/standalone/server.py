"""FastAPI server: serves the static UI and bridges browser audio to OpenAI Realtime.

Tools exposed to Cedar:
- load_scene(act, scene) → returns the text of a specific scene
- load_full_play() → returns the full play (when user asks for everything)
- list_scenes() → returns the index of all scenes
"""

import asyncio
import hashlib
import json
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
import httpx

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import base64

from cedar_conteur import Conteur  # noqa: E402
from cedar_conteur.annotations import (  # noqa: E402
    load_annotations,
    save_annotations,
    seed_default_annotations,
)
from cedar_conteur.dsp import (  # noqa: E402
    DSPBufferedProcessor,
    apply_profile,
    detect_perso,
    detect_perso_with_pos,
    profile_for_perso,
)
from cedar_conteur.robot import RobotController  # noqa: E402
from cedar_conteur.library import (  # noqa: E402
    fetch_oeuvre,
    get_scene_text,
    list_oeuvres_from_catalog,
    list_scenes,
    load_catalog,
)
from cedar_conteur.prompts import build_system_prompt  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
logger = logging.getLogger("conteur.server")

OPENAI_KEY = os.getenv("OPENAI_API_KEY")
DEFAULT_MODEL = os.getenv("MODEL", "gpt-realtime-2")
DEFAULT_VOICE = os.getenv("VOICE", "cedar")
ENVIRONMENT = os.getenv("ENVIRONMENT", "development").lower()
PRODUCTION = ENVIRONMENT in {"prod", "production"}
ALLOW_ROBOT = os.getenv("CONTEUR_ENABLE_ROBOT", "0" if PRODUCTION else "1") == "1"
ENABLE_TTS_ENDPOINT = os.getenv("CONTEUR_ENABLE_TTS_ENDPOINT", "0" if PRODUCTION else "1") == "1"
ALLOWED_ORIGINS = {
    origin.strip().rstrip("/")
    for origin in os.getenv(
        "CONTEUR_ALLOWED_ORIGINS",
        "https://conteur.eiffelai.io,http://127.0.0.1:7860,http://localhost:7860",
    ).split(",")
    if origin.strip()
}
BOOKS_DIR = PROJECT_ROOT / "data" / "books"
TTS_CACHE_DIR = PROJECT_ROOT / ".cache" / "tts"
TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)

TRACE_DIR = Path("/tmp")
TRACE_FILE = TRACE_DIR / f"cedar-conteur-trace-{int(time.time())}.jsonl"
_trace_lock = asyncio.Lock()


async def trace(kind: str, **fields) -> None:
    """Append one structured event to the session trace file (JSONL)."""
    rec = {"ts": time.time(), "src": fields.pop("src", "server"), "kind": kind, **fields}
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    async with _trace_lock:
        try:
            with TRACE_FILE.open("a", encoding="utf-8") as fh:
                fh.write(line)
        except Exception as exc:
            logger.debug("trace write failed: %s", exc)


app = FastAPI(title="Cedar Conteur")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

logger.info("Trace file: %s", TRACE_FILE)
seed_default_annotations()


def _load_book(book_id: str) -> dict:
    path = BOOKS_DIR / f"{book_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Book not found: {book_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def _book_as_oeuvre(book: dict) -> dict:
    text_complet = "\n\n".join(ch["text"] for ch in book.get("chapters", []))
    return {
        "id": f"book:{book['id']}",
        "titre": book.get("title", book["id"]),
        "auteur": book.get("author", "Auteur inconnu"),
        "date": "",
        "genre": "roman",
        "source": book.get("source", "local"),
        "text_complet": text_complet,
        "by_character_structured": {},
        "personnages_dracor": [],
        "n_actes": "",
        "n_scenes": len(book.get("chapters", [])),
        "n_repliques": "",
    }


def _list_books() -> list[dict]:
    books = []
    for path in sorted(BOOKS_DIR.glob("*.json")):
        book = json.loads(path.read_text(encoding="utf-8"))
        books.append({
            "id": book.get("id", path.stem),
            "title": book.get("title", path.stem),
            "author": book.get("author", ""),
        })
    return books


CONTEUR_TOOLS = [
    {
        "type": "function",
        "name": "load_scene",
        "description": (
            "Récupère le texte exact d'une scène précise de la pièce en cours. "
            "À appeler quand l'auditeur demande de lire une scène spécifique."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "act": {"type": "string", "description": "Numéro d'acte (ex: '1')"},
                "scene": {"type": "string", "description": "Numéro de scène dans l'acte (ex: '3')"},
            },
            "required": ["act", "scene"],
        },
    },
    {
        "type": "function",
        "name": "load_full_play",
        "description": (
            "Récupère le texte intégral de la pièce en cours (tous les actes et scènes). "
            "À appeler quand l'auditeur dit « lis toute la pièce »."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "type": "function",
        "name": "list_scenes",
        "description": (
            "Liste l'index des actes et scènes de la pièce en cours avec personnages présents. "
            "Utile quand l'auditeur demande « qu'est-ce qu'il y a comme scènes ? »."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
]


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((ROOT / "static" / "index.html").read_text(encoding="utf-8"))


@app.get("/api/config")
async def config() -> dict:
    return {
        "model": DEFAULT_MODEL,
        "voice": DEFAULT_VOICE,
        "has_key": bool(OPENAI_KEY),
        "environment": ENVIRONMENT,
        "allow_robot": ALLOW_ROBOT,
        "tts_endpoint": ENABLE_TTS_ENDPOINT,
    }


@app.get("/api/status")
async def status() -> dict:
    return {
        "ok": True,
        "model": DEFAULT_MODEL,
        "voice": DEFAULT_VOICE,
        "has_key": bool(OPENAI_KEY),
        "environment": ENVIRONMENT,
        "allow_robot": ALLOW_ROBOT,
        "tts_endpoint": ENABLE_TTS_ENDPOINT,
    }


@app.get("/api/library")
async def library() -> dict:
    return {
        "catalog": load_catalog(),
        "oeuvres": list_oeuvres_from_catalog(),
        "books": _list_books(),
    }


@app.get("/api/book/{book_id}")
async def get_book(book_id: str) -> dict:
    book = _load_book(book_id)
    oeuvre = _book_as_oeuvre(book)
    return {
        "book": {
            "id": book["id"],
            "title": book.get("title", book["id"]),
            "author": book.get("author", ""),
            "translator": book.get("translator", ""),
            "source": book.get("source", ""),
            "source_url": book.get("source_url", ""),
            "chapters": book.get("chapters", []),
        },
        "oeuvre": oeuvre,
        "annotations": load_annotations(oeuvre["id"]),
    }


@app.get("/api/oeuvre/{oeuvre_id}")
async def get_oeuvre(oeuvre_id: str) -> dict:
    try:
        oeuvre = await fetch_oeuvre(oeuvre_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"DraCor fetch failed: {exc}") from exc
    annotations = load_annotations(oeuvre_id)
    # Strip the heavy 'actes' tree from JSON (kept server-side); send what UI needs.
    light = {k: v for k, v in oeuvre.items() if k != "actes"}
    light["scenes_index"] = list_scenes(oeuvre)
    return {"oeuvre": light, "annotations": annotations}


@app.post("/api/oeuvre/{oeuvre_id}/annotations")
async def post_annotations(oeuvre_id: str, payload: dict) -> dict:
    save_annotations(oeuvre_id, payload)
    return {"ok": True}


@app.post("/api/trace")
async def post_trace(request: Request) -> dict:
    """Browser-side trace events. Body: {events: [{kind, ...}, ...]}.

    Browser batches events client-side and flushes via sendBeacon/fetch.
    Each event is timestamped server-side at receive time (ts_server) so
    we can align with server traces even if client clocks drift.
    """
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "error": "invalid_json"}
    events = body.get("events") or []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        kind = ev.pop("kind", "browser_event")
        ev["src"] = "browser"
        ev["ts_server"] = time.time()
        await trace(kind, **ev)
    return {"ok": True, "n": len(events)}


@app.post("/api/tts")
async def post_tts(request: Request) -> Response:
    if not ENABLE_TTS_ENDPOINT:
        raise HTTPException(status_code=404, detail="TTS endpoint disabled")
    if not OPENAI_KEY:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY missing in .env")
    body = await request.json()
    text = (body.get("text") or "").strip()
    voice = body.get("voice") or DEFAULT_VOICE or "cedar"
    if not text:
        raise HTTPException(status_code=400, detail="missing text")

    cache_key = hashlib.sha256(
        json.dumps({"text": text, "voice": voice}, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    cache_path = TTS_CACHE_DIR / f"{cache_key}.mp3"
    if cache_path.exists():
        return Response(cache_path.read_bytes(), media_type="audio/mpeg")

    payload = {
        "model": "gpt-4o-mini-tts",
        "voice": voice,
        "input": text,
        "instructions": (
            "Lis exactement le texte fourni, sans résumé, sans omission, sans commentaire. "
            "Lecture française calme de livre du soir, diction claire, rythme régulier."
        ),
        "response_format": "mp3",
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            "https://api.openai.com/v1/audio/speech",
            headers={
                "Authorization": f"Bearer {OPENAI_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    if resp.status_code >= 400:
        logger.error("TTS failed %s: %s", resp.status_code, resp.text[:500])
        raise HTTPException(status_code=resp.status_code, detail=resp.text[:500])
    cache_path.write_bytes(resp.content)
    return Response(resp.content, media_type="audio/mpeg")


@app.websocket("/ws/session")
async def ws_session(ws: WebSocket) -> None:
    origin = (ws.headers.get("origin") or "").rstrip("/")
    if PRODUCTION and origin not in ALLOWED_ORIGINS:
        logger.warning("WS rejected from origin=%r client=%s", origin, ws.client)
        await ws.close(code=1008)
        return
    await ws.accept()
    logger.info("WS accepted from %s", ws.client)
    if not OPENAI_KEY:
        await ws.send_json({"type": "error", "error": "OPENAI_API_KEY missing in .env"})
        await ws.close()
        return

    conteur: Conteur | None = None
    current_oeuvre: dict | None = None
    current_annotations: dict = {}
    send_lock = asyncio.Lock()
    pending_tool_calls: dict[str, dict] = {}  # call_id → {name, args_buffer}
    transcript_buffer = {"text": ""}
    active_perso = {"name": None}
    dsp_enabled = {"on": True}
    robot_enabled = {"on": True}
    robot: RobotController | None = None
    dsp_proc = DSPBufferedProcessor()
    scene_speakers: set[str] = set()
    # Audio/transcript sync: count cumulated audio bytes received from OpenAI
    # so we can convert "char position in transcript" → "time when audio reaches there".
    # PCM 24kHz mono = 48000 bytes/sec. We track this per-response (reset at response.done).
    audio_bytes_total = {"n": 0}
    # Adaptive speaking rate measurement (solution B).
    # `chars_recv_total` counts transcript chars received for the current
    # response. Once we have >= MIN_CHARS_FOR_ADAPTIVE, switch from the
    # constant fallback to a per-response measured rate that tracks Cedar's
    # actual speed for this character / content. Reset at response.done.
    chars_recv_total = {"n": 0}
    SPEAKING_RATE_CHARS_PER_SEC = 17.0  # empirical Cedar FR ~16-20 (fallback)
    MIN_CHARS_FOR_ADAPTIVE = 60        # ~4s at 17 chars/s; lets one short reply land first
    # Track when we sent each schedule_switch, so we can measure how long
    # the browser took to send back the corresponding apply_switch.
    pending_schedule_ts: dict[str, float] = {}
    # Track the byte_offset of the FIRST raw chunk currently accumulated in the
    # DSP buffer. When feed() returns a processed block, this is the offset we
    # send to the browser (not the offset of the chunk that triggered the flush,
    # which would point ~240 ms too late).
    dsp_buffer_state = {"start_offset": None}
    # When the user clicks Interrompre, drop every audio/transcript event from
    # the current OpenAI response — they're already obsolete. Resets on the
    # next response.done.
    cancelled = {"v": False}

    async def relay_to_browser(event: dict) -> None:
        et = event.get("type", "")
        # After a cancel, OpenAI still finishes streaming the in-flight response.
        # Drop those tail events so the browser doesn't keep playing.
        if cancelled["v"] and et in (
            "response.output_audio.delta", "response.audio.delta",
            "response.audio_transcript.delta", "response.output_audio_transcript.delta",
        ):
            return
        if et in ("response.output_audio.delta", "response.audio.delta"):
            delta = event.get("delta")
            if delta:
                try:
                    raw_bytes = base64.b64decode(delta)
                    chunk_start_offset = audio_bytes_total["n"]
                    audio_bytes_total["n"] += len(raw_bytes)
                except Exception:
                    raw_bytes = None
                    chunk_start_offset = audio_bytes_total["n"]
                # DSP is now browser-side (SoundTouch AudioWorklet streaming):
                # pitch shift + tempo + vibrato + gain happen at playback time,
                # synchronised with the speaker, with no 250 ms buffering and
                # no glitches at profile changes. The server just relays raw
                # PCM and lets the browser apply the active perso's profile.
                async with send_lock:
                    await ws.send_json({"type": "audio.delta", "data": delta,
                                        "perso": active_perso["name"],
                                        "byte_offset": chunk_start_offset})
                await trace("audio_delta_emit",
                            chunk_raw_offset=chunk_start_offset,
                            buffer_start_offset=chunk_start_offset,
                            raw_size=len(raw_bytes) if raw_bytes is not None else 0,
                            processed_size=len(raw_bytes) if raw_bytes is not None else 0,
                            perso=active_perso["name"],
                            dsp=False)
        elif et in ("response.audio_transcript.delta", "response.output_audio_transcript.delta"):
            text = event.get("delta", "")
            transcript_buffer["text"] += text
            chars_recv_total["n"] += len(text)
            # Detection scope: persos who actually speak in the current scene
            # (if known via load_scene), otherwise fall back to all persos of the oeuvre.
            if scene_speakers:
                pool = list(scene_speakers)
            else:
                pool = list((current_oeuvre or {}).get("by_character_structured", {}).keys())
            # Rewrite phonetic spellings back to canonical names so the detector
            # can still match on annotation['nom'] after we pushed the phonetic
            # version to Cedar. Does not mutate the canonical transcript buffer.
            buffer_for_detect = _unphonetize(transcript_buffer["text"], current_annotations)
            new_perso, match_end_pos = detect_perso_with_pos(
                buffer_for_detect, current_annotations, all_persos=pool,
            )
            if new_perso and new_perso != active_perso.get("name"):
                # We used to schedule a switch via at_byte and let the browser
                # apply it when audio playback reached the byte. That doesn't
                # work: OpenAI streams ~5x faster than real-time, so the
                # browser buffers seconds of audio ahead; meanwhile the
                # transcript lags the audio by ~500 ms. Result: at_byte points
                # to a chunk scheduled 10-70 s in the AudioContext future, the
                # browser waits forever to apply, and Cedar has long since
                # moved on to another character.
                #
                # New strategy: when we detect a perso in the transcript, the
                # audio has already been emitted with the previous profile.
                # Apply the new perso IMMEDIATELY server-side (DSP for next
                # chunks + antennas now). We accept a ~500 ms lag between the
                # heard name and the actual switch — that's the transcript
                # latency. The right long-term fix is browser-side DSP, but
                # for now this is good enough to make antennas track perso
                # changes and pitch settle within ~500 ms of the heard name.
                prev_perso = active_perso.get("name")
                active_perso["name"] = new_perso
                active_perso["pending"] = None
                # Browser-side DSP picks up the new profile on perso.active —
                # nothing to flush server-side anymore.

                # Move antennas in a worker thread so the WS loop stays alive.
                if robot_enabled["on"] and robot is not None:
                    try:
                        logger.info("→ antenna call perso=%s mode=%s connected=%s",
                                    new_perso, robot.mode, robot._connected)
                        left, right = await asyncio.to_thread(
                            robot.set_perso_antennas, new_perso, current_annotations
                        )
                        logger.info("← antenna done perso=%s L=%.1f° R=%.1f° mode=%s",
                                    new_perso, left, right, robot.mode)
                        async with send_lock:
                            await ws.send_json({"type": "robot.pose",
                                                "perso": new_perso,
                                                "left_deg": left,
                                                "right_deg": right,
                                                "mode": robot.mode})
                    except Exception as exc:
                        logger.warning("antenna move failed perso=%s: %s", new_perso, exc, exc_info=True)

                async with send_lock:
                    await ws.send_json({"type": "perso.active", "perso": new_perso})

                # Adaptive rate logged for diagnostics, but no longer used to
                # compute at_byte.
                if (chars_recv_total["n"] >= MIN_CHARS_FOR_ADAPTIVE
                        and audio_bytes_total["n"] > 0):
                    rate_used = chars_recv_total["n"] / (audio_bytes_total["n"] / 48000)
                    rate_source = "adaptive"
                else:
                    rate_used = SPEAKING_RATE_CHARS_PER_SEC
                    rate_source = "fallback"

                logger.info(
                    "switch_now perso=%s ← %s (transcript pos=%d, rate=%.2f %s)",
                    new_perso, prev_perso, match_end_pos, rate_used, rate_source,
                )
                await trace("detect",
                            match_end_pos=match_end_pos,
                            new_perso=new_perso,
                            prev_perso=prev_perso,
                            audio_bytes_total=audio_bytes_total["n"],
                            chars_recv_total=chars_recv_total["n"],
                            rate_used=rate_used,
                            rate_source=rate_source,
                            transcript_len=len(transcript_buffer["text"]),
                            applied="immediate")
                return
            async with send_lock:
                await ws.send_json({"type": "transcript.delta", "data": text})
        elif et in (
            "conversation.item.input_audio_transcription.completed",
            "input_audio_buffer.transcription.completed",
        ):
            async with send_lock:
                await ws.send_json({"type": "user.transcript", "data": event.get("transcript", "")})
        elif et == "input_audio_buffer.speech_started":
            async with send_lock:
                await ws.send_json({"type": "speech.started"})
        elif et == "input_audio_buffer.speech_stopped":
            async with send_lock:
                await ws.send_json({"type": "speech.stopped"})
        elif et == "response.function_call_arguments.delta":
            cid = event.get("call_id")
            name = event.get("name") or event.get("item", {}).get("name") if isinstance(event.get("item"), dict) else None
            if cid:
                slot = pending_tool_calls.setdefault(cid, {"name": name, "args_buffer": ""})
                if name and not slot.get("name"):
                    slot["name"] = name
                slot["args_buffer"] += event.get("delta", "")
        elif et == "response.function_call_arguments.done":
            cid = event.get("call_id")
            name = event.get("name") or (pending_tool_calls.get(cid) or {}).get("name")
            args_str = event.get("arguments") or (pending_tool_calls.get(cid) or {}).get("args_buffer", "")
            pending_tool_calls.pop(cid, None)
            if cid and name and conteur:
                try:
                    args = json.loads(args_str) if args_str else {}
                except Exception:
                    args = {}
                result = _handle_tool_call(name, args, current_oeuvre, current_annotations)
                # When Cedar loads a specific scene, restrict perso detection
                # to the actual speakers of that scene (prevents false positives
                # like "Hippolyte" name mentioned in another perso's reply).
                if name in ("load_scene", "load_full_play") and current_oeuvre is not None:
                    scene_speakers.clear()
                    if name == "load_scene":
                        for sc in (current_oeuvre.get("actes") or []):
                            if str(sc.get("n")) != str(args.get("act")):
                                continue
                            for scene in sc.get("scenes", []):
                                if str(scene.get("n")) != str(args.get("scene")):
                                    continue
                                for b in scene.get("blocks", []):
                                    if b.get("type") == "sp" and b.get("who"):
                                        scene_speakers.add(b["who"])
                    else:
                        # full play: every speaker is allowed
                        for sc in (current_oeuvre.get("actes") or []):
                            for scene in sc.get("scenes", []):
                                for b in scene.get("blocks", []):
                                    if b.get("type") == "sp" and b.get("who"):
                                        scene_speakers.add(b["who"])
                    logger.info("scene_speakers set to: %s", scene_speakers)
                async with send_lock:
                    await ws.send_json({"type": "tool.call", "name": name, "args": args})
                await conteur.adapter.send_tool_result(cid, result)
        elif et == "response.done":
            # Flush any remaining DSP-buffered audio before announcing done
            try:
                tail = dsp_proc.flush()
                if tail:
                    emit_offset = dsp_buffer_state["start_offset"] or audio_bytes_total["n"]
                    dsp_buffer_state["start_offset"] = None
                    async with send_lock:
                        await ws.send_json({"type": "audio.delta",
                                            "data": base64.b64encode(tail).decode("ascii"),
                                            "perso": active_perso["name"],
                                            "byte_offset": emit_offset})
                    await trace("audio_delta_emit",
                                chunk_raw_offset=audio_bytes_total["n"],
                                buffer_start_offset=emit_offset,
                                raw_size=0,
                                processed_size=len(tail),
                                perso=active_perso["name"],
                                dsp=True,
                                reason="response_done_flush")
            except Exception:
                pass
            dsp_proc.reset()
            dsp_buffer_state["start_offset"] = None
            # Reset perso tracking BOTH name and pending (otherwise the pending
            # guard blocks the next switch). Keep antennas at their last position
            # so they don't "snap back" to 0 between turns of a continuous reading.
            await trace("response_done",
                        audio_bytes_total=audio_bytes_total["n"],
                        chars_recv_total=chars_recv_total["n"],
                        transcript_len=len(transcript_buffer["text"]),
                        pending_count=len(pending_schedule_ts))
            active_perso["name"] = None
            active_perso["pending"] = None
            transcript_buffer["text"] = ""
            audio_bytes_total["n"] = 0
            chars_recv_total["n"] = 0
            pending_schedule_ts.clear()
            cancelled["v"] = False
            async with send_lock:
                await ws.send_json({"type": "response.done"})
        elif et == "error":
            err = event.get("error", {})
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            async with send_lock:
                await ws.send_json({"type": "error", "error": msg})

    try:
        while True:
            msg = await ws.receive()
            if "text" in msg and msg["text"] is not None:
                data = json.loads(msg["text"])
                mtype = data.get("type")

                if mtype == "start":
                    oeuvre_id = data["oeuvre_id"]
                    logger.info("WS start received: oeuvre_id=%s", oeuvre_id)
                    settings = data.get("settings", {})
                    dsp_enabled["on"] = bool(settings.get("dsp_enabled", True))
                    robot_enabled["on"] = bool(settings.get("robot_enabled", True)) and ALLOW_ROBOT
                    if oeuvre_id.startswith("book:"):
                        current_oeuvre = _book_as_oeuvre(_load_book(oeuvre_id.split(":", 1)[1]))
                    else:
                        current_oeuvre = await fetch_oeuvre(oeuvre_id)
                    annotations = load_annotations(oeuvre_id)
                    current_annotations = annotations

                    if robot_enabled["on"] and robot is None:
                        try:
                            robot = await asyncio.to_thread(RobotController, True)
                            async with send_lock:
                                await ws.send_json({"type": "robot.ready", "mode": robot.mode})
                        except Exception as exc:
                            logger.warning("Robot init failed: %s", exc)

                    conteur = Conteur(api_key=OPENAI_KEY, on_event=relay_to_browser)
                    await conteur.start(
                        oeuvre=current_oeuvre,
                        annotations=annotations,
                        model=settings.get("model", DEFAULT_MODEL),
                        voice=settings.get("voice", DEFAULT_VOICE),
                        speed=float(settings.get("speed", 0.92)),
                        reasoning_effort=settings.get("reasoning_effort", "medium"),
                        enable_preambles=bool(settings.get("enable_preambles", True)),
                        tools=CONTEUR_TOOLS,
                    )
                    async with send_lock:
                        await ws.send_json({
                            "type": "session.ready",
                            "n_actes": current_oeuvre.get("n_actes"),
                            "n_scenes": current_oeuvre.get("n_scenes"),
                        })

                elif mtype == "text" and conteur is not None:
                    await conteur.push_user_text(data["text"])

                elif mtype == "push_scene" and conteur is not None:
                    scene_text = _apply_pronunciations(data["scene_text"], current_annotations)
                    await conteur.push_oeuvre_text(scene_text)

                elif mtype == "speed" and conteur is not None:
                    await conteur.update_speed(float(data["speed"]))

                elif mtype == "cancel" and conteur is not None:
                    # Mark cancelled FIRST so any in-flight audio.delta from
                    # OpenAI is dropped by relay_to_browser. Then tell the
                    # browser to kill its locally-buffered audio. Finally ask
                    # OpenAI to cancel — its tail events may still arrive but
                    # we'll silently drop them.
                    cancelled["v"] = True
                    dsp_proc.reset()
                    dsp_buffer_state["start_offset"] = None
                    async with send_lock:
                        await ws.send_json({"type": "audio.cancel"})
                    await conteur.cancel()
                    await trace("cancel_recv",
                                audio_bytes_total=audio_bytes_total["n"],
                                pending_count=len(pending_schedule_ts))
                    # Pre-emptively reset perso state so any late detection
                    # doesn't fire a switch on the cancelled tour.
                    active_perso["name"] = None
                    active_perso["pending"] = None
                    transcript_buffer["text"] = ""
                    audio_bytes_total["n"] = 0
                    chars_recv_total["n"] = 0
                    pending_schedule_ts.clear()

                elif mtype == "apply_switch" and conteur is not None and current_oeuvre is not None:
                    # Browser tells us audio playback has reached the byte_offset
                    # where this perso should become active. Apply now — perfectly
                    # in sync with what the user is hearing.
                    new_perso = (data.get("perso") or "").strip()
                    schedule_ts = pending_schedule_ts.pop(new_perso, None)
                    ms_since_schedule = int((time.time() - schedule_ts) * 1000) if schedule_ts else None
                    await trace("apply_switch_recv",
                                perso=new_perso,
                                ms_since_schedule=ms_since_schedule)
                    if new_perso and new_perso != active_perso.get("name"):
                        prev_perso = active_perso.get("name")
                        active_perso["name"] = new_perso
                        # Clear the pending guard so the same perso can be
                        # re-scheduled next time they speak in this response.
                        if active_perso.get("pending") == new_perso:
                            active_perso["pending"] = None
                        if robot_enabled["on"] and robot is not None:
                            try:
                                left, right = await asyncio.to_thread(
                                    robot.set_perso_antennas, new_perso, current_annotations
                                )
                                async with send_lock:
                                    await ws.send_json({"type": "robot.pose", "perso": new_perso,
                                                        "left_deg": left, "right_deg": right,
                                                        "mode": robot.mode})
                            except Exception as exc:
                                logger.warning("apply_switch robot move failed: %s", exc)
                        # Flush any DSP buffer with the previous profile so the new
                        # one applies cleanly to subsequent chunks.
                        try:
                            tail = dsp_proc.flush()
                            if tail:
                                emit_offset = dsp_buffer_state["start_offset"] or audio_bytes_total["n"]
                                dsp_buffer_state["start_offset"] = None
                                async with send_lock:
                                    await ws.send_json({"type": "audio.delta",
                                                        "data": base64.b64encode(tail).decode("ascii"),
                                                        "perso": new_perso,
                                                        "byte_offset": emit_offset})
                                await trace("audio_delta_emit",
                                            chunk_raw_offset=audio_bytes_total["n"],
                                            buffer_start_offset=emit_offset,
                                            raw_size=0,
                                            processed_size=len(tail),
                                            perso=new_perso,
                                            dsp=True,
                                            reason="apply_switch_flush")
                        except Exception:
                            pass
                        async with send_lock:
                            await ws.send_json({"type": "perso.active", "perso": new_perso})

                elif mtype == "reload_prompt" and conteur is not None and current_oeuvre is not None:
                    annotations = load_annotations(current_oeuvre["id"])
                    current_annotations = annotations
                    new_prompt = build_system_prompt(current_oeuvre, annotations)
                    await conteur.adapter.update_instructions(new_prompt)
                    async with send_lock:
                        await ws.send_json({"type": "prompt.reloaded"})

                elif mtype == "dsp_toggle":
                    # Flush whatever the DSP buffer holds with the current profile
                    # before we change the toggle, otherwise those ~250 ms of audio
                    # would be silently dropped at reset() and the byte offset
                    # accounting would skew.
                    try:
                        tail = dsp_proc.flush()
                        if tail:
                            emit_offset = dsp_buffer_state["start_offset"] or audio_bytes_total["n"]
                            async with send_lock:
                                await ws.send_json({"type": "audio.delta",
                                                    "data": base64.b64encode(tail).decode("ascii"),
                                                    "perso": active_perso["name"],
                                                    "byte_offset": emit_offset})
                    except Exception:
                        pass
                    dsp_buffer_state["start_offset"] = None
                    dsp_enabled["on"] = bool(data.get("on", True))
                    async with send_lock:
                        await ws.send_json({"type": "dsp.state", "on": dsp_enabled["on"]})

                elif mtype == "robot_toggle":
                    robot_enabled["on"] = bool(data.get("on", True))
                    if not robot_enabled["on"] and robot is not None:
                        try:
                            await asyncio.to_thread(robot.reset_pose)
                        except Exception:
                            pass
                    async with send_lock:
                        await ws.send_json({"type": "robot.state", "on": robot_enabled["on"]})

                elif mtype == "stop" and conteur is not None:
                    await conteur.stop()
                    conteur = None
                    current_oeuvre = None
                    async with send_lock:
                        await ws.send_json({"type": "session.stopped"})

            elif "bytes" in msg and msg["bytes"] is not None and conteur is not None:
                await conteur.send_audio_24k(msg["bytes"])

    except WebSocketDisconnect:
        logger.info("WS disconnected")
    except Exception as exc:
        logger.exception("WS error: %s", exc)
        try:
            await ws.send_json({"type": "error", "error": str(exc)})
        except Exception:
            pass
    finally:
        if conteur is not None:
            try:
                await conteur.stop()
            except Exception:
                pass
        if robot is not None:
            try:
                await asyncio.to_thread(robot.shutdown)
            except Exception:
                pass


def _unphonetize(text: str, annotations: dict | None) -> str:
    """Inverse of _apply_pronunciations: rewrite phonetic forms back to canonical
    speaker names, so the perso detector can still match on the original name
    even after the text-to-speech version was substituted upstream.

    Operates on a copy — never mutates the canonical transcript buffer.
    """
    if not annotations:
        return text
    prons = annotations.get("prononciations") or {}
    if not prons:
        return text
    for word, phonetic in prons.items():
        if not word or not phonetic:
            continue
        text = text.replace(phonetic, word)
        if phonetic.upper() != phonetic:
            text = text.replace(phonetic.upper(), word.upper())
    return text


def _apply_pronunciations(text: str, annotations: dict | None) -> str:
    """Rewrite the text with phonetic spellings before Cedar reads it.

    Cedar's "Reference Pronunciations" section in the system prompt is often
    ignored mid-stream, especially in alexandrins where Cedar locks onto the
    classical pronunciation. Substituting the word in the text itself bypasses
    Cedar's prior — it reads what we wrote, character by character.

    The annotations dict keys are the canonical forms ("Aricie"), values are
    the phonetic spellings ("A-ri-si"). Done case-sensitive (Cedar respects
    capitalization), and both raw and uppercase versions are substituted so
    "ARICIE." (as a speaker tag in DraCor format) is also rewritten.
    """
    if not annotations:
        return text
    prons = annotations.get("prononciations") or {}
    if not prons:
        return text
    for word, phonetic in prons.items():
        if not word or not phonetic:
            continue
        text = text.replace(word, phonetic)
        # Also rewrite the all-caps speaker-tag form ("ARICIE." → "A-RI-SI.")
        if word.upper() != word:
            text = text.replace(word.upper(), phonetic.upper())
    return text


def _handle_tool_call(name: str, args: dict, oeuvre: dict | None,
                     annotations: dict | None = None) -> dict:
    if not oeuvre:
        return {"error": "no oeuvre loaded"}
    if name == "load_scene":
        text = get_scene_text(oeuvre, args.get("act"), args.get("scene"))
        if not text:
            return {"error": f"Scene {args.get('act')}.{args.get('scene')} not found"}
        text = _apply_pronunciations(text, annotations)
        return {"act": args.get("act"), "scene": args.get("scene"), "text": text}
    if name == "load_full_play":
        text = _apply_pronunciations(oeuvre.get("text_complet", ""), annotations)
        return {"text": text}
    if name == "list_scenes":
        return {"scenes": list_scenes(oeuvre)}
    return {"error": f"unknown tool {name}"}
