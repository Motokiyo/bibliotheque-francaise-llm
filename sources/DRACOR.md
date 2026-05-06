# DraCor — Corpus de théâtre français

## Qu'est-ce que c'est ?

**DraCor** (Drama Corpora Project) : infrastructure ouverte qui permet l'analyse computationnelle de plus de 4 000 textes dramatiques.

## Corpus français (fre) — 1 560 pièces

- Période : **1549–1947**
- Auteurs : Molière, Racine, Corneille, Marivaux, Musset, Beaumarchais, etc.
- Encodage : TEI XML (Text Encoding Initiative)
- Licence : CC BY NC SA 4.0
- GitHub : https://github.com/dracor-org/fredracor

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
