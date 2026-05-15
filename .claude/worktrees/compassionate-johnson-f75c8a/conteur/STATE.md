# STATE — conteur (Cedar storyteller)

## Snapshot 12/05/2026 — session compréhension pipeline + fix sync timing

### Ce qui a été fait aujourd'hui (mode plan puis implémentation)

1. **Cartographie complète du pipeline audio bout en bout** (mic browser AudioWorklet 24k → WS → OpenAI Realtime → DSP serveur → WS → Web Audio scheduling browser → speaker). Documentée dans `KNOWLEDGE_BASE.md §6`.
2. **Identification des 2 causes du drift sync** :
   - 1A — `SPEAKING_RATE_CHARS_PER_SEC = 17.0` constant ignore la variation Cedar selon perso et `audio.output.speed`.
   - 1B — bug structurel `byte_offset` du buffer DSP : le serveur envoyait l'offset du DERNIER chunk brut accumulé alors que le bloc DSP contient ~6 chunks (250 ms en amont). Le browser ne trouvait jamais le bon chunk dans son `chunkSchedule`.
3. **Implémentation de 3 changements** dans `standalone/server.py` + `standalone/static/app.js` (zéro modif du module portable `cedar_conteur/`) :
   - Logger JSONL structuré → trace dans `/tmp/cedar-conteur-trace-{ts}.jsonl`
   - Fix byte_offset DSP : `dsp_buffer_state["start_offset"]` mémorise le début d'accumulation
   - Adaptive rate : `rate = chars_recv_total / (audio_bytes_total / 48000)` dès qu'on a ≥ 60 chars
4. **Réponses aux questions structurelles** (Cedar V1/V2, lecture longue, archi Rust) actées dans `DECISIONS.md`.

### À valider par test terrain (priorité immédiate)

- Lancer `./run.sh`, ouvrir Phèdre, dire « lis-moi l'acte 1 scène 3 ». Écouter 6+ transitions. Comparer les traces JSONL avant/après par `analyse.py` ou à l'œil.
- **Critères d'acceptation** : aucun `schedule_lookup chunk_found:false` après 3 sec audio en cours ; `ms_since_schedule` < 1000 ms médiane ; switch DSP perceptible < 500 ms après le nom du perso.

### P0 ouverts (restant)

1. **Sync timing perso après ~3 répliques** — fix implémenté, **à valider par test terrain 12/05/2026**.
   - Si OK → sortir de P0
   - Si résidu → analyser traces JSONL pour diagnostic fin (la cause peut être ailleurs : time_stretch qui change la durée DSP, latence WS, etc.)

2. **Transcript Cedar concaténé mono-ligne** (P0 #2, ouvert)
   - OpenAI Realtime renvoie le transcript de la lecture sans les `\n\n` du texte poussé.
   - Notre regex stricte rate des transitions perso quand Cedar enchaîne sans ponctuation forte.
   - À traiter dans une session séparée (régex assouplie ou pré-traitement transcript_buffer).

3. **Micro-glitches audio possibles au switch DSP** (P0 #3, ouvert)
   - Le flush du DSPBufferedProcessor à `apply_switch` peut produire des artefacts en bordure de chunk.
   - `prev_tail` (10 ms) du bloc avec ancien profil sert au crossfade-in du bloc avec nouveau profil → si pitch très différent, oreille perçoit la bordure.
   - Fix proposé : reset `prev_tail` à zéros au changement de profil.

### P1 ouverts (à attaquer après validation P0 #1)

- **Ré-injection persona toutes les ~8 turns** pour combattre la dérive baseline Cedar (bible §7).
- **Sous-chunking des scènes longues** (Racine V.6 ~3500 chars) sur frontière `PERSO.\n` pour éviter troncation Cedar au-delà de ~5 min audio continu (bible §9).
- **VAD adaptatif pour lecture continue** : eagerness=low pendant chunks, réactivation entre chunks naturels, mot d'arrêt explicite ("Douze, arrête").
- **Découplage TTS long terme** (Cedar V1 figé) : à étudier si OpenAI annonce dépréciation V1 ou si un produit Eiffel AI doit garantir stabilité voix 5+ ans.
- **`pyrubberband` absent de `requirements.txt`** mais hard-importé dans `dsp.py` : à ajouter.

### Décisions actives

- Modèle : `gpt-realtime` (V1) — voix `cedar`
- DSP serveur : `pyrubberband` crispness=6, buffer 250ms, crossfade 5ms
- Antennes : ±156° (max Reachy Mini ±2.73 rad), profils 8 persos Phèdre seedés
- Daemon Pollen patché : `args.no_media = True` quand `mockup_sim` (workaround SIGSEGV GStreamer)
- `sitecustomize.py` installé dans venv Pollen pour ré-appliquer le patch après updates
- Timing browser-authoritative : `chunkSchedule` + `apply_switch` au moment réel de lecture

### Persos Phèdre seedés (annotations)

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

### Architecture livrée

```
bibliotheque-francaise-llm/conteur/
├── src/cedar_conteur/        ← paquet portable
│   ├── adapter.py            OpenAI Realtime adapter
│   ├── conteur.py            orchestrateur
│   ├── prompts.py            squelette officiel cookbook
│   ├── library.py            catalogue + fetch DraCor TEI parsing
│   ├── annotations.py        load/save + seed 8 persos Phèdre
│   ├── dsp.py                pyrubberband pipeline + detect_perso
│   └── robot.py              RobotController 3-tier (attach/spawn/mock)
├── standalone/
│   ├── server.py             FastAPI + WS bridge + sync byte_offset
│   └── static/               UI vanilla HTML/JS/CSS
└── annotations/oeuvres/      Phèdre seedée
```

### Pi-Only (notes pour Reachy Care)

- Patch `no_media=True` quand `mockup_sim` : à porter dans `reachy_care/app/conv_app_v2/llm/` si on déploie le mode standalone sur le robot.
- HuggingFace `claim_b_key` mécanisme : utilisable depuis n'importe quelle machine avec HF_TOKEN, à documenter dans `PI_KNOWLEDGE_BASE.md` (déjà fait dans MAP.md §8).
- Voix V1 partout : à confirmer pour Reachy Care quand on remonte la mise à jour.

### Suivant

V1.x (post-comprehension audio) :
- Fixer le drift sync après N répliques (probablement via tracking exact audio joué côté browser vs reçu)
- Améliorer la détection perso pour Cedar mono-ligne (ou abandonner regex au profit d'un autre signal)
- DSP smoother au switch (overlap au lieu de flush brutal)

V2 :
- Mode lecture longue avec STT watchdog (architecture documentée dans bible vocale)
- Portage cedar_conteur sur reachy_care `conv_app_v2/llm/`
- Portage sur Aristote (reachy-noos)
