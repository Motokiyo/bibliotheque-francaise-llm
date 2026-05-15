# DraCor — Corpus de théâtre français

## Qu'est-ce que c'est ?

**DraCor** (Drama Corpora Project) : infrastructure ouverte qui permet l'analyse computationnelle de plus de 4 000 textes dramatiques.

## Corpus français (fre) — 1 940 pièces

(Chiffre vérifié contre `https://dracor.org/api/v1/corpora/fre/metadata` le 2026-05-12.)

- Période : **1549–1947**
- Auteurs : Molière, Racine, Corneille, Marivaux, Musset, Beaumarchais, etc.
- Encodage : TEI XML (Text Encoding Initiative)
- Licence : **CC BY-NC-SA 4.0** — voir avertissement ci-dessous
- GitHub : https://github.com/dracor-org/fredracor

## ⚠️ Licence NC, restriction sur usage commercial

La clause **NC** (Non-Commercial) interdit toute exploitation à but lucratif. Pour Eiffel AI (SASU) et Galaad, cela impose une séparation stricte : DraCor peut servir à la **R&D interne et à la recherche** (Mode Histoire), pas à un produit commercial ni à entraîner un modèle vendu. Toute oeuvre dérivée hérite de la même licence (clause SA, ShareAlike).

## ⚠️ Limite de l'API pour le schéma d'annotation

L'endpoint `/spoken-text-by-character` retourne les répliques agrégées par personnage, **sans découpage acte/scène ni didascalies inline**. Pour construire le JSON décrit dans `annotations/schema-theatre.md` (acte, scène, personnages présents, destinataire, didascalies par scène), il faut **parser le TEI XML** via `/plays/{play}/tei`. L'API JSON seule ne suffit pas.

## API REST

| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/corpora/fre` | Liste des pièces |
| `GET /api/v1/corpora/fre/metadata/csv` | Métadonnées (CSV) |
| `GET /api/v1/corpora/fre/plays/{play}` | Infos d'une pièce |
| `GET /api/v1/corpora/fre/plays/{play}/tei` | Texte TEI complet |
| `GET /api/v1/corpora/fre/plays/{play}/spoken-text` | Texte parlé uniquement |
| `GET /api/v1/corpora/fre/plays/{play}/stage-directions` | Didascalies uniquement |
| `GET /api/v1/corpora/fre/plays/{play}/spoken-text-by-character` | Répliques par personnage |
| `GET /api/v1/corpora/fre/plays/{play}/networkdata/csv` | Réseau de personnages |

Base URL : `https://dracor.org/api/v1`

## Package Python

```bash
pip install pydracor
```

```python
from pydracor import DracorApi
api = DracorApi()
fre = api.get_corpus("fre")
plays = api.get_plays("fre")
# Récupérer les répliques d'une pièce
sp = api.get_spoken_text_by_character("fre", "moliere-ecole-des-femmes")
```
