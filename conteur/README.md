# conteur/ — interface de test mode histoire Cedar

Banc de test standalone pour le mode conteur Reachy Care. Tourne sur Mac, sans Pi, sans robot. Le cœur Python (`cedar_conteur/`) sera ensuite branché tel quel dans Reachy Care et Aristote.

## Pour qui

Alexandre, en avant-vente et en R&D. L'objectif est de tester l'**interprétation Cedar** sur des textes du domaine public français (DraCor, 1940 pièces), d'**éditer les annotations vocales** par personnage, et de **lancer la lecture** depuis le navigateur (micro Mac → Cedar → speakers Mac), pour itérer rapidement sur le prompt système avant de toucher Reachy Care.

## Quickstart

```bash
cd RD/Eiffel/bibliotheque-francaise-llm/conteur
cp .env.example .env
# remplir OPENAI_API_KEY (la clé Pollen, fournie pour le robot, copiée depuis /home/pollen/reachy_mini_conversation_app/.env du Pi)
./run.sh
```

Ouvrir `http://localhost:7860`. Cliquer sur une œuvre (Phèdre par défaut, on peut filtrer par genre), éditer les annotations dans le panneau droit si besoin, sauvegarder, puis "Commencer la lecture". Le navigateur demandera la permission micro.

## Architecture

```
conteur/
├── src/cedar_conteur/        ← paquet portable
│   ├── adapter.py            OpenAI Realtime adapter (extrait propre de reachy_care)
│   ├── conteur.py            orchestrateur
│   ├── prompts.py            squelette officiel OpenAI cookbook + Cedar conteur
│   ├── library.py            catalogue + fetch DraCor + cache local
│   └── annotations.py        load/save JSON conforme schema-theatre.md
└── standalone/
    ├── server.py             FastAPI + WS bridge
    └── static/               UI vanilla (HTML/JS/CSS, 0 dépendance)
```

Le paquet `cedar_conteur` est volontairement portable. Quand on voudra l'intégrer dans Reachy Care ou Aristote, on importe :

```python
from cedar_conteur import Conteur, OpenAIRealtimeAdapter
```

et on remplace juste `standalone/server.py` par le runtime cible (Pi audio dans Reachy Care, audio Aristote, etc.).

## Paramètres exposés dans l'UI

| Paramètre | Source | Défaut | Effet |
|---|---|---|---|
| Voix | OpenAI Realtime | `cedar` | conteur recommandé, ou `marin` pour A/B |
| Modèle | OpenAI Realtime | `gpt-realtime-2` | bascule possible vers `gpt-realtime` pour comparaison |
| Reasoning effort | gpt-realtime-2 seul | `medium` | conteur, `high` pour acte complexe, `low` pour latence |
| Speed | session.audio.output.speed | `0.92` | débit narrateur posé, 1.0 conv, 1.15 nerveux |
| Préambules audibles | gpt-realtime-2 seul | activés | "un instant, je cherche..." natif côté Cedar |

## Annotations vocales

Le schéma suit `annotations/schema-theatre.md` du repo. Chaque personnage a :

- `description` : qui c'est dans la pièce
- `registre` : registre vocal (grave, aigu, tremblant…)
- `prompt_instruction` : instruction de jeu en français, injectée dans le prompt système
- `speed_hint` : conseil de débit (non automatiquement appliqué, le modèle l'utilise comme guide)

Une œuvre déjà annotée est `racine-phedre.json` (Phèdre, Thésée, Hippolyte, Œnone, Théramène, Aricie), tirée du travail antérieur dans `reachy_care/app/docs/VOIX_CEDAR_MODE_HISTOIRE.md §7.5`.

## Modèle de prompt

Sections produites par `prompts.py` (ordre exact recommandé par le cookbook OpenAI Realtime) :

1. Role and Objective
2. Personality and Tone
3. Language
4. Reasoning
5. Preambles
6. Personnages et registre vocal *(injecté depuis annotations)*
7. Reference Pronunciations *(injecté depuis annotations)*
8. Conversation Flow
9. Instructions de jeu (transitions narrateur ↔ personnage)
10. Moments dramatiques
11. Safety

## Limites V1 connues

- Pas encore de bouton "générer annotations via LLM texte" (à venir, V1.1).
- Le texte DraCor renvoyé par scène est tronqué à 600 caractères par personnage dans l'aperçu (juste pour repérage). Pour faire lire un passage long, le coller dans la zone "Passage à lire" puis "Pousser ce passage à Cedar".
- Pas de gestion fine de mémoire de lecture (offset, chapitre). À venir quand on intégrera vraiment dans Reachy Care.
- Latence ~600 ms entre le clic "Commencer" et le premier audio Cedar (handshake WS + session.update OpenAI). Normal.

## Suivant

V1.1 : tool LLM auto-génération des annotations à partir de la liste DraCor des personnages.
V1.2 : intégration dans Reachy Care `conv_app_v2/llm/` (remplace l'adapter actuel par celui-ci, identique mais portable).
V1.3 : runtime Aristote (reachy-noos).
