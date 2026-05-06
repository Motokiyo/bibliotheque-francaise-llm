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
| **DraCor — fre** | Théâtre français XVIe-XXe | 1560 pièces | Excellente (TEI) | ✅ API REST |
| **Wikisource** | Littérature française validée | ~50k textes | Excellente (humains) | ✅ API MediaWiki |
| **Project Gutenberg** | Littérature française | ~40k titres | Bonne (PD US) | ⚠️ Vérifier PD France |
| **Ebooks libres et gratuits** | Classiques français | ~2500 titres | Bonne | ❌ Pas d'API |

## Architecture proposée

```
bibliotheque-francaise-llm/
├── index/              # Index des œuvres (Parquet/JSONL)
├── sources/            # Scripts d'extraction par source
├── annotations/        # Schémas d'annotation (théâtre, dialogues)
├── formats/            # Spécifications des formats LLM-optimisés
├── tools/              # Outils de conversion et nettoyage
└── docs/               # Documentation
```

## Premières étapes

1. Connecter l'API DraCor (1 560 pièces françaises annotées)
2. Explorer Common Corpus (format Parquet, 110B mots français)
3. Définir le schéma d'index (genre, auteur, époque, métadonnées)
4. Définir le format d'annotation pour le théâtre (personnages, répliques, didascalies)

## Licence

Les textes sources sont du domaine public (sauf mention contraire).
Les annotations et index produits par ce projet seront sous licence ouverte (CC-BY-SA ou équivalent).
