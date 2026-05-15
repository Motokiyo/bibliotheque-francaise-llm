# DECISIONS — conteur (Cedar storyteller)

## VALIDÉ

### 12/05/2026 — Sync timing : adaptive rate + fix byte_offset buffer DSP

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
