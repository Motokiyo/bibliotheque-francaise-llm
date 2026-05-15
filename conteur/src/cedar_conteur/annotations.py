"""Per-oeuvre voice annotations: load and save JSON conforming to annotations/schema-theatre.md."""

import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
ANNOTATIONS_DIR = REPO_ROOT / "annotations" / "oeuvres"
ANNOTATIONS_DIR.mkdir(parents=True, exist_ok=True)


def load_annotations(oeuvre_id: str) -> dict[str, Any]:
    path = ANNOTATIONS_DIR / f"{oeuvre_id}.json"
    if not path.exists():
        return {
            "oeuvre_id": oeuvre_id,
            "personnages": [],
            "prononciations": {},
            "instructions_globales": "",
        }
    return json.loads(path.read_text(encoding="utf-8"))


def save_annotations(oeuvre_id: str, data: dict[str, Any]) -> None:
    path = ANNOTATIONS_DIR / f"{oeuvre_id}.json"
    data["oeuvre_id"] = oeuvre_id
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def seed_default_annotations() -> None:
    phedre_path = ANNOTATIONS_DIR / "racine-phedre.json"
    if phedre_path.exists():
        return
    phedre = {
        "oeuvre_id": "racine-phedre",
        "personnages": [
            {
                "nom": "PHÈDRE",
                "description": "Femme de Thésée, tourmentée par sa passion interdite pour Hippolyte",
                "registre": "intense, légèrement au-dessus du registre de base, chargée d'émotion",
                "prompt_instruction": "voix passionnée, parfois suppliante parfois désespérée. Tu poses les alexandrins, tu n'écrases pas la rime.",
                "speed_hint": 0.95,
                "pitch_shift": 1.0,
                "vibrato_hz": 0,
                "vibrato_depth": 0,
                "gain_db": 0,
                "antenna_left": -149,
                "antenna_right": 149,
            },
            {
                "nom": "THÉSÉE",
                "description": "Roi d'Athènes, père d'Hippolyte, figure d'autorité",
                "registre": "grave, posé, autoritaire, poids de la royauté",
                "prompt_instruction": "voix grave, parle lentement, presque pesamment. En colère, plus fort mais toujours grave.",
                "speed_hint": 0.93,
                "pitch_shift": -2.0,
                "vibrato_hz": 0,
                "vibrato_depth": 0,
                "gain_db": 1,
                "antenna_left": -80,
                "antenna_right": -80,
            },
            {
                "nom": "HIPPOLYTE",
                "description": "Jeune prince, fils de Thésée, noble et retenu",
                "registre": "voix naturelle de Douze, légèrement plus douce, jeune",
                "prompt_instruction": "ton noble mais jeune, parfois hésitant quand il parle d'amour à Aricie.",
                "speed_hint": 1.0,
                "pitch_shift": 0,
                "vibrato_hz": 0,
                "vibrato_depth": 0,
                "gain_db": 0,
                "antenna_left": 25,
                "antenna_right": 25,
            },
            {
                "nom": "ŒNONE",
                "description": "Nourrice de Phèdre, vieille femme sage et dévouée",
                "registre": "femme âgée, registre un peu plus bas, légèrement tremblante",
                "prompt_instruction": "voix de vieille nourrice, pleine de sollicitude, parle lentement avec gravité.",
                "speed_hint": 0.90,
                "pitch_shift": -1.0,
                "vibrato_hz": 2.5,
                "vibrato_depth": 0.04,
                "gain_db": -1,
                "antenna_left": -138,
                "antenna_right": 138,
            },
            {
                "nom": "THÉRAMÈNE",
                "description": "Gouverneur d'Hippolyte, confident sage",
                "registre": "neutre, posé, ton de conseiller",
                "prompt_instruction": "voix calme et mesurée, sage, légèrement plus discrète que le narrateur.",
                "speed_hint": 0.95,
                "pitch_shift": 0,
                "vibrato_hz": 0,
                "vibrato_depth": 0,
                "gain_db": -1,
                "antenna_left": 0,
                "antenna_right": 0,
            },
            {
                "nom": "ARICIE",
                "description": "Jeune princesse, aimée d'Hippolyte",
                "registre": "légèrement plus haute, douce, retenue, timidité noble",
                "prompt_instruction": "voix de jeune femme noble, douce et retenue, presque pudique quand elle parle d'amour.",
                "speed_hint": 1.0,
                "pitch_shift": 1.5,
                "vibrato_hz": 0,
                "vibrato_depth": 0,
                "gain_db": -1,
                "antenna_left": -156,
                "antenna_right": 156,
            },
            {
                "nom": "ISMÈNE",
                "description": "Confidente d'Aricie, jeune femme avisée",
                "registre": "voix de jeune femme posée, légèrement plus claire qu'Aricie",
                "prompt_instruction": "voix nette et alerte, débit assuré, posture de confidente attentive qui rapporte une nouvelle.",
                "speed_hint": 1.02,
                "pitch_shift": 0.5,
                "vibrato_hz": 0,
                "vibrato_depth": 0,
                "gain_db": 0,
                "antenna_left": -130,
                "antenna_right": 156,
            },
            {
                "nom": "PANOPE",
                "description": "Suivante de Phèdre, messagère sobre",
                "registre": "voix posée, neutre, presque administrative",
                "prompt_instruction": "ton de messagère, brève et précise, sans pathos.",
                "speed_hint": 0.97,
                "pitch_shift": 0,
                "vibrato_hz": 0,
                "vibrato_depth": 0,
                "gain_db": -1,
                "antenna_left": -110,
                "antenna_right": 110,
            },
        ],
        "prononciations": {
            "Thésée": "té-zé",
            "Hippolyte": "i-po-lit",
            "Œnone": "é-no-né",
            "Théramène": "té-ra-mèn",
            "Trézène": "tré-zèn",
        },
        "instructions_globales": "Respecte la métrique de l'alexandrin (6+6 syllabes, césure marquée). Les e muets en fin de vers ne se prononcent pas. Pose les rimes sans les écraser.",
    }
    phedre_path.write_text(json.dumps(phedre, ensure_ascii=False, indent=2), encoding="utf-8")
