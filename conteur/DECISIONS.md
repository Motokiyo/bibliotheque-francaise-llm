# DECISIONS — conteur (Cedar storyteller)

## VALIDÉ

### 11/06/2026 — Lecture roman fiable via Realtime Cedar V1, segments courts anticipés

Le mode livre long (*L'île au trésor*) passe par OpenAI Realtime `gpt-realtime` + voix `cedar` V1, pas par l'endpoint Speech REST. La clé Reachy/Pollen récupérée via `HuggingFaceM4/gradium_setup` fonctionne pour Realtime mais pas pour `/v1/audio/speech` (`missing_scope api.model.audio.request`).

Architecture validée :
- texte source local structuré en chapitres JSON ;
- découpe déterministe en segments courts sur frontières de phrase/paragraphe ;
- progression sauvegardée en offsets source dans `localStorage` ;
- le segment suivant est demandé quand il reste environ 15 secondes d'audio local bufferisé, pour éviter un blanc audible ;
- l'avancement livre ne dépend plus du transcript ni de `response.done` seul : le navigateur tient l'autorité de lecture réelle via Web Audio ;
- bouton `Depuis sélection` reprend à l'offset source choisi sans snap arrière.

Test 11/06/2026 : test Playwright mobile 90 s validant 3 segments successifs du chapitre I (`0→776`, `776→1476`, `1476→2112`), statut `speaking`, progression sauvegardée à `charOffset=1476`, sans erreur console.

### 11/06/2026 — Déploiement pérenne : Vercel non recommandé pour cette version

Le conteur actuel nécessite un process serveur persistant FastAPI/Uvicorn, WebSocket Realtime durable, Web Audio côté client, et une clé OpenAI côté serveur. Vercel n'est donc pas le déploiement cible pour la version actuelle.

Déploiement recommandé : petit VPS, Fly.io, Render ou Railway avec Uvicorn persistant, HTTPS, variable `OPENAI_API_KEY`, volume/disque pour données locales et cache éventuel. Objectif : accès stable depuis Android sans dépendre de l'ordinateur d'Alexandre.

Option compatible avec les sites existants d'Alexandre : si le site est servi par un vrai serveur (VPS, reverse proxy, Docker, Node/Python backend), déployer le conteur sur un sous-domaine ou chemin protégé par code d'accès, avec proxy WebSocket vers Uvicorn. Si le site est statique/serverless pur, il ne suffit pas pour cette version.

### 12/05/2026 SOIR — DSP browser-side via SoundTouch streaming worklet

**Décision finale de la session** : abandon complet du DSP pyrubberband côté serveur, bascule vers un `AudioWorkletProcessor` streaming côté browser utilisant la lib `@soundtouchjs/audio-worklet@0.1.17`.

Raisons :
- Le DSP server-side appliquait le pitch/vibrato AVANT envoi au browser, donc les audio.delta arrivaient déjà traités côté browser. Or OpenAI Realtime streame ~5× plus vite que temps réel : le browser bufferise plusieurs secondes d'audio en avance dans `playbackTime`. Conséquence : impossible de synchroniser le changement de profil DSP avec ce que l'utilisateur entend, sans connaître la position exacte de lecture du browser.
- Le `prev_tail` du DSPBufferedProcessor produisait des glitchs audibles au crossfade entre profils très différents (Phèdre +1 → Œnone -1 = 2 demi-tons de saut).
- Le buffer 250 ms ajoutait une latence cumulée incompressible.

Architecture browser :
- `pitchNode` = `AudioWorkletNode('streaming-pitch-shifter')` — processor maison ajouté à la fin de `soundtouch-worklet.js`, réutilise les classes internes `SoundTouch`, `FifoSampleBuffer` du package. Pitch et tempo découplés. Latence interne ~50-150 ms (steady-state uniforme).
- `vibratoGain` = GainNode dont `gain.value` est modulée en AM par `vibratoLFO` (OscillatorNode) × `vibratoDepthGain`. Tremblement Œnone propre.
- `masterGain` = GainNode pour le `gain_db` du profil.
- Chaîne : `BufferSource → pitchNode → vibratoGain → masterGain → destination`.
- `applyPersoProfile(persoName)` lit l'annotation côté browser, ramps les params en 30 ms (`setTargetAtTime`) pour éviter les clics.

Fichiers : `soundtouch-worklet.js` (vendu + extension streaming maison), `soundtouch-audio-node.js` (vendu mais non utilisé — le helper pre-loaded buffer ne convient pas au streaming OpenAI), `app.js` (refonte chaîne audio), `server.py` (suppression de l'appel à `dsp_proc.feed()`).

### 12/05/2026 — Switch perso direct côté serveur (après abandon scheduling at_byte)

Au moment où la détection regex trouve un nouveau perso dans le transcript, le serveur applique **immédiatement** : `active_perso["name"] = new_perso`, `set_perso_antennas` via `asyncio.to_thread`, notification `perso.active` au browser.

Plus de `schedule_switch` + `at_byte` + `apply_switch` round-trip. Le browser reçoit `perso.active` et applique le profil DSP **localement** via `applyPersoProfile` (depuis 12/05 soir, c'est le SoundTouch worklet qui le porte).

### 12/05/2026 — Interruption immédiate

Bouton Interrompre :
- Browser : `killLocalAudio()` stoppe toutes les `BufferSource` actives via `src.stop(0)`, vide `chunkSchedule` et `pendingSwitches`, reset `playbackTime`. Appelé AVANT le `wsSend cancel` pour silence instantané sans attendre le round-trip serveur.
- Serveur : flag `cancelled["v"] = True`, drop tous les `audio.delta` et `audio_transcript.delta` en transit jusqu'au prochain `response.done`. `dsp_proc.reset()` (vestige), reset complet de `active_perso`, `transcript_buffer`, `audio_bytes_total`, `chars_recv_total`, `pending_schedule_ts`. Envoie `audio.cancel` au browser comme filet de sécurité.

Pattern à porter dans Reachy Care quand on intégrera `cedar_conteur`.

### 12/05/2026 — Logger JSONL structuré

`/tmp/cedar-conteur-trace-{int(time.time())}.jsonl` créé au démarrage du serveur. Events serveur (`audio_delta_emit`, `detect`, `apply_switch_recv`, `response_done`, `cancel_recv`) et browser (`audio_delta_browser`, `schedule_switch_recv`, `schedule_lookup`, `apply_switch_emit`) consolidés dans le même fichier via `POST /api/trace` (batched 200 ms côté browser).

Script `tools/analyze_trace.py` (stdlib seul, ~480 lignes) sort un rapport markdown : adaptive rate distribution, schedule lookup health, délais apply_switch, cohérence buffer_start_offset, silences > 200 ms, transitions perso bout-en-bout avec verdict.

### 12/05/2026 — Substitution texte des prononciations (court terme uniquement)

Cedar n'obéit pas au prompt `Reference Pronunciations`. Solution court terme : substituer `Aricie → A-ri-si` dans le texte poussé via `_apply_pronunciations()`. La détection perso continue de matcher sur le nom canonique grâce à `_unphonetize()` qui rewrite le transcript_buffer avant détection.

**Limite acceptée** : ne scale pas pour des milliers d'œuvres (curation par-œuvre). À remplacer par : (a) dictionnaire FR classique global automatiquement appliqué, ou (b) TTS découplé avec lexique phonétique IPA (option B du rapport DECOUPLAGE).

### 12/05/2026 — `@soundtouchjs/audio-worklet@0.1.17` vendu localement

- Téléchargé depuis unpkg avec autorisation explicite d'Alex (12/05/2026 soir).
- 2 fichiers dans `standalone/static/` : `soundtouch-worklet.js` (60 KB, ajouté à la fin un processor streaming maison `streaming-pitch-shifter`) et `soundtouch-audio-node.js` (16 KB, non utilisé pour notre cas streaming).
- Licence LGPL — compatible usage commercial Eiffel AI tant que la lib elle-même reste libre.
- Hashes SHA-256 :
  - `soundtouch-audio-node.js` : `1db8be9eb311b17771bce73b5e63b055b032abbf60cad122a179ffbd53763bf8`
  - `soundtouch-worklet.js` : `de7fe19ed9dc091c6c9f82d77dc0041f542ce8802f006449989f15e7e7bf4c4c` (avant modif, le streaming-pitch-shifter ajouté change ce hash)

### 12/05/2026 — Refus migration Rust (statu quo Python)

Le bottleneck du conteur n'est pas un bug de perf mais un bug d'algorithme (scheduling at_byte foireux). Rust ne corrige rien. Reachy Mini SDK est Python, OpenAI SDK officiel Python plus mature, `pyrubberband + numpy = 30-50 ms par chunk` confortable. Migration estimée 2-3 semaines sans bénéfice mesurable.

À reconsidérer si bottleneck DSP mesuré sur Pi5, multi-sessions parallèles serveur, ou phase production stabilisée pour binaire de prod.

### 12/05/2026 — Cedar V1 only, multi-projets (confirmation 11/05)

Cedar V1 (`gpt-realtime`) reste le défaut pour conteur + Reachy Care + tous projets persona Reachy/Douze. La voix `cedar` est inséparable du modèle de génération audio — OpenAI a rentraîné cedar entre août 2025 et mai 2026 pour le stack V2, donc Cedar V2 ≠ Cedar V1 en timbre. Pas de paramètre `voice_version` côté API.

Stratégie cohérence : tant que la persona Douze ne change pas, tous les projets restent sur V1. Découplage TTS long terme à creuser (rapport `docs/DECOUPLAGE_TTS_LONGTERME.md`).

### 12/05/2026 — Wiki Karpathy adopté pour le projet bibliothèque-francaise-llm

`MODE_HISTOIRE.md` (vision) + `README.md` (pointeur court) à la racine. Sous-projet `conteur/` a son propre wiki Karpathy complet (`CLAUDE.md`, `INDEX.md`, `STATE.md`, `DECISIONS.md`, `KNOWLEDGE_BASE.md`). DraCor corrigé 1560 → 1940 pièces, alerte licence NC commerciale documentée.

## ABANDONNÉ

### 12/05/2026 SOIR — Scheduling at_byte / mapping transcript→bytes audio

Le modèle conceptuel `at_byte = match_end_pos / rate * 48000` est fondamentalement faux dans le streaming Realtime :
- Le transcript arrive ~500 ms après l'audio correspondant (latence intrinsèque OpenAI Realtime).
- L'audio est envoyé ~5× plus vite que temps réel ; le browser bufferise plusieurs secondes d'avance.
- `audio_bytes_total` côté serveur (cumul émis) ne correspond pas à la position de lecture côté browser.

Mesuré sur trace : adaptive rate calculait 11-14 c/s (au lieu des 17 c/s annoncés), donc `at_byte` pointait loin dans le futur AudioContext (12-71 sec d'attente avant apply_switch). Switch jamais appliqué dans le bon timing.

Remplacé par : **switch direct côté serveur + DSP browser-side**.

### 12/05/2026 SOIR — DSP server-side pyrubberband

Abandonné pour les raisons ci-dessus (impossible à synchroniser) + glitchs `prev_tail` au crossfade. Le module `cedar_conteur/dsp.py` reste mais n'est plus appelé. À nettoyer si on confirme la stabilité du browser-side.

### 12/05/2026 SOIR — 2-phase narrator/perso switch (narrator avant le nom, perso après)

Idée : appliquer le profil "narrator" (pitch 0, antennes 0/0) AVANT que Cedar lise "ŒNONE.", puis profil "ŒNONE" APRÈS. Demande un timing audio→transcript exact que le serveur n'a pas. Reporté en P1, à reprendre quand on a un signal de lecture en temps réel côté browser.

### 12/05/2026 SOIR — Substitution prononciation par-œuvre comme stratégie pérenne

Marche court terme pour Phèdre (5 prononciations), pas pour une bibliothèque de milliers d'œuvres. À remplacer par dictionnaire global FR classique OU TTS découplé.

### 12/05/2026 — `--reload` était absent du process uvicorn lancé manuellement par Alex

PID 37773 lancé hier soir sans `--reload`, n'a jamais rechargé le code malgré mes modifs. Symptôme : `POST /api/trace → 404` × ~80. Résolu en tuant le process et relançant via `./run.sh`. Convention : utiliser **toujours** `./run.sh` pour lancer le serveur conteur (inclut `--reload`).

### 12/05/2026 — Tentative MIN_CHARS_FOR_ADAPTIVE=60 pour stabiliser adaptive rate

Insuffisant : à 60 chars on a 3-4 sec d'audio, mais le transcript est encore en retard, donc rate calculé reste sous-estimé. Abandonné avec tout le scheduling at_byte.

### 11/05/2026 — Modèle et voix

**Solution B (mesure adaptative chars/sec)** retenue contre A (fix-only) et C (timestamps OpenAI). Choisie pour rester **générique** : aucune dépendance sur DraCor, le contenu, ou la source de texte. Marche pour tout contenu Cedar. Le conteur reste plus central que les sources qu'il consomme.

Implémentation dans `standalone/server.py` uniquement (préserve la portabilité de `cedar_conteur/`) :
- `chars_recv_total` tracké par réponse, incrémenté à chaque `audio_transcript.delta`
- Dès `chars_recv_total >= 60` ET `audio_bytes_total > 0` : `rate = chars_recv_total / (audio_bytes_total / 48000)`
- Sinon : fallback à `SPEAKING_RATE_CHARS_PER_SEC = 17.0` (la constante précédente)
- Reset à `response.done`

### 12/05/2026 — Fix structurel byte_offset du buffer DSP

Le bug : `DSPBufferedProcessor.feed()` accumulait ~6 chunks bruts (250 ms) avant de flusher un bloc. Le serveur envoyait ce bloc avec `byte_offset = chunk_start_offset` du **dernier** chunk brut (celui qui a déclenché le flush), pas du **premier** chunk brut accumulé. Le browser ne trouvait jamais le bon chunk dans son `chunkSchedule` (le startByte était décalé de 240 ms en avant) → switch en `pendingSwitches` indéfiniment.

Fix : maintenir `dsp_buffer_state["start_offset"]` au scope du handler WS. Premier chunk dans buffer vide → init au `chunk_start_offset`. Quand `feed()` retourne un bloc → émettre avec cet offset, reset. Aussi appliqué dans les flushes manuels (`apply_switch` et `response.done`).

Pas de modification de `cedar_conteur/dsp.py` (préserve la portabilité du module).

### 12/05/2026 — Cedar V1 only, multi-projets (confirmation 11/05)

Cedar V1 (`gpt-realtime`) reste le défaut pour le conteur, Reachy Care, et tous projets persona Reachy/Douze. Pas de migration V2 tant que la persona ne change pas. La voix est inséparable du modèle (rentraînée sur stack audio V2 entre août 2025 et mai 2026), donc impossible d'avoir "Cedar V1 sur gpt-realtime-2". Décision stratégique : la cohérence vocale prime sur les features V2 (reasoning, parallel_tool_calls, contexte 128k) tant qu'on n'a pas un besoin produit explicite.

Référence : `/IA/Modeles/openai-realtime-2-bible-vocale.md §1`.

### 12/05/2026 — Pas de migration Rust, on reste Python

Le bottleneck actuel n'est PAS un bug de perf (pyrubberband + numpy = 30-50 ms par chunk, largement sous les 250 ms du buffer DSP). C'est un bug d'algorithme (chars/sec constant + byte_offset mal transmis). Rust ne corrige aucun des deux.

Coût migration estimé : 2-3 semaines à un dev, pendant lesquelles zéro itération UX. Reachy Mini SDK est Python, OpenAI SDK Python officiel mature > SDK Rust communautaire. Compromis intelligent si un point chaud DSP émerge un jour : réécrire UN module Rust isolé via PyO3 (juste `apply_profile`), garder le reste Python. Pattern NumPy/Pandas.

À reconsidérer si :
- Bottleneck DSP mesuré sur Pi5 (pas hypothétique)
- Plusieurs sessions parallèles dans un produit serveur (multi-clients EHPAD)
- Phase production stabilisée demande un binaire de prod robuste

### 11/05/2026 — Modèle et voix
- **Cedar V1 (`gpt-realtime`) par défaut pour tous projets persona Reachy/Douze** (conteur, reachy_care, futurs voice agents). Décision Alex après A/B test : timbre V1 plus chaud, plus narratif, alors que V2 a été rentraîné. Cohérence multi-projets = un client doit toujours entendre la même voix.
- **Voix `cedar`** exclusivement (vs `marin` ou autres).
- **`reasoning.effort` et `parallel_tool_calls`** : V2-only, conditionnellement appliqués dans `adapter.py` pour éviter `Unsupported option for this model`.

### 11/05/2026 — DSP audio pyrubberband
- Pipeline DSP côté serveur Python (`cedar_conteur/dsp.py`).
- **Crispness=6** (mieux pour la voix que défaut 5).
- **Buffering 250ms** par chunk avant DSP (sinon scratch sur chunks 40ms).
- **Crossfade 5ms** entre chunks consécutifs DSP-traités.
- Profils par perso : pitch_shift (±3 demi-tons typique, -2 fort), speed_hint, vibrato_hz/depth, gain_db.

### 11/05/2026 — Antennes Reachy
- Plage UI : ±156° (= ±2.73 rad, max hardware Reachy Mini).
- SDK reachy_mini installé localement, mode `sim-attached` préféré (Tauri viewer 3D actif) → fallback `sim-spawned` (notre daemon) → fallback `mock` (logs seulement).
- **Spawn daemon Pollen via `/bin/sh -c env -i`** : isolation totale d'env, sinon SIGSEGV à cause de la pollution `pyrubberband/gstreamer_python` (11+ vars d'env).
- Patch `args.no_media = True` quand `mockup_sim=True` dans `reachy_mini/daemon/app/main.py:create_app` : workaround SIGSEGV `gst_device_monitor_stop` sur macOS sans USB Reachy.
- `sitecustomize.py` dans le venv Pollen pour ré-appliquer le patch automatiquement après une éventuelle update Pollen.

### 11/05/2026 — Annotations
- Noms personnages **avec accents originaux** (PHÈDRE, ŒNONE, THÉSÉE, THÉRAMÈNE, ISMÈNE) pour match exact avec DraCor (sans normalisation requise).
- Normalisation NFD + ligatures (`Œ → OE`, `Æ → AE`) côté serveur ET côté JS pour robustesse.
- Profils Phèdre seedés pour 8 persos complets (ajout ISMÈNE + PANOPE).
- "Cheveux longs femmes" : antennes basses asymétriques (L négatif, R positif). Aricie 2.73 rad, Phèdre 2.60, Œnone 2.41, etc.

### 11/05/2026 — Détection perso
- Regex stricte ancrée début de ligne + séparateur `[.:]` obligatoire après le nom (+ paranthèse optionnelle).
- Préfixe accepté : `\A | \n\n | [.?!…\d]\s+` (start, ligne vide, ponctuation forte, ou chiffre+espace pour en-têtes scène).
- **Filtre `scene_speakers`** : restriction aux persos qui parlent dans la scène en cours (populé par tool `load_scene`).
- Pool de détection : `scene_speakers` si défini, sinon tous les persos de l'œuvre (`by_character_structured`).
- Détection insensible aux accents/ligatures via `_strip_accents`.

### 11/05/2026 — Sync timing browser
- **Le browser tient l'autorité timing audio** : chaque audio.delta porte un `byte_offset` cumulé, le browser maintient `chunkSchedule[]` (startByte, endByte, startTime Web Audio, duration).
- Quand serveur détecte switch perso, il envoie `schedule_switch(perso, at_byte)`. Le browser calcule `playTime = chunk.startTime + (at_byte - chunk.startByte) / 48000` et fait `setTimeout` pour envoyer `apply_switch` quand l'audio sort vraiment du speaker.
- Serveur reçoit `apply_switch` → applique antennes + flush DSP en sync parfait.

### 11/05/2026 — Récupération clé OpenAI
- Mécanisme officiel Pollen via HF Space `HuggingFaceM4/gradium_setup`, endpoint `/claim_b_key`, auth par `HF_TOKEN` local.
- Permet récupération depuis n'importe quelle machine sans accès au Pi (cf. démo Bordeaux).

## ABANDONNÉ

### 11/05/2026 — Tool `set_speaker` par réplique
Idée : Cedar appelle `set_speaker(name)` avant chaque réplique → signal explicite, zéro parsing transcript.
**Abandonné** : chaque tool call provoque un `response.done` côté OpenAI Realtime → pause + nouveau tour → Cedar marque une pause sonore à chaque transition de perso. Anti-pattern pour lecture continue.

### 11/05/2026 — Délai statique calculé via chars/sec
Délai = `match_pos / 17 chars/sec - audio_received_seconds`.
**Abandonné** : Cedar change de débit selon les persos (Œnone lente, Ismène vive). L'estimation moyenne 17 chars/sec ne tient pas. Solution adoptée : timing via browser (`chunkSchedule`).

### 11/05/2026 — Spawn daemon Pollen avec env complet
**Abandonné** : SIGSEGV à cause de `pyrubberband/gstreamer_python` installés dans notre venv qui polluent 11+ vars d'env (PATH, PYTHONPATH, GST_*, GI_TYPELIB_PATH, etc.) pointant vers Python 3.13. Solution : `/bin/sh -c env -i` (isolation totale).

### 11/05/2026 — `--preload-datasets` comme cause unique du crash daemon
Fausse piste initiale. La vraie cause = `gst_device_monitor_stop` dans la stack GStreamer sur Mac M1 sans USB Reachy hardware. Workaround : `args.no_media = True` quand `mockup_sim=True`.

### 11/05/2026 — Wrapper bash `python3` pour filtrer args Tauri
**Abandonné** : `uv-trampoline` (binaire Astral) bypasse le symlink et appelle directement le vrai binaire Python → wrapper bash jamais exécuté. Solution adoptée : patch direct `main.py` + `sitecustomize.py`.

### 11/05/2026 — App Tauri Pollen viewer 3D pour démo standalone
**Pas abandonné mais limité** : l'app Tauri crash systématiquement au démarrage (bug Pollen côté front, daemon Python par lui-même fonctionne). Le viewer 3D est compilé dans le binaire Tauri → inextractible. Workaround actuel : on patch le code Python du daemon pour éviter le crash, Tauri peut alors afficher son viewer. Si Pollen update et casse, on a `sitecustomize.py` en filet.

### 11/05/2026 — Cedar V2 (`gpt-realtime-2`)
**Reporté** : pas utilisé pour le conteur ni futurs projets Reachy tant que le timbre Cedar V2 n'est pas validé par Alex. Disponible dans le dropdown UI pour A/B mais pas par défaut.

## BUGS OUVERTS (à reprendre demain en mode plan)

- Après ~3 répliques, le sync timing perso se perd (pitch et antennes). Probablement cumul d'erreurs dans le mapping `byte_offset` vs réalité du débit Cedar variable. Alex demande une session plan dédiée à comprendre le pipeline audio.
- Cedar concatène le transcript en mono-ligne (pas de `\n\n`) → la regex stricte du pattern manque parfois des transitions.
- Le flush DSP au switch peut produire des micro-glitches audibles.
