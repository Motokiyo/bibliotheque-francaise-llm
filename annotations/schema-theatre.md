# Schéma d'annotation pour le théâtre

## Principe

Transformer le TEI XML de DraCor en JSON structuré pour ingestion LLM.
Chaque scène est un document indépendant, avec ses métadonnées.

## Format proposé

```json
{
  "meta": {
    "titre": "L'École des femmes",
    "auteur": "Molière",
    "date": 1662,
    "genre": "comédie",
    "actes": 5,
    "source": "dracor",
    "id": "moliere-ecole-des-femmes"
  },
  "personnages": [
    {"nom": "Arnolphe", "role": "principal", "description": "bourgeois, vieux barbon"},
    {"nom": "Agnès", "role": "principal", "description": "jeune fille innocente"},
    {"nom": "Horace", "role": "secondaire", "description": "jeune amoureux"}
  ],
  "scenes": [
    {
      "acte": 1,
      "scene": 1,
      "lieu": "Place de ville",
      "personnages_presents": ["Arnolphe"],
      "didascalies": [
        {"type": "entree", "contenu": "Arnolphe seul sur le théâtre"}
      ],
      "repliques": [
        {
          "perso": "Arnolphe",
          "texte": "Ah ! que je suis heureux d'avoir fait ce voyage !",
          "ton": "joyeux",
          "destinataire": "lui-même"
        }
      ]
    }
  ]
}
```

## Champs d'annotation par réplique

| Champ | Type | Description |
|-------|------|-------------|
| `perso` | string | Personnage qui parle |
| `texte` | string | Texte de la réplique |
| `ton` | enum | joyeux, furieux, triste, ironique, solennel, etc. |
| `destinataire` | string | À qui s'adresse la réplique |
| `didascalie_interne` | string | Indication de jeu dans le texte |

## Types de didascalies

- `entree` / `sortie` : déplacements
- `action` : gestes, déplacements
- `ton` : indication de jeu
- `decor` : description du lieu
- `bruitage` : effets sonores
- `silence` : pauses
