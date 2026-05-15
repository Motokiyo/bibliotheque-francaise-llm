# Bibliothèque française pour LLM — Mode Histoire

Créer un index structuré et annoté des textes libres de droit en français, optimisé pour la lecture par des LLM (Large Language Models).

## Objectif

Permettre à un LLM (via API OpenAI — modèle Cedar) de naviguer, lire et interpréter la littérature française du domaine public avec :
- Un index catégorisé par **genre, auteur, époque**
- Des annotations de **personnages, tons, didascalies** pour le théâtre
- Du texte nettoyé et formaté (Markdown structuré, XML léger ou JSONL)
- Un format optimisé pour l'ingestion LLM (pas de balisage lourd, métadonnées intégrées)

## Sources identifiées

| Source | Contenu | Taille | Qualité | Statut |
|--------|---------|--------|---------|--------|
| **Common Corpus (Pleias)** | Littérature + presse + docs officiels | 110B mots | Bonne (curé) | ✅ Disponible |
| **French-PD-Books (Pleias)** | Livres (Gallica) | 289k titres, 16,4B mots | OCR à corriger | ✅ Disponible |
| **DraCor — fre** | Théâtre français XVIe-XXe | 1940 pièces | Excellente (TEI) | ✅ API REST (NC) |
| **Wikisource** | Littérature française validée | ~50k textes | Excellente (humains) | ✅ API MediaWiki |
| **Project Gutenberg** | Littérature française | ~40k titres | Bonne (PD US) | ⚠️ Vérifier PD France |
| **Ebooks libres et gratuits** | Classiques français | ~2500 titres | Bonne | ❌ Pas d'API |

## Architecture actuelle

```
bibliotheque-francaise-llm/
├── CLAUDE.md            # Garde-fous projet (lu à chaque session)
├── INDEX.md             # Catalogue wiki, lu EN PREMIER
├── STATE.md             # Snapshot projet (P0, blocages, decisions)
├── DECISIONS.md         # Décisions validées et abandonnées
├── README.md            # Pointeur court
├── MODE_HISTOIRE.md     # Ce document (vision et sources)
├── index/               # Index des œuvres (STRUCTURE.md, futurs Parquet/JSONL)
├── sources/             # Documentation des sources (DRACOR.md, COMMON_CORPUS.md)
├── annotations/         # Schémas d'annotation (schema-theatre.md)
├── data/                # Données brutes (index-monde-histoire.json)
└── docs/                # Rapports d'audit (audit-sources.md)
```

(`formats/` et `tools/` étaient annoncés mais n'existent pas encore. Ils seront créés à l'étape pipeline.)

## Premières étapes

1. Connecter l'API DraCor (1 940 pièces françaises annotées, licence CC BY-NC-SA 4.0)
2. Explorer Common Corpus (format Parquet, 110B mots français)
3. Définir le schéma d'index (genre, auteur, époque, métadonnées) — voir `index/STRUCTURE.md`
4. Définir le format d'annotation pour le théâtre (personnages, répliques, didascalies) — voir `annotations/schema-theatre.md`

## ⚠️ Contraintes de licence

- **DraCor** : CC BY-NC-SA 4.0 — clause **NC bloquante** pour un usage commercial direct (Eiffel AI, Galaad). Utilisable pour R&D interne et recherche. Toute oeuvre dérivée hérite de la licence (SA).
- **Common Corpus / French-PD-Books** : domaine public revendiqué — vérifier les sous-corpus avant publication.
- **Wikisource** : licences variables (CC BY-SA majoritaire) — à vérifier oeuvre par oeuvre.

## Licence du projet

Les textes sources restent sous leur licence d'origine.
Les annotations et index produits par ce projet seront sous licence ouverte (CC BY-SA ou équivalent), mais devront respecter la NC de DraCor pour toute partie dérivée du corpus dramatique.
