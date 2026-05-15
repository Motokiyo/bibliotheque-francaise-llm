# Mode Histoire — Bibliothèque française pour LLM

Index structuré et annoté des textes français libres de droit, optimisé pour ingestion par un Large Language Model.

Projet R&D Eiffel AI.

## Entrée rapide

| Document | À lire si... |
|----------|--------------|
| [INDEX.md](INDEX.md) | tu cherches un fichier précis |
| [STATE.md](STATE.md) | tu veux savoir où on en est |
| [CLAUDE.md](CLAUDE.md) | tu démarres une session Claude Code |
| [MODE_HISTOIRE.md](MODE_HISTOIRE.md) | tu veux la vision et la stratégie de sources |
| [DECISIONS.md](DECISIONS.md) | tu cherches ce qui a été validé ou abandonné |
| [docs/audit-sources.md](docs/audit-sources.md) | tu veux le rapport d'audit complet (110B mots, formats, gaps) |

## Sources principales

- **DraCor (fre)** : 1940 pièces de théâtre français annotées (TEI), licence CC BY-NC-SA 4.0.
- **Common Corpus (Pleias)** : 110 milliards de mots français propres, Parquet.
- **Wikisource** : ~50 000 textes relus par des humains.

## Statut

Structure documentaire posée. Aucun code livré pour l'instant. Prochaine étape : pipeline DraCor TEI→JSON sur 3 pièces, voir [STATE.md](STATE.md).

## Licence

- Textes sources : licences d'origine (domaine public ou CC selon source).
- Annotations et index produits : CC BY-SA, sauf parties dérivées de DraCor qui héritent du NC.
