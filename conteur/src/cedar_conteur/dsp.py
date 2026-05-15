"""Post-Cedar DSP pipeline.

Applies per-character voice modulation on top of Cedar's audio:
- pitch_shift (semitones, -3..+3)
- formant_shift (relative, -2..+2) — NOTE: not natively supported by RubberBand,
  approximated by pitch+time compensation
- speed (0.7..1.3)
- vibrato (Hz, depth 0..0.15) for trembling voices
- gain_db (-6..+3)

PCM int16 little-endian @ 24kHz mono in, same out. Latency ~30-50ms per chunk.
"""

import io
import logging
from typing import Any

import numpy as np
import pyrubberband as pyrb

logger = logging.getLogger(__name__)

SAMPLE_RATE = 24000

DEFAULT_PROFILE = {
    "pitch_shift": 0.0,
    "speed": 1.0,
    "vibrato_hz": 0.0,
    "vibrato_depth": 0.0,
    "gain_db": 0.0,
}


_CROSSFADE_MS = 5
_CROSSFADE_SAMPLES = int(SAMPLE_RATE * _CROSSFADE_MS / 1000)


def apply_profile(pcm_bytes: bytes, profile: dict[str, Any], prev_tail: bytes = b"") -> bytes:
    """Apply DSP to a PCM chunk.

    `prev_tail`: last few ms of the previously-processed chunk, used to crossfade
    the start of this output and avoid pops between consecutive DSP'd chunks.
    """
    if not pcm_bytes:
        return pcm_bytes
    pitch = float(profile.get("pitch_shift", 0) or 0)
    speed = float(profile.get("speed", 1.0) or 1.0)
    vibrato_hz = float(profile.get("vibrato_hz", 0) or 0)
    vibrato_depth = float(profile.get("vibrato_depth", 0) or 0)
    gain_db = float(profile.get("gain_db", 0) or 0)

    if (pitch == 0 and speed == 1.0 and vibrato_hz == 0 and gain_db == 0):
        return pcm_bytes

    try:
        samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        # Use crispness=6 (better for voice, fewer artifacts than default 5).
        # Available in rubberband CLI via -c, passed through pyrubberband.
        rbargs = {"-c": "6"}
        if pitch != 0:
            samples = pyrb.pitch_shift(samples, SAMPLE_RATE, n_steps=pitch, rbargs=rbargs)
        if speed != 1.0:
            samples = pyrb.time_stretch(samples, SAMPLE_RATE, rate=speed, rbargs=rbargs)
        if vibrato_hz > 0 and vibrato_depth > 0:
            t = np.arange(len(samples)) / SAMPLE_RATE
            mod = 1.0 + vibrato_depth * np.sin(2 * np.pi * vibrato_hz * t)
            samples = samples * mod
        if gain_db != 0:
            samples = samples * (10.0 ** (gain_db / 20.0))

        # Crossfade start with previous tail to mask boundary discontinuities
        if prev_tail and len(samples) > _CROSSFADE_SAMPLES:
            tail_samples = np.frombuffer(prev_tail, dtype=np.int16).astype(np.float32) / 32768.0
            n = min(_CROSSFADE_SAMPLES, len(tail_samples), len(samples))
            fade_in = np.linspace(0, 1, n, dtype=np.float32)
            fade_out = 1 - fade_in
            samples[:n] = samples[:n] * fade_in + tail_samples[-n:] * fade_out

        samples = np.clip(samples, -1.0, 1.0)
        out = (samples * 32767).astype(np.int16).tobytes()
        return out
    except Exception as exc:
        logger.warning("DSP failed (profile=%s): %s — returning raw audio", profile, exc)
        return pcm_bytes


class DSPBufferedProcessor:
    """Accumulates incoming audio chunks into a min-size buffer before DSP-ing,
    so that pyrubberband has enough samples to produce clean output without scratch.
    """

    MIN_BUFFER_BYTES = SAMPLE_RATE * 2 * 250 // 1000  # 250ms of PCM16 mono = 12000 bytes

    def __init__(self) -> None:
        self._buf = bytearray()
        self._current_profile: dict[str, Any] | None = None
        self._prev_tail = b""

    def feed(self, pcm_bytes: bytes, profile: dict[str, Any]) -> bytes:
        """Returns processed audio ready to send (may be empty if still buffering)."""
        if not pcm_bytes:
            return b""

        # If profile is default (no DSP), passthrough immediately and flush buffer
        is_default = (
            (profile.get("pitch_shift", 0) or 0) == 0
            and (profile.get("speed", 1.0) or 1.0) == 1.0
            and (profile.get("vibrato_hz", 0) or 0) == 0
            and (profile.get("gain_db", 0) or 0) == 0
        )
        if is_default:
            flushed = self.flush()
            return flushed + pcm_bytes

        # Profile changed → flush previous profile's buffer first
        if self._current_profile is not None and self._current_profile != profile:
            flushed = self.flush()
            self._current_profile = profile
            self._buf.extend(pcm_bytes)
            return flushed

        self._current_profile = profile
        self._buf.extend(pcm_bytes)
        if len(self._buf) >= self.MIN_BUFFER_BYTES:
            return self.flush()
        return b""

    def flush(self) -> bytes:
        if not self._buf or self._current_profile is None:
            self._buf.clear()
            return b""
        out = apply_profile(bytes(self._buf), self._current_profile, self._prev_tail)
        # Keep the last 5ms as crossfade tail for the next call
        if len(out) >= _CROSSFADE_SAMPLES * 2:
            self._prev_tail = out[-_CROSSFADE_SAMPLES * 2:]
        else:
            self._prev_tail = b""
        self._buf.clear()
        return out

    def reset(self) -> None:
        self._buf.clear()
        self._current_profile = None
        self._prev_tail = b""


