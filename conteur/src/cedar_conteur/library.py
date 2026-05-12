"""Library catalog + DraCor TEI fetcher + chronological parser.

TEI structure (DraCor French corpus):
- <div type="act" n="N"> ... </div>
- <div type="scene" n="N"> <head>...</head> <stage>...</stage> <sp who="#perso"> ... </sp> </div>
- <sp> contains <l> (vers) or <p> (prose) and optional <stage> didascalies
"""

import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DRACOR_BASE = "https://dracor.org/api/v1"
REPO_ROOT = Path(__file__).resolve().parents[3]
CACHE_DIR = REPO_ROOT / "sources" / "dracor-cache"
INDEX_FILE = REPO_ROOT / "data" / "index-monde-histoire.json"

CACHE_DIR.mkdir(parents=True, exist_ok=True)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def load_catalog() -> dict[str, Any]:
    if not INDEX_FILE.exists():
        return {"meta": {}, "genres": {}, "siecles": {}}
    return json.loads(INDEX_FILE.read_text(encoding="utf-8"))


async def fetch_oeuvre(oeuvre_id: str) -> dict[str, Any]:
    cache_meta = CACHE_DIR / f"{oeuvre_id}.json"
    if cache_meta.exists():
        return json.loads(cache_meta.read_text(encoding="utf-8"))

    async with httpx.AsyncClient(timeout=30.0) as client:
        json_hdr = {"Accept": "application/json"}
        meta_resp = await client.get(f"{DRACOR_BASE}/corpora/fre/plays/{oeuvre_id}", headers=json_hdr)
        meta_resp.raise_for_status()
        meta = meta_resp.json()

        tei_resp = await client.get(f"{DRACOR_BASE}/corpora/fre/plays/{oeuvre_id}/tei")
        tei_resp.raise_for_status()
        tei_xml = tei_resp.text

    perso_alias = _build_perso_alias_map(meta)
    actes = _parse_tei(tei_xml, perso_alias)
    text_complet = _format_chronological(actes)
    by_character_structured = _build_by_character_structured(actes)

    oeuvre = {
        "id": oeuvre_id,
        "titre": meta.get("title", oeuvre_id),
        "auteur": _first_author(meta),
        "date": meta.get("yearNormalized") or meta.get("yearWritten") or meta.get("yearPrinted"),
        "genre": meta.get("genre", "théâtre"),
        "source": "dracor",
        "actes": actes,
        "text_complet": text_complet,
        "by_character_structured": by_character_structured,
        "personnages_dracor": list(perso_alias.values()),
        "n_actes": len(actes),
        "n_scenes": sum(len(a["scenes"]) for a in actes),
        "n_repliques": sum(
            sum(1 for b in s["blocks"] if b["type"] == "sp")
            for a in actes for s in a["scenes"]
        ),
    }
    cache_meta.write_text(json.dumps(oeuvre, ensure_ascii=False, indent=2), encoding="utf-8")
    return oeuvre


def get_scene_text(oeuvre: dict[str, Any], act_n: str, scene_n: str) -> str:
    """Returns the formatted text of a specific act/scene."""
    for act in oeuvre.get("actes", []):
        if str(act.get("n")) != str(act_n):
            continue
        for scene in act.get("scenes", []):
            if str(scene.get("n")) != str(scene_n):
                continue
            return _format_scene(act, scene)
    return ""


def list_scenes(oeuvre: dict[str, Any]) -> list[dict[str, Any]]:
    """Returns flat list of all scenes with their indices."""
    out = []
    for act in oeuvre.get("actes", []):
        for scene in act.get("scenes", []):
            out.append({
                "act": act["n"],
                "scene": scene["n"],
                "didascalie": scene.get("didascalie", ""),
                "personnages": sorted({b["who"] for b in scene["blocks"] if b["type"] == "sp"}),
            })
    return out


def list_oeuvres_from_catalog() -> list[dict[str, Any]]:
    cat = load_catalog()
    out = []
    for genre, body in cat.get("genres", {}).items():
        for oid in body.get("oeuvres_representatives", []):
            out.append({
                "id": oid,
                "genre": genre,
                "auteurs_principaux": body.get("auteurs_principaux", []),
            })
    return out


def _first_author(meta: dict) -> str:
    authors = meta.get("authors", [])
    if not authors:
        return "Auteur inconnu"
    a = authors[0]
    if isinstance(a, dict):
        return a.get("name") or a.get("shortName") or "Auteur inconnu"
    return str(a)


