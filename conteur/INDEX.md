# INDEX — conteur

Catalogue wiki conteur. À lire en premier dans toute nouvelle session.

## Fichiers vivants

| Fichier | Rôle |
|---|---|
| `README.md` | Quickstart utilisateur, architecture courte |
| `STATE.md` | Snapshot projet : P0 ouverts, décisions actives, persos seedés |
| `DECISIONS.md` | VALIDÉ / ABANDONNÉ par date — historique des choix d'archi |
| `KNOWLEDGE_BASE.md` | Contraintes techniques validées : OpenAI Realtime, Reachy SDK, DSP, détection perso, sync browser |
| `INDEX.md` | Ce fichier |

## Outils

| Fichier | Rôle |
|---|---|
| `tools/analyze_trace.py` | Analyse les traces JSONL `/tmp/cedar-conteur-trace-*.jsonl` (auto-détection du plus récent), sort un rapport markdown : adaptive rate, schedule lookup health, délais apply_switch, transitions perso bout-en-bout, verdict global |

## Docs stratégiques

| Fichier | Rôle |
|---|---|
| `docs/DECOUPLAGE_TTS_LONGTERME.md` | Comment pérenniser la voix Cedar V1 sur 5+ ans malgré les évolutions OpenAI. Options A/B/C/D comparées avec recommandation Option B (ElevenLabs Pro Voice Clone via LiveKit) |

## Architecture audio (référence rapide)

```
OpenAI Realtime → audio.delta → WS → browser (PCM brut, AUCUN DSP serveur)
                                ↓
   BufferSource (chunk) → pitchNode (streaming-pitch-shifter, SoundTouch)
                       → vibratoGain (modulé AM par LFO Web Audio)
                       → masterGain (gain_db)
                       → speaker
```

Détecteur perso : `dsp.py:detect_perso_with_pos` (regex stricte sur transcript_buffer post-`_unphonetize`).
Profil DSP : `applyPersoProfile(persoName)` côté browser sur event `perso.active`.
Antennes Reachy : `set_perso_antennas` côté serveur (asyncio.to_thread).

## P0 / Suivant (au 11/06/2026)

1. Déployer le conteur sur un serveur accessible à la famille (pas Vercel statique pur) : Uvicorn persistant + HTTPS/WSS + code d'accès.
2. Tester sur Android 15-20 minutes après déploiement.
3. Anciens P0 théâtre à reprendre ensuite : antennes, transcript mono-ligne, latence perso.

## Référence canonique externe

- **Bible vocale** : `/Users/alexandre/Territoire/Galaad-Motokiyo-Ferran/4 Ressources/Outils-IA/Modeles/openai-realtime-2-bible-vocale.md`
- **MAP.md reachy_care** : `/Users/alexandre/Territoire/Galaad-Motokiyo-Ferran/1 Projets/reachy_care/app/MAP.md`
- **VOIX_CEDAR_MODE_HISTOIRE** : `/Users/alexandre/Territoire/Galaad-Motokiyo-Ferran/1 Projets/reachy_care/app/docs/VOIX_CEDAR_MODE_HISTOIRE.md`

## Reprise demain

Alex demande session en **mode plan** dédiée à comprendre :
- Capture mic browser AudioWorklet 24kHz → WebSocket → OpenAI Realtime
- Stream `response.audio.delta` ← OpenAI
- DSP serveur Python (buffering 250ms + crossfade + pitch shift)
- WS `audio.delta` → browser
- Web Audio API scheduling → speaker
- Boucle de retour : `response.audio_transcript.delta` → detect_perso → schedule_switch → apply_switch
- Identifier où le drift apparaît
- pouvoir choisir entre les voix. Elles ont changé entre v1.5 et 2 donc retrouver Cedar v1 dans v2, il faut pouvoir tester toutes les voix : alloy, ash, ballad, cedar, coral, echo, fable, marin, nova, onyx, sage, shimmer, verse

Avant tout code nouveau, comprendre.
