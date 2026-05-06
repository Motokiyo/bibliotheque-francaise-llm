# Audit des sources de textes libres de droit en français pour LLM

> Rapport de recherche — 6 mai 2026
> Commande : Alexandre Ferran

---

## Résumé exécutif

**110 milliards de mots** de français libre de droit sont accessibles aujourd'hui, principalement via le projet **Pleias** (Common Corpus). Mais ces données sont **non catégorisées par genre littéraire** et l'OCR de la principale source (Gallica/BnF) est dégradé (qualité réelle ~60-85%).

Le format idéal pour ingestion LLM est le **Parquet** (standard HuggingFace Datasets), avec colonnes : texte, auteur, date, genre, source, qualité.

---

## 1. Inventaire des sources

### 1.1 Project Gutenberg

- **~40 000 titres français** sur ~75 000 ebooks du catalogue
- Formats : TXT, EPUB, HTML, PDF
- API : **Gutendex** (gutendex.com) — JSON non-officiel
- **⚠️ Statut domaine public US ≠ France** : certains textes PD aux US (95 ans publication) sont encore sous droits en France (70 ans après mort auteur)
- Chaque TXT commence par un en-tête de 20-40 lignes (license, préambule)
- Pas de découpage par chapitre, pas de métadonnées structurées dans le TXT

### 1.2 Wikisource francophone

- **668 809 pages de contenu**, soit **~30 000 à 50 000 textes uniques**
- Qualité : **ProofreadPage** — textes relus par des humains, fiables
- API : MediaWiki REST standard
- Forces : domaine public France assuré, métadonnées riches, qualité humaine
- Faiblesses : pas d'extraction massive, wikicode complexe, rate limit

### 1.3 Gallica (BnF)