def _build_perso_alias_map(meta: dict) -> dict[str, str]:
    """Map DraCor character id (e.g. 'hippolyte') to display label (e.g. 'Hippolyte')."""
    out = {}
    for c in meta.get("characters", []) or []:
        cid = c.get("id") or ""
        name = c.get("name") or c.get("label") or cid
        if cid:
            out[cid.lower()] = name
            out[f"#{cid.lower()}"] = name
    return out


def _strip_tags(s: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub("", s)).strip()


def _parse_tei(xml: str, alias: dict[str, str]) -> list[dict[str, Any]]:
    # Isolate the body (drop teiHeader and front matter)
    body_match = re.search(r"<body\b[^>]*>(.*?)</body>", xml, re.DOTALL)
    body = body_match.group(1) if body_match else xml

    actes: list[dict[str, Any]] = []
    # Each act block
    for act_m in re.finditer(
        r'<div\s+type="act"\s+n="([^"]+)"[^>]*>(.*?)</div>\s*(?=<div\s+type="act"|</body|$)',
        body, re.DOTALL,
    ):
        act_n = act_m.group(1)
        act_inner = act_m.group(2)
        head_m = re.search(r"<head\b[^>]*>(.*?)</head>", act_inner, re.DOTALL)
        act_title = _strip_tags(head_m.group(1)) if head_m else f"Acte {act_n}"

        scenes = []
        for scene_m in re.finditer(
            r'<div\s+type="scene"\s+n="([^"]+)"[^>]*>(.*?)</div>\s*(?=<div\s+type="scene"|</div>|$)',
            act_inner, re.DOTALL,
        ):
            scene_n = scene_m.group(1)
            scene_inner = scene_m.group(2)
            head_s = re.search(r"<head\b[^>]*>(.*?)</head>", scene_inner, re.DOTALL)
            scene_title = _strip_tags(head_s.group(1)) if head_s else ""

            blocks = []
            # walk the scene linearly: stage, sp (in order)
            for el in re.finditer(
                r"<stage\b[^>]*>(.*?)</stage>|<sp\b([^>]*)>(.*?)</sp>",
                scene_inner, re.DOTALL,
            ):
                if el.group(1) is not None:
                    txt = _strip_tags(el.group(1))
                    if txt:
                        blocks.append({"type": "stage", "text": txt})
                else:
                    attrs = el.group(2) or ""
                    inner = el.group(3) or ""
                    who_m = re.search(r'who="#?([^"\s]+)"', attrs)
                    who_raw = (who_m.group(1) if who_m else "?").lower()
                    who = alias.get(who_raw) or alias.get(f"#{who_raw}") or who_raw.title()
                    lines = []
                    # collect <l> and <p> in order
                    for line_m in re.finditer(r"<(l|p)\b[^>]*>(.*?)</\1>", inner, re.DOTALL):
                        txt = _strip_tags(line_m.group(2))
                        if txt:
                            lines.append(txt)
                    if lines:
                        blocks.append({"type": "sp", "who": who, "lines": lines})

            scenes.append({
                "n": scene_n,
                "didascalie": scene_title,
                "blocks": blocks,
            })

        actes.append({"n": act_n, "title": act_title, "scenes": scenes})
    return actes


def _format_scene(act: dict, scene: dict) -> str:
    """Format a scene as cleanly as possible for Cedar to read.

    Stage directions (didascalies, scene headers) are stripped: Cedar should
    never read them aloud. Only act/scene markers + character names + their
    spoken lines remain. This keeps the perso-detection regex robust and
    prevents pitch DSP from being applied to scene didascalies.
    """
    out = [f"ACTE {act['n']} — SCÈNE {scene['n']}", ""]
    for b in scene["blocks"]:
        if b["type"] == "stage":
            continue
        out.append(f"{b['who'].upper()}.")
        for line in b["lines"]:
            out.append(f"  {line}")
        out.append("")
    return "\n".join(out).rstrip()


def _format_chronological(actes: list[dict[str, Any]]) -> str:
    parts = []
    for act in actes:
        parts.append(f"\n═══════════════════════════════════════")
        parts.append(f"   ACTE {act['n']} — {act.get('title','')}")
        parts.append(f"═══════════════════════════════════════\n")
        for scene in act["scenes"]:
            parts.append(_format_scene(act, scene))
            parts.append("")
    return "\n".join(parts).strip()


def _build_by_character_structured(actes: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """For each character, list their speeches grouped by act/scene with context."""
    out: dict[str, list[dict[str, Any]]] = {}
    for act in actes:
        for scene in act["scenes"]:
            for b in scene["blocks"]:
                if b["type"] != "sp":
                    continue
                out.setdefault(b["who"], []).append({
                    "act": act["n"],
                    "scene": scene["n"],
                    "scene_didascalie": scene.get("didascalie", ""),
                    "lines": b["lines"],
                })
    return out
