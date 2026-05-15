# STATE — conteur (Cedar storyteller)

## Snapshot 12/05/2026 soir — pivot complet DSP browser-side

### Trajectoire de la session (4 versions successives)

1. **Tentative 1 — adaptive rate + fix byte_offset DSP** (matin) : on cherchait à patcher le scheduling at_byte. Mesuré sur trace : `chars/sec` adaptive = 11-14 c/s au lieu de 17, et le browser attendait 12-71 sec pour appliquer un switch programmé loin dans le futur.
2. **Tentative 2 — switch direct serveur** (après-midi) : on a abandonné le scheduling, fait `active_perso = new_perso` immédiatement à la détection. Antennes bougent, mais glitchs audibles au switch DSP, et latence ~500 ms entre nom entendu et bascule pitch.
3. **Tentative 3 — interruption immédiate + substitution prononciation** : kill local audio côté browser, drop server-side après cancel. Substitution `Aricie → A-ri-si` dans le texte poussé (rejeté par Alex car ne scale pas pour milliers d'œuvres — gardé en V1 minimal).
4. **Tentative 4 (FINALE V1.x) — DSP browser-side via SoundTouch** : abandon complet du DSP server-side pyrubberband. AudioWorkletProcessor streaming maison ajouté à `soundtouch-worklet.js` (vendu localement depuis `@soundtouchjs/audio-worklet@0.1.17`). Pitch + tempo découplés, vibrato via LFO Web Audio, gain via GainNode. Synchronisation parfaite avec la lecture speaker.

### Architecture audio finale (12/05/2026 soir)

```
OpenAI Realtime → audio.delta → WS → browser (PCM brut, AUCUN DSP serveur)
                                ↓
   BufferSource (par chunk) → pitchNode (streaming-pitch-shifter, SoundTouch)
                            → vibratoGain (modulé AM par vibratoLFO + vibratoDepthGain)
                            → masterGain (gain_db → linear)
                            → audioCtx.destination

Transcript → détection perso (server) → perso.active → browser applyPersoProfile()
                                       ↓
                                   set_perso_antennas (server-side, async to_thread)
```

### P0 ouverts (12/05/2026 soir)

1. **Antennes s'arrêtent après ~3 transitions** (RÉOUVERT le 12/05 soir)
   - Logs explicites `→ antenna call` / `← antenna done` ajoutés mais pas encore exploités.
   - Diagnostic à reprendre demain : tracer le log au moment où les antennes se figent, voir si l'appel est fait, s'il revient, si exception silencieuse côté SDK Pollen.

2. **Latence transcript ~500 ms** (intrinsèque au stream OpenAI)
   - Le transcript arrive 300-500 ms après l'audio correspondant. Donc quand on détecte "ŒNONE." côté serveur, Cedar l'a déjà dit dans le speaker il y a 500 ms.
   - Conséquence : le profil DSP s'applique 500 ms trop tard. On entend la fin de la réplique précédente avec le pitch précédent, puis le nom du nouveau perso avec le pitch précédent, puis la bascule.
   - Non résolvable sans timing audio→transcript exact (qu'OpenAI ne fournit pas).
   - Solution stratégique long terme : TTS découplé (voir `docs/DECOUPLAGE_TTS_LONGTERME.md`).

3. **Prononciation FR classique non respectée** (Aricie → A-ri-cié au lieu d'A-ri-si)
   - Cedar ignore le prompt `Reference Pronunciations`.
   - Solution court terme : substitution dans le texte poussé (Aricie → A-ri-si) — fait mais ne scale pas pour milliers d'œuvres.
   - Vraies solutions : (a) dictionnaire FR classique global, (b) TTS découplé avec lexique phonétique IPA. À traiter dans une session dédiée.

4. **Transcript Cedar concaténé mono-ligne** (P0 #2, toujours ouvert)
   - Regex de détection rate des transitions quand Cedar enchaîne sans `\n\n` ni ponctuation forte.
   - À fixer par régex assouplie ou pré-traitement du transcript_buffer.

### P0 résolus pendant cette session

- ✅ **P0 #1 (matin) — Sync timing perso drift après ~3 répliques** : résolu par bascule DSP browser-side. Le scheduling at_byte était conceptuellement faux (transcript en retard sur audio bufferisé, mapping impossible).
- ✅ **P0 #3 (matin) — Micro-glitches au switch DSP** : disparus avec le DSP browser-side. Plus de `prev_tail` à crossfader entre profils différents, les changements de pitch passent par `setTargetAtTime` ramping 30 ms.
- ✅ **Interruption immédiate** : `killLocalAudio()` côté browser stoppe toutes les `BufferSource` actives avec `src.stop(0)`, vide `chunkSchedule` et `pendingSwitches`, reset `playbackTime`. Côté serveur, flag `cancelled["v"]` drop les `audio.delta` en transit jusqu'au prochain `response.done`.

### P1 reporté (à attaquer après stabilisation P0)

- Retour automatique en voix narrateur sur lecture du nom de perso et didascalies : demande un timing exact entre transcript et audio, donc dépend du DSP browser-side (✅ fait) ET d'un signal de fin de réplique (à ajouter, probablement via détection d'une pause ou d'un retour à la baseline).
- Ré-injection persona toutes les ~8 turns pour combattre la dérive baseline Cedar (bible §7).
- Sous-chunking des scènes longues (Racine V.6 ~3500 chars) sur frontière `PERSO.\n`.
- VAD adaptatif pour lecture continue : eagerness=low pendant chunks, réactivation entre chunks, mot d'arrêt explicite ("Douze, arrête").
- Découplage TTS long terme : Option B du rapport DECOUPLAGE_TTS_LONGTERME.md, à creuser quand OpenAI annonce dépréciation V1 ou pour un produit Eiffel AI grand public.
- `pyrubberband` toujours absent de `requirements.txt` mais plus utilisé (DSP server désactivé) — à retirer de `dsp.py` ou laisser pour fallback futur.

### Décisions actives (post-session 12/05/2026 soir)

- Modèle : `gpt-realtime` (V1), voix `cedar` — verrouillé pour cohérence multi-projets persona Douze.
- DSP : **browser-side via `@soundtouchjs/audio-worklet@0.1.17` (vendu localement)** + worklet streaming maison `streaming-pitch-shifter`.
- DSP serveur (pyrubberband) **désactivé** ; le code reste mais `dsp_proc.feed()` n'est plus appelé.
- Antennes : ±156°, profils 8 persos Phèdre seedés.
- Daemon Pollen patché : `args.no_media=True` quand `mockup_sim` (workaround SIGSEGV GStreamer).
- Switch perso : appliqué **immédiatement côté serveur** quand détecté (plus de scheduling at_byte).
- Interruption : `cancel` immédiat côté browser + flag `cancelled["v"]` côté serveur.
- Trace JSONL : `/tmp/cedar-conteur-trace-{ts}.jsonl` + `tools/analyze_trace.py` pour exploitation.

### Persos Phèdre seedés (annotations) — inchangé

| Perso | Pitch | Speed | Antennes L/R° |
|---|---|---|---|
| PHÈDRE | +1.0 | 0.95 | -149 / +149 |
| THÉSÉE | -2.0 | 0.93 | -80 / -80 |
| HIPPOLYTE | 0 | 1.00 | +25 / +25 |
| ŒNONE | -1.0 + vibrato 2.5Hz | 0.90 | -138 / +138 |
| THÉRAMÈNE | 0 | 0.95 | 0 / 0 |
| ARICIE | +1.5 | 1.00 | -156 / +156 |
| ISMÈNE | +0.5 | 1.02 | -130 / +156 |
| PANOPE | 0 | 0.97 | -110 / +110 |

### Architecture finale livrée

```
bibliotheque-francaise-llm/conteur/
├── src/cedar_conteur/        ← paquet portable (zero modif cette session)
│   ├── adapter.py            OpenAI Realtime adapter
│   ├── conteur.py            orchestrateur
│   ├── prompts.py            squelette officiel cookbook
│   ├── library.py            catalogue + fetch DraCor TEI parsing
│   ├── annotations.py        load/save + seed 8 persos Phèdre
│   ├── dsp.py                pyrubberband (NON utilisé côté serveur)
│   └── robot.py              RobotController 3-tier (attach/spawn/mock)
├── standalone/
│   ├── server.py             FastAPI + WS + tools + trace logger + cancel
│   └── static/
│       ├── app.js            UI + chaîne audio Web Audio + perso.active
│       ├── soundtouch-worklet.js   SoundTouch lib + streaming-pitch-shifter
│       └── soundtouch-audio-node.js  (chargé mais inutilisé côté node helper)
├── tools/
│   └── analyze_trace.py      script de diagnostic des sessions
├── docs/
│   └── DECOUPLAGE_TTS_LONGTERME.md   stratégie pérennité voix Cedar V1
└── annotations/oeuvres/      Phèdre seedée
```

### Pi-Only (notes pour Reachy Care)

- Patch `no_media=True` quand `mockup_sim` : à porter dans `reachy_care/app/conv_app_v2/llm/` si on déploie le mode standalone sur le robot.
- DSP browser-side via SoundTouch : nécessite un browser, pas trivial à porter en Pi audio direct. Reachy Care V2 devra repenser le pipeline (DSP local Python ou TTS séparé).
- Voix V1 partout : à confirmer pour Reachy Care quand on remonte la mise à jour.

### Suivant (demain 13/05/2026)

1. **Antennes** : diagnostiquer pourquoi elles se figent après ~3 transitions (logs prêts).
2. **Test sur Phèdre I.3 et IV.2** avec le DSP browser-side activé (Alex doit recharger l'onglet pour charger `soundtouch-worklet.js`).
3. **PoC découplage TTS long terme** : capturer 30 min de Cedar V1 sur contenu varié AVANT toute évolution OpenAI.
4. **Prononciation FR classique** : commencer un dictionnaire global `data/pronunciations_fr_classique.json` (Aricie, Œnone, Phèdre, Hippolyte, Théramène, Trézène, plus ~200 autres).
5. **Si tout stable**, V2 : déplacer le code conteur portable dans `reachy_care/app/conv_app_v2/llm/` pour intégration Reachy Care.
