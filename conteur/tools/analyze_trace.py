#!/usr/bin/env python3
"""Analyse d'une trace JSONL du banc de test conteur Cedar.

Lit un fichier JSONL (un event par ligne) produit par une session de lecture
instrumentée et émet un rapport markdown sur la qualité de la synchronisation
audio/transcript.

Usage:
    python analyze_trace.py [PATH] [--json]
    # PATH facultatif. Par défaut, le plus récent /tmp/cedar-conteur-trace-*.jsonl.
    # --json : émet aussi un bloc JSON compact des métriques (CI).

Schéma des events (clés communes : ts (epoch float), src ("server"|"browser"), kind) :

  server.audio_delta_emit   chunk_raw_offset, buffer_start_offset, raw_size,
                            processed_size, perso, dsp (bool)
  server.detect             match_end_pos, start_pos_approx, new_perso,
                            audio_bytes_total, chars_recv_total, rate_used,
                            rate_source, at_byte_before, at_byte_after,
                            transcript_len  (two-phase: narrator + perso)
  server.cancel_recv        audio_bytes_total, pending_count
  server.apply_switch_recv  perso, ms_since_schedule (int|null)
  server.response_done      audio_bytes_total, chars_recv_total,
                            transcript_len, pending_count

  browser.audio_delta_browser  byteOffset, endByte, startTime, duration,
                               audioCtxCurrentTime
  browser.schedule_switch_recv perso, at_byte
  browser.schedule_lookup      perso, at_byte, chunk_found (bool)
                               [+ playTime, currentTime, delayMs] si found
                               [+ gap_bytes, gap_direction, chunkSchedule_size] sinon
  browser.apply_switch_emit    perso, currentTime, scheduled_playTime
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


# --------- ANSI couleur ---------------------------------------------------
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
RESET = "\033[0m"


def colorize(text: str, color: str, use_color: bool) -> str:
    if not use_color:
        return text
    return f"{color}{text}{RESET}"


# --------- Modèles --------------------------------------------------------
@dataclass
class Metrics:
    total_events: int = 0
    kinds: Counter = field(default_factory=Counter)
    duration_s: float = 0.0
    response_done_count: int = 0

    # adaptive
    adaptive_rates: list[float] = field(default_factory=list)
    fallback_count: int = 0

    # schedule lookups
    lookup_found: int = 0
    lookup_missed: int = 0
    lookup_gaps: list[int] = field(default_factory=list)
    lookup_delay_ms: list[float] = field(default_factory=list)

    # apply_switch_recv ms_since_schedule
    apply_switch_ms: list[int] = field(default_factory=list)

    # buffer offsets coherence
    dsp_emit_count: int = 0
    dsp_inverted: int = 0
    accumulated_per_block: list[int] = field(default_factory=list)

    # silences > 200 ms
    silences: list[tuple[float, float]] = field(default_factory=list)

    # transitions perso end-to-end
    perso_transitions: list[dict[str, Any]] = field(default_factory=list)


# --------- Chargement -----------------------------------------------------
def load_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, raw in enumerate(f, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                events.append(json.loads(raw))
            except json.JSONDecodeError as exc:
                print(
                    f"[warn] ligne {line_no} invalide : {exc}", file=sys.stderr
                )
    events.sort(key=lambda e: e.get("ts", 0.0))
    return events


def autodetect_latest() -> Path | None:
    matches = glob.glob("/tmp/cedar-conteur-trace-*.jsonl")
    if not matches:
        return None
    matches.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return Path(matches[0])


# --------- Calcul ---------------------------------------------------------
def compute_metrics(events: list[dict[str, Any]]) -> Metrics:
    m = Metrics()
    m.total_events = len(events)
    if not events:
        return m

    ts_first = events[0].get("ts", 0.0)
    ts_last = events[-1].get("ts", 0.0)
    m.duration_s = max(0.0, ts_last - ts_first)

    last_browser_audio_ts: float | None = None

    # Pour transitions perso : on a besoin de matcher detect (serveur) avec
    # apply_switch_emit (browser) sur le même perso, dans l'ordre.
    detects_by_perso: dict[str, list[dict[str, Any]]] = defaultdict(list)
    applies_by_perso: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for ev in events:
        kind = ev.get("kind", "")
        src = ev.get("src", "")
        m.kinds[f"{src}.{kind}" if src else kind] += 1

        if kind == "response_done":
            m.response_done_count += 1

        elif kind == "detect":
            rate_source = ev.get("rate_source")
            rate_used = ev.get("rate_used")
            if rate_source == "adaptive" and isinstance(rate_used, (int, float)):
                m.adaptive_rates.append(float(rate_used))
            elif rate_source == "fallback":
                m.fallback_count += 1
            perso = ev.get("new_perso")
            if perso:
                detects_by_perso[perso].append(ev)

        elif kind == "apply_switch_recv":
            ms = ev.get("ms_since_schedule")
            if isinstance(ms, int):
                m.apply_switch_ms.append(ms)

        elif kind == "schedule_lookup":
            if ev.get("chunk_found") is True:
                m.lookup_found += 1
                delay = ev.get("delayMs")
                if isinstance(delay, (int, float)):
                    m.lookup_delay_ms.append(float(delay))
            else:
                m.lookup_missed += 1
                gap = ev.get("gap_bytes")
                direction = ev.get("gap_direction")
                if isinstance(gap, int):
                    # Signed gap: + for after_window (waiting on chunk to arrive),
                    # - for before_window (chunk got trimmed before lookup ran).
                    signed = -gap if direction == "before_window" else gap
                    m.lookup_gaps.append(signed)

        elif kind == "audio_delta_emit":
            if ev.get("dsp") is True:
                m.dsp_emit_count += 1
                buf_start = ev.get("buffer_start_offset")
                chunk_off = ev.get("chunk_raw_offset")
                raw_size = ev.get("raw_size") or 0
                if (
                    isinstance(buf_start, int)
                    and isinstance(chunk_off, int)
                ):
                    if buf_start > chunk_off:
                        m.dsp_inverted += 1
                    if raw_size > 0:
                        # nombre de chunks accumulés avant émission
                        accumulated = max(
                            1, (chunk_off - buf_start) // max(1, raw_size) + 1
                        )
                        m.accumulated_per_block.append(accumulated)

        elif kind == "audio_delta_browser":
            ts = ev.get("ts")
            if isinstance(ts, (int, float)):
                if last_browser_audio_ts is not None:
                    gap = ts - last_browser_audio_ts
                    if gap > 0.200:
                        m.silences.append((last_browser_audio_ts, ts))
                last_browser_audio_ts = ts

        elif kind == "apply_switch_emit":
            perso = ev.get("perso")
            if perso:
                applies_by_perso[perso].append(ev)

    # Transitions perso bout-en-bout : appariement chronologique par perso.
    for perso, detects in detects_by_perso.items():
        applies = applies_by_perso.get(perso, [])
        # Apparie chaque detect avec le 1er apply_switch_emit postérieur non utilisé.
        used = [False] * len(applies)
        for d in detects:
            d_ts = d.get("ts")
            if not isinstance(d_ts, (int, float)):
                continue
            for i, a in enumerate(applies):
                if used[i]:
                    continue
                a_ts = a.get("ts")
                if not isinstance(a_ts, (int, float)):
                    continue
                if a_ts >= d_ts:
                    used[i] = True
                    m.perso_transitions.append(
                        {
                            "perso": perso,
                            "detect_ts": d_ts,
                            "apply_ts": a_ts,
                            "delay_ms": int(round((a_ts - d_ts) * 1000)),
                            "rate_used": d.get("rate_used"),
                            "rate_source": d.get("rate_source"),
                        }
                    )
                    break

    return m


# --------- Formatage ------------------------------------------------------
def distribution(values: Iterable[float | int]) -> dict[str, float] | None:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    vals_sorted = sorted(vals)
    n = len(vals_sorted)
    p95_idx = max(0, min(n - 1, int(round(0.95 * (n - 1)))))
    return {
        "n": n,
        "min": vals_sorted[0],
        "median": statistics.median(vals_sorted),
        "max": vals_sorted[-1],
        "p95": vals_sorted[p95_idx],
        "mean": statistics.fmean(vals_sorted),
    }


def fmt_dist(d: dict[str, float] | None, unit: str = "") -> str:
    if not d:
        return "_aucune donnée_"
    u = f" {unit}" if unit else ""
    return (
        f"n={d['n']}, min={d['min']:.1f}{u}, médiane={d['median']:.1f}{u}, "
        f"moyenne={d['mean']:.1f}{u}, p95={d['p95']:.1f}{u}, max={d['max']:.1f}{u}"
    )


def render_markdown(m: Metrics, path: Path, use_color: bool) -> str:
    lines: list[str] = []
    lines.append(f"# Rapport trace Cedar — {path.name}")
    lines.append("")
    lines.append(f"Chemin : `{path}`")
    lines.append(f"Events totaux : **{m.total_events}**")
    lines.append(f"Durée session : **{m.duration_s:.2f} s**")
    lines.append(f"Tours `response.done` : **{m.response_done_count}**")
    lines.append("")

    # 1. Vue d'ensemble
    lines.append("## 1. Vue d'ensemble")
    lines.append("")
    lines.append("| Kind | Count |")
    lines.append("|------|------:|")
    for kind, count in sorted(m.kinds.items(), key=lambda x: -x[1]):
        lines.append(f"| `{kind}` | {count} |")
    lines.append("")

    # 2. Adaptive rate
    lines.append("## 2. Adaptive rate (chars/sec)")
    lines.append("")
    dist_rates = distribution(m.adaptive_rates)
    if dist_rates:
        lines.append(f"- Distribution adaptive : {fmt_dist(dist_rates, 'c/s')}")
        med = dist_rates["median"]
        if 16.0 <= med <= 22.0:
            verdict = colorize("OK (médiane dans [16, 22])", GREEN, use_color)
        else:
            verdict = colorize(
                f"hors plage attendue 16-22 c/s", YELLOW, use_color
            )
        lines.append(f"- Verdict : {verdict}")
    else:
        lines.append("- Aucun event `detect` avec `rate_source=adaptive`.")
    lines.append(f"- Fallbacks (`rate_source=fallback`, taux 17.0) : **{m.fallback_count}**")
    lines.append("")

    # 3. Schedule lookup health
    lines.append("## 3. Schedule lookup health")
    lines.append("")
    total_lookups = m.lookup_found + m.lookup_missed
    if total_lookups > 0:
        pct_found = 100.0 * m.lookup_found / total_lookups
        pct_missed = 100.0 * m.lookup_missed / total_lookups
        lines.append(f"- Total lookups : **{total_lookups}**")
        lines.append(
            f"- `chunk_found:true` : **{m.lookup_found}** ({pct_found:.1f} %)"
        )
        lines.append(
            f"- `chunk_found:false` : **{m.lookup_missed}** ({pct_missed:.1f} %)"
        )
        if m.lookup_missed > 0:
            gap_dist = distribution(m.lookup_gaps)
            lines.append(f"- Gap (octets) sur misses : {fmt_dist(gap_dist, 'B')}")
            positives = [g for g in m.lookup_gaps if g > 0]
            negatives = [g for g in m.lookup_gaps if g < 0]
            lines.append(
                f"  - positifs (at_byte futur) : {len(positives)}, "
                f"négatifs (at_byte passé) : {len(negatives)}"
            )
        if m.lookup_delay_ms:
            lines.append(
                f"- delayMs sur hits : {fmt_dist(distribution(m.lookup_delay_ms), 'ms')}"
            )
        if pct_missed > 20.0:
            verdict = colorize(
                f"DRIFT PERSISTANT ({pct_missed:.1f} % misses > 20 %)",
                RED,
                use_color,
            )
        elif pct_missed > 5.0:
            verdict = colorize(
                f"léger drift ({pct_missed:.1f} % misses)", YELLOW, use_color
            )
        else:
            verdict = colorize(
                f"OK ({pct_missed:.1f} % misses)", GREEN, use_color
            )
        lines.append(f"- Verdict : {verdict}")
    else:
        lines.append("- Aucun event `schedule_lookup`.")
    lines.append("")

    # 4. apply_switch ms_since_schedule
    lines.append("## 4. Délai apply_switch (ms_since_schedule)")
    lines.append("")
    dist_apply = distribution(m.apply_switch_ms)
    if dist_apply:
        lines.append(f"- Distribution : {fmt_dist(dist_apply, 'ms')}")
        med = dist_apply["median"]
        if med > 1000:
            verdict = colorize(
                f"MAUVAIS (médiane {med:.0f} ms > 1000 ms)", RED, use_color
            )
        elif med > 500:
            verdict = colorize(
                f"moyen (médiane {med:.0f} ms)", YELLOW, use_color
            )
        else:
            verdict = colorize(
                f"OK (médiane {med:.0f} ms)", GREEN, use_color
            )
        lines.append(f"- Verdict : {verdict}")
    else:
        lines.append("- Aucun `apply_switch_recv` avec `ms_since_schedule` non null.")
    lines.append("")

    # 5. Cohérence buffer_start_offset
    lines.append("## 5. Cohérence buffer_start_offset (DSP)")
    lines.append("")
    if m.dsp_emit_count > 0:
        lines.append(f"- `audio_delta_emit` DSP : **{m.dsp_emit_count}**")
        lines.append(
            f"- Inversions `buffer_start_offset > chunk_raw_offset` : "
            f"**{m.dsp_inverted}**"
        )
        if m.accumulated_per_block:
            dist_acc = distribution(m.accumulated_per_block)
            lines.append(
                f"- Chunks accumulés par bloc : {fmt_dist(dist_acc, 'chunks')}"
            )
        if m.dsp_inverted == 0:
            verdict = colorize("OK (aucune inversion)", GREEN, use_color)
        else:
            verdict = colorize(
                f"BUG ({m.dsp_inverted} inversions)", RED, use_color
            )
        lines.append(f"- Verdict : {verdict}")
    else:
        lines.append("- Aucun event `audio_delta_emit` DSP.")
    lines.append("")

    # 6. Silences
    lines.append("## 6. Silences (trous > 200 ms entre `audio_delta_browser`)")
    lines.append("")
    if m.silences:
        gaps = [b - a for a, b in m.silences]
        lines.append(f"- Trous détectés : **{len(m.silences)}**")
        lines.append(f"- Distribution durée trous : {fmt_dist(distribution(gaps), 's')}")
        # Top 5 trous
        top = sorted(zip(m.silences, gaps), key=lambda x: -x[1])[:5]
        if top:
            lines.append("")
            lines.append("| # | début (ts) | fin (ts) | durée (ms) |")
            lines.append("|--:|-----------:|---------:|-----------:|")
            for i, ((a, b), g) in enumerate(top, 1):
                lines.append(f"| {i} | {a:.3f} | {b:.3f} | {g*1000:.0f} |")
    else:
        lines.append("- Aucun silence > 200 ms.")
    lines.append("")

    # 7. Transitions perso bout-en-bout
    lines.append("## 7. Transitions perso bout-en-bout (detect → apply_switch_emit)")
    lines.append("")
    if m.perso_transitions:
        delays = [t["delay_ms"] for t in m.perso_transitions]
        dist_t = distribution(delays)
        lines.append(
            f"- Transitions appariées : **{len(m.perso_transitions)}**"
        )
        lines.append(f"- Distribution délai : {fmt_dist(dist_t, 'ms')}")
        lines.append("")
        lines.append("| Perso | detect_ts | apply_ts | délai (ms) | rate | source |")
        lines.append("|-------|----------:|---------:|-----------:|-----:|--------|")
        for t in m.perso_transitions:
            rate = (
                f"{t['rate_used']:.1f}"
                if isinstance(t["rate_used"], (int, float))
                else "—"
            )
            lines.append(
                f"| {t['perso']} | {t['detect_ts']:.3f} | "
                f"{t['apply_ts']:.3f} | {t['delay_ms']} | {rate} | "
                f"{t.get('rate_source') or '—'} |"
            )
        med = dist_t["median"]
        if 300 <= med <= 700:
            verdict = colorize(
                f"OK (médiane {med:.0f} ms dans [300, 700])", GREEN, use_color
            )
        elif med < 300:
            verdict = colorize(
                f"trop rapide (médiane {med:.0f} ms < 300 — switch anticipe Cedar)",
                YELLOW,
                use_color,
            )
        else:
            verdict = colorize(
                f"trop lent (médiane {med:.0f} ms > 700 — Cedar lit déjà la suite)",
                RED,
                use_color,
            )
        lines.append("")
        lines.append(f"- Verdict : {verdict}")
    else:
        lines.append("- Aucun appariement detect→apply réussi.")
    lines.append("")

    # Verdict global
    lines.append("## Verdict global")
    lines.append("")
    issues: list[str] = []
    successes: list[str] = []
    if m.lookup_missed > 0 and (m.lookup_found + m.lookup_missed) > 0:
        pct_missed = 100.0 * m.lookup_missed / (m.lookup_found + m.lookup_missed)
        if pct_missed > 20.0:
            issues.append(
                f"drift sync persistant : {pct_missed:.1f} % de lookups manqués"
            )
        else:
            successes.append(
                f"lookups misses sous contrôle ({pct_missed:.1f} %)"
            )
    if m.dsp_inverted > 0:
        issues.append(
            f"bug buffer_start_offset : {m.dsp_inverted} inversions"
        )
    if dist_apply and dist_apply["median"] > 1000:
        issues.append(
            f"apply_switch lent : médiane {dist_apply['median']:.0f} ms"
        )
    if dist_rates:
        med_r = dist_rates["median"]
        if not (16.0 <= med_r <= 22.0):
            issues.append(
                f"adaptive rate hors plage : médiane {med_r:.1f} c/s"
            )
        else:
            successes.append(f"adaptive rate sain (médiane {med_r:.1f} c/s)")
    if m.perso_transitions:
        med_t = statistics.median(t["delay_ms"] for t in m.perso_transitions)
        if 300 <= med_t <= 700:
            successes.append(
                f"transitions perso dans la fenêtre attendue ({med_t:.0f} ms)"
            )
        elif med_t > 700:
            issues.append(
                f"transitions perso lentes ({med_t:.0f} ms > 700)"
            )

    if not issues:
        lines.append(
            colorize("Le fix a fonctionné. ", GREEN, use_color)
            + "Aucun signal de drift détecté."
        )
    else:
        lines.append(
            colorize("Drift persistant. ", RED, use_color)
            + "Investiguer :"
        )
        for issue in issues:
            lines.append(f"- {issue}")
    if successes:
        lines.append("")
        lines.append("Points positifs :")
        for s in successes:
            lines.append(f"- {s}")
    lines.append("")

    return "\n".join(lines)


def metrics_to_json(m: Metrics) -> dict[str, Any]:
    total_lookups = m.lookup_found + m.lookup_missed
    return {
        "total_events": m.total_events,
        "duration_s": round(m.duration_s, 3),
        "response_done_count": m.response_done_count,
        "kinds": dict(m.kinds),
        "adaptive": {
            "distribution": distribution(m.adaptive_rates),
            "fallback_count": m.fallback_count,
        },
        "schedule_lookup": {
            "total": total_lookups,
            "found": m.lookup_found,
            "missed": m.lookup_missed,
            "pct_missed": (
                round(100.0 * m.lookup_missed / total_lookups, 2)
                if total_lookups
                else None
            ),
            "gap_distribution": distribution(m.lookup_gaps),
            "delay_ms_on_hits": distribution(m.lookup_delay_ms),
        },
        "apply_switch_ms_since_schedule": distribution(m.apply_switch_ms),
        "buffer": {
            "dsp_emit_count": m.dsp_emit_count,
            "inverted": m.dsp_inverted,
            "accumulated_per_block": distribution(m.accumulated_per_block),
        },
        "silences_gt_200ms": {
            "count": len(m.silences),
            "durations_s": distribution([b - a for a, b in m.silences]),
        },
        "perso_transitions": {
            "count": len(m.perso_transitions),
            "delay_ms": distribution(
                [t["delay_ms"] for t in m.perso_transitions]
            ),
            "items": m.perso_transitions,
        },
    }


# --------- CLI ------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Analyse une trace JSONL Cedar conteur et émet un rapport markdown."
    )
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        help="Chemin du fichier JSONL. Par défaut : plus récent /tmp/cedar-conteur-trace-*.jsonl.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Émet aussi un bloc JSON compact des métriques en fin de rapport.",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Désactive la coloration ANSI.",
    )
    args = parser.parse_args(argv)

    path = args.path
    if path is None:
        path = autodetect_latest()
        if path is None:
            print(
                "Aucun fichier JSONL trouvé dans /tmp/cedar-conteur-trace-*.jsonl",
                file=sys.stderr,
            )
            return 2
    if not path.exists():
        print(f"Fichier introuvable : {path}", file=sys.stderr)
        return 2

    use_color = (not args.no_color) and sys.stdout.isatty()

    events = load_events(path)
    metrics = compute_metrics(events)

    print(render_markdown(metrics, path, use_color))

    if args.json:
        print("```json")
        print(json.dumps(metrics_to_json(metrics), separators=(",", ":"), default=str))
        print("```")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
