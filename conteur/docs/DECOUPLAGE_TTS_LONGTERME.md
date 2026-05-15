# Découplage TTS long terme — pérenniser la voix de Douze sur 5+ ans

## Constat

Cedar est exclusive à OpenAI Realtime V1. V2 (mai 2026, nouveau stack audio) modifie le timbre sous le même nom. Sur 5 ans, OpenAI dépréciera V1. Les résidents EHPAD vivent mal un changement de voix du compagnon : c'est un risque produit majeur, à traiter avant le déploiement clinique, pas après.

## Comparatif des 4 options

| Option | Voix stable 5 ans | Qualité FR | Latence (TTFA) | Coût mensuel EHPAD (6h/j) | Complexité | Souveraineté |
|--------|-------------------|------------|----------------|---------------------------|------------|--------------|
| **A. OpenAI Realtime V1 figée** | Non (dépréciation inévitable) | Excellente (cedar V1) | ~500 ms | ~250 € (audio in+out) | Faible | Nulle |
| **B. Realtime text-only + TTS cloné cloud** (ElevenLabs Multilingual v2 ou Cartesia Sonic-3) | Oui (clone figé dans notre compte) | Excellente (EL v2 PVC à 30 min FR, MOS ~4.4) | 75–300 ms TTFA (Flash/Sonic) | 80–180 € | Moyenne (LiveKit pipeline) | Faible (cloud US) |
| **C. TTS local (XTTS v2 / OpenVoice v2)** | Oui (poids gelés) | Bonne mais inférieure cloud, FR natif | M1: ~200 ms ; **Pi5: 30–60 s/phrase, inutilisable temps réel** | ~0 € après setup | Élevée (ops, GPU dans Douze) | Totale |
| **D. Hybride : Realtime conversation + TTS cloné Cedar pour conte** | Partielle (voix change en mode dialogue) | Excellente sur les 2 modes | Conversation 500 ms / Conte 200 ms | ~150 € | Moyenne | Mixte |

## Recommandation — Option B comme cible, D comme transition

Architecture cible : **OpenAI Realtime en `output_modalities: ["text"]`** (STT + raisonnement + streaming texte), routage du texte vers **ElevenLabs Professional Voice Clone** entraîné sur ~30 min de Cedar V1 archivée, streamé via LiveKit Agents. Cela découple complètement la voix de l'évolution OpenAI : le clone reste figé dans notre compte EL aussi longtemps que le service vit, et est portable vers Cartesia ou self-host si EL change ses CGU.

**Timing** : démarrer le PoC B **maintenant** (mai 2026) pendant que Cedar V1 est encore servie. Chaque mois perdu = audio Cedar V1 disponible mais non capturé. Si OpenAI déprécie V1 sans capture préalable, la voix est perdue.

**Risque légal** : OpenAI Usage Policies interdisent l'usurpation de voix sans consentement. Cedar est une voix synthétique sans personne réelle derrière, mais le statut juridique du clone d'une voix OpenAI n'est pas tranché. Mitigation : ElevenLabs PVC demande une attestation de droits d'usage. À faire valider par juriste avant production EHPAD. Plan B sain : faire enregistrer **30 min de narration française par un comédien sous contrat** (300–800 €) au timbre proche de Cedar, et cloner ce comédien. Voix juridiquement propre, perenne, indépendante d'OpenAI.

## PoC à lancer cette semaine (1 jour chacun)

1. **PoC découplage Realtime + EL** (priorité 1) : LiveKit agent en `modalities: ["text"]` sur gpt-realtime, TTS ElevenLabs Flash v2.5 voix française stock, mesurer TTFA bout-en-bout et naturel sur 5 phrases de conte. Valide la faisabilité technique avant d'investir dans le clone.
2. **PoC clone Cedar** : capturer 30 min de cedar V1 sur des textes français variés (dialogue court, narration longue, émotion), créer un PVC ElevenLabs, A/B test à l'aveugle avec 3 auditeurs sur 2 min de conte. Cible : indiscernable à 4/5.

## Sources

- [LiveKit — Realtime models overview (text-only modality)](https://docs.livekit.io/agents/models/realtime/)
- [OpenAI Community — text-only output Realtime](https://community.openai.com/t/how-to-get-text-only-output-from-the-realtime-api/967528)
- [ElevenLabs Multilingual v2 — long-form narration](https://elevenlabs.io/docs/overview/models)
- [Cartesia Sonic-3 — 40–90 ms TTFB, 40+ langues](https://docs.cartesia.ai/build-with-cartesia/tts-models/latest)
- [Hume Octave 2 — sub-200 ms, 11 langues dont FR](https://www.hume.ai/blog/octave-2-launch)
- [Coqui XTTS v2 — FR + 15 autres, MIT, lent sur Pi](https://huggingface.co/coqui/XTTS-v2)
- [Pi DIY Lab — XTTS sur Pi 5 : 30–60 s/phrase](https://pidiylab.com/text-to-speech-raspberry-pi-piper/)
- [OpenVoice v2 — MIT, FR natif, local GPU 4–8 Go](https://github.com/myshell-ai/OpenVoice)
- [OpenAI Usage Policies — voix et likeness](https://openai.com/policies/usage-policies/)
- [Berkeley Tech Law Journal — voice cloning legal 2025](https://btlj.org/2025/06/from-training-data-to-ai-covers-the-legal-challenges-of-voice-cloning/)
