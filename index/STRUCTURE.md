# Index des œuvres — Structure

L'index est le point d'entrée unique pour le LLM.
Format : Parquet (compatible HuggingFace Datasets).

## Schéma proposé

| Colonne | Type | Description |
|---------|------|-------------|
| `id` | string | Identifiant unique |
| `titre` | string | Titre de l'œuvre |
| `auteur` | string | Auteur |
| `date` | integer | Année de publication |
| `siecle` | integer | Siècle (arrondi) |
| `genre` | enum | roman, theatre, poesie, essai, biographie, correspondance |
| `sous_genre` | string | comedie, tragedie, drame, sonnet, etc. |
| `source` | string | dracor, common-corpus, wikisource, gutenberg |
| `source_url` | string | URL d'origine |
| `nb_mots` | integer | Nombre de mots |
| `nb_tokens` | integer | Nombre de tokens (estimation) |
| `qualite_ocr` | integer | 0-100 (si OCR) |
| `langue` | string | fr (par défaut) |
| `resume` | string | Résumé court (1-2 phrases) |
| `mots_cles` | array[string] | Mots-clés thématiques |

## Requêtes possibles

```sql
-- Toutes les tragédies du XVIIe
SELECT * FROM index WHERE genre = 'theatre' AND sous_genre = 'tragedie' AND siecle = 17

-- Tous les romans de Zola
SELECT * FROM index WHERE auteur LIKE '%Zola%' AND genre = 'roman'

-- La poésie romantique (1820-1850)
SELECT * FROM index WHERE genre = 'poesie' AND date BETWEEN 1820 AND 1850
```