def profile_for_perso(perso_name: str, annotations: dict[str, Any]) -> dict[str, Any]:
    if not perso_name:
        return DEFAULT_PROFILE.copy()
    target = _strip_accents(perso_name).strip().upper()
    for p in annotations.get("personnages", []) or []:
        name = _strip_accents(p.get("nom") or "").strip().upper()
        if name == target:
            return {
                "pitch_shift": p.get("pitch_shift", 0) or 0,
                "speed": p.get("speed_hint", 1.0) or 1.0,
                "vibrato_hz": p.get("vibrato_hz", 0) or 0,
                "vibrato_depth": p.get("vibrato_depth", 0) or 0,
                "gain_db": p.get("gain_db", 0) or 0,
            }
    return DEFAULT_PROFILE.copy()


import unicodedata


_LIGATURE_MAP = str.maketrans({
    "Œ": "OE", "œ": "oe", "Æ": "AE", "æ": "ae",
})


def _strip_accents(s: str) -> str:
    s = s.translate(_LIGATURE_MAP)
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


_PERSO_NAME_RE_CACHE: dict[str, "re.Pattern"] = {}


def detect_perso_with_pos(
    transcript_buffer: str,
    annotations: dict[str, Any],
    all_persos: list[str] | None = None,
) -> tuple[str | None, int]:
    """Same as detect_perso but also returns the END position of the match
    in the FULL transcript_buffer (not the tail). Used for audio/transcript sync.
    """
    if not transcript_buffer:
        return None, 0
    name = _detect_perso_impl(transcript_buffer, annotations, all_persos, want_pos=True)
    return name


def detect_perso(
    transcript_buffer: str,
    annotations: dict[str, Any],
    all_persos: list[str] | None = None,
) -> str | None:
    """Return the currently-speaking character based on the transcript.

    `annotations` provides DSP profiles for known characters.
    `all_persos` is the full character list from DraCor — we detect ANY of them
    in the transcript, not only the annotated ones, so unannotated characters
    (e.g. ISMENE, PANOPE) still trigger a switch out of the previous character's
    DSP profile (back to narrator).
    """
    res = _detect_perso_impl(transcript_buffer, annotations, all_persos, want_pos=False)
    return res


def _detect_perso_impl(transcript_buffer, annotations, all_persos, want_pos):
    import re

    if not transcript_buffer:
        return (None, 0) if want_pos else None
    tail_start = max(0, len(transcript_buffer) - 500)
    tail_norm = _strip_accents(transcript_buffer[tail_start:]).upper()
    names: dict[str, str] = {}
    for p in annotations.get("personnages", []) or []:
        nom = (p.get("nom") or "").strip()
        if nom:
            names[_strip_accents(nom).upper()] = nom
    for nom in all_persos or []:
        if nom and nom.strip():
            names.setdefault(_strip_accents(nom).upper(), nom)

    if not names:
        return (None, 0) if want_pos else None

    best_pos = -1
    best = None
    for caps_norm, original in names.items():
        pat = _PERSO_NAME_RE_CACHE.get(caps_norm)
        if pat is None:
            escaped = re.escape(caps_norm)
            # STRICT: the name must be alone on its line (possibly with `.`,
            # `:`, or a parenthetical didascalie), nothing else after.
            # This blocks false positives when Cedar reads a vocative or
            # mentions a character name mid-reply (e.g. "...la mort de Thésée.\n
            # Préparez-vous...").
            # Accepted shapes:
            #   PERSO\n              (bare)
            #   PERSO.\n             (classic DraCor)
            #   PERSO :\n            (with colon)
            #   PERSO (didascalie)\n (with parenthetical)
            # Each followed by an actual line break or end of string.
            # Reply header detection. Cedar's transcript often comes back as
            # a single line (no preserved `\n\n` from the pushed DraCor text),
            # so we accept BOTH formats:
            #
            #   Multi-line DraCor:  "...vers.\n\nPERSO.\n  texte"
            #   Mono-line streamed: "...vers ? PERSO. Texte"
            #
            # The trick to block false positives ("Hippolyte" mentioned in
            # someone else's reply) is to REQUIRE the dot/colon after the name
            # AND require a strong separator BEFORE (start, \n\n, or [.?!] +
            # whitespace = end of previous sentence). Plain comma/space + name
            # won't match.
            pat = re.compile(
                rf"(?:\A|\n\n+|[.?!…\d]\s+){escaped}[ \t]*(?:\([^)]*\))?[ \t]*[.:][ \t]*(?:\n|\Z|[A-ZÉÈÊÀÂÎÏÔÛÙÇ])",
            )
            _PERSO_NAME_RE_CACHE[caps_norm] = pat
        for m in pat.finditer(tail_norm):
            end_pos = m.end()
            if end_pos > best_pos:
                best_pos = end_pos
                best = original
    if want_pos:
        full_pos = tail_start + best_pos if best_pos >= 0 else 0
        return best, full_pos
    return best