- **~300 000 monographies**, **3M éditions de presse**, **~35 Go d'OCR**
- APIs : SRU (recherche), Document (métadonnées), OCR/texte brut, IIIF
- **Qualité OCR réelle : ~60-85%** (mesure OCRoscope Pleias plus sévère que l'auto-estimation BnF)
- **Licence contestée** : la BnF revendique des droits additionnels sur les reproductions numériques
- Nombreux textes non littéraires (encyclopédies, catalogues, thèses)

### 1.4 Autres sources

| Source | Contenu | Taille | Utilité LLM |
|--------|---------|--------|-------------|
| Ebooks libres et gratuits | Classiques français bénévoles | ~2 500 titres | Faible (pas d'API) |
| BEQ (Québec) | Auteurs québécois | Modeste | Complément |
| Google Books API | Métadonnées uniquement | — | Enrichissement seulement |
| Harvard Library Corpus | 10M livres (majorité EN) | — | Faible pour français |

---

## 2. Datasets existants sur HuggingFace

### 2.1 PleIAs/French-PD-Books ⭐

| Caractéristique | Valeur |
|---|---|
| **Livres** | 289 000 |
| **Mots** | 16,4 milliards |
| **Source** | Gallica (BnF) |
| **Format** | Parquet |
| **Qualité** | OCR non nettoyé (~60-85%) |
| **Licence** | Domaine public (revendiqué) |

### 2.2 Common Corpus (Pleias) ⭐⭐

| Caractéristique | Valeur |
|---|---|
| **Taille totale** | 2 000+ milliards de tokens |
| **Part française** | 110 milliards de mots |
| **Format** | Parquet |
| **Nettoyage** | OCR corrigé (OCRonos), toxicité filtrée, PII removed |
| **Sous-corpus** | OpenCulture, OpenGovernment, OpenScience, OpenWeb, OpenSource |

**C'est LE dataset de référence.** Propre, traçable, ouvert.

### 2.3 DraCor — French Drama Corpus ⭐⭐

| Caractéristique | Valeur |
|---|---|
| **Pièces de théâtre** | **1 560** |
| **Période** | 1549–1947 |
| **Format** | TEI XML → API JSON |
| **Données disponibles** | Personnages, répliques, didascalies, actes/scènes, réseaux |
| **Licence** | CC BY NC SA 4.0 |
| **API** | REST complète, endpoints spécialisés |
| **Package Python** | pydracor |

### 2.4 Autres datasets HF

- **French-PD-Newspapers** : 3M éditions, 69,8B mots, 287 GB
- **OCRonos-Vintage** : modèle correction OCR (124M params)
- **InstructionFr** : instructions en français pour fine-tuning

---

## 3. Analyse critique des formats

| Format | Taille relative | Parsing | Adapté LLM | Notes |
|--------|:-:|:-:|:-:|-------|
| **TXT brut** | 1x | Trivial | ⚠️ moyen | Pas de structure, métadonnées à inférer |
| **Markdown** | 1.05x | Trivial | ✅ excellent | Léger, structuré, lisible |
| **HTML** | 1.3-2x | Complexe | ⚠️ moyen | Balisage lourd |
| **JSONL** | 1.5x | Trivial | ✅ excellent | Standard ML |
| **Parquet** | ~0.5x | Bibliothèque | ✅ excellent | Compressé, colonnes, standard HF |
| **EPUB** | 1.2x | Complexe | ❌ | Conteneur ZIP + XHTML |
| **XML TEI** | 2-4x | Très complexe | ❌ | Trop de balisage |
| **ALTO XML** | 3-5x | Très complexe | ❌ | Format OCR position + texte |

**Recommandation** : **Parquet** comme format de stockage, **JSON simplifié** comme format d'échange LLM.

---

## 4. Ce qui manque

### 4.1 Catégorisation par genre littéraire
- Les datasets Pleias n'ont **aucune** catégorisation par genre
- Impossible d'isoler "toute la poésie" ou "tout le théâtre"
- **Solution** : classifieur automatique (LLM) + curation

### 4.2 Découpage structuré
- Pas de découpage fiable par chapitre dans l'OCR Gallica
- Pas de séparation dialogues/récit dans les romans
- **Solution** : pipeline de détection de structure

### 4.3 Annotations de jeu (théâtre et dialogues)
- DraCor a les personnages, répliques, didascalies — mais en TEI XML
- Pour les romans : rien n'existe pour taguer les dialogues
- **Solution** : schéma d'annotation JSON (proposé dans `annotations/schema-theatre.md`)

### 4.4 Éditions critiques
- Préfaces, notes, appareil critique — quasi absents
- **Solution** : à chercher dans Wikisource validé

### 4.5 Licence BnF (pour usage commercial)
- Zone grise juridique sur les reproductions numériques
- **Solution** : prioriser les sources clean (Common Corpus, DraCor, Wikisource)

---

## 5. Spécifications du format idéal

### Format de stockage : Parquet
```yaml
colonnes:
  - id: string          # Identifiant unique
  - titre: string       # Titre de l'œuvre
  - auteur: string      # Auteur
  - date: int           # Année
  - genre: string       # Genre littéraire
  - source: string      # Provenance
  - texte: string       # Texte complet (Markdown léger)
  - meta: struct        # Métadonnées enrichies
  - nb_tokens: int      # Nombre de tokens estimé
```

### Format d'annotation (théâtre)
```json
{
  "meta": { "titre": "...", "auteur": "...", "genre": "..." },
  "personnages": [{"nom": "...", "role": "..."}],
  "scenes": [{
    "acte": 1, "scene": 3,
    "personnages_presents": ["...", "..."],
    "didascalies": [{"type": "action", "contenu": "..."}],
    "repliques": [{"perso": "...", "texte": "...", "ton": "..."}]
  }]
}
```

---

## 6. Recommandations

### Priorité 1 — Common Corpus
- **110 milliards de mots** de français, propre, format Parquet
- Téléchargement : 1-2 jours
- Prêt à l'emploi

### Priorité 2 — DraCor (théâtre)
- **1 560 pièces** avec annotations personnages/répliques/didascalies
- API REST, package Python pydracor
- Peut être connecté au robot en un weekend

### Priorité 3 — Wikisource (qualité)
- Textes relus par des humains
- Extraction via API MediaWiki
- Complément pour les œuvres absentes de Gallica

### Effort estimé pour dataset clean unifié
- Pipeline extraction + correction OCR : **1-3 mois**
- Classification par genre : **1-2 semaines** (via LLM)
- Annotation théâtre : déjà fait (DraCor)
- Annotation dialogues romans : **plusieurs mois** (recherche)

---

## 7. Projets similaires

| Projet | Description | Pertinence |
|--------|-------------|:----:|
| **Pleias / Common Corpus** | Dataset LLM open français | ⭐⭐⭐ |
| **DraCor** | Corpus théâtral annoté | ⭐⭐⭐ |
| **OpenLLM-France** | Communauté datasets français | ⭐⭐ |
| **OCRonos** | Correction OCR open source | ⭐⭐ |
| **Gallicagram** | Analyse culturelle ngram | ⭐ |
| **HTR-United** | Vérité terrain OCR | ⭐ |

---

*Document généré par recherche web (Brave API) le 6 mai 2026.*
