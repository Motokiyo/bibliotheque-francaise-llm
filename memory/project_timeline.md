# Project Timeline — bibliotheque-francaise-llm / conteur Cedar

Une ligne par événement marquant. `+` ajout/avancée, `-` blocage/abandon, `=` statut, `★` décision durable.

## 2026-05-11 (création du sous-projet conteur, par Alex en session précédente)

- `+` Création du sous-projet `conteur/` (banc de test mode histoire Cedar pour Reachy Mini), module Python portable `cedar_conteur/` + serveur FastAPI standalone + UI vanilla HTML/JS.
- `+` 8 personnages Phèdre seedés avec annotations (pitch, vibrato, speed, antennes).
- `+` Wiki Karpathy conteur initialisé : CLAUDE / INDEX / STATE / DECISIONS / KNOWLEDGE_BASE.
- `★` Cedar V1 (`gpt-realtime`) verrouillé comme défaut multi-projets persona Douze (Reachy Care, conteur, futurs voice agents). Cedar V2 timbre différent, rentraîné par OpenAI sur stack audio V2.
- `=` 3 P0 ouverts en fin de session : drift sync timing après ~3 répliques, transcript Cedar mono-ligne, micro-glitches DSP au switch.

## 2026-05-12 matin (session compréhension pipeline)

- `+` Audit ouvert sur worktree `compassionate-johnson-f75c8a`. Confirmation que DraCor fre a 1940 pièces (pas 1560) via API metadata directe.
- `+` Corrections doc bibliothèque : 1560→1940 dans README, MODE_HISTOIRE, audit-sources, sources/DRACOR. Alerte licence CC BY-NC-SA 4.0 NC bloquante pour Eiffel AI commercial.
- `+` Lecture exhaustive du code conteur (matin) : adapter, library, annotations, dsp, prompts, robot, server, app.js, mic-worklet, index.html.
- `+` Mode plan activé. 4 problèmes structurels Alex : (1) sync voix+antennes comme un acteur, (2) Cedar V1/V2 stabilité long terme, (3) lecture longue interruptible avec pauses logiques, (4) migration Rust ?
- `★` Réponses : Cedar V1 only confirmé. Pas de Rust pour l'instant (bug d'algo pas de perf). Lecture longue → spec `reachy_care/CHUNKING_BOOKS.md` à porter. Sync = voir solution B mesure adaptative chars/sec.
- `+` Solution B implémentée (matin) : adaptive rate + fix byte_offset DSP buffer + logger JSONL structuré + endpoint `/api/trace` + script `tools/analyze_trace.py` (480 lignes stdlib).
- `+` Code review par sous-agent feature-dev:code-reviewer. 4 issues HIGH/MEDIUM corrigées : H1 clear pending dans apply_switch, H2 reset dsp_buffer_state au toggle, M3 rename gap_to_nearest_endByte, M4 suppression dead code _delayed_switch.
- `+` Sous-agent recherche TTS long terme → rapport `docs/DECOUPLAGE_TTS_LONGTERME.md`. Recommandation : Option B (OpenAI text-only → ElevenLabs Pro Voice Clone via LiveKit). Plan B juridique : cloner un comédien francophone (300-800 €) au timbre Cedar.
- `+` PR #1 ouverte sur GitHub : https://github.com/Motokiyo/bibliotheque-francaise-llm/pull/1 (2 commits : doc bibliothèque + sous-projet conteur).

## 2026-05-12 après-midi (premier test + diagnostic drift)

- `-` Test sur Phèdre. Alex relance le serveur. Constat : pitch et antennes drift après 2-3 répliques. Pas de pitch au bon moment, antennes bloquées.
- `-` Diagnostic : PID 37773 (ancien serveur lancé hier soir sans `--reload`) tournait toujours, n'avait PAS chargé mes fixes. `POST /api/trace → 404` × 80 fois dans le log.
- `★` Convention validée : utiliser **toujours** `./run.sh` (qui inclut `--reload`) pour lancer le serveur conteur. Lancement manuel uvicorn sans `--reload` = code figé.
- `+` Kill 37773 + relance via `./run.sh` (en arrière-plan dans la session Claude). Nouveau trace file actif.
- `+` Trace analysée : 3 detect seulement, `chars/sec=11-12` (au lieu de 17), `delayMs apply_switch` 12-71 sec ! Le scheduling at_byte pointe loin dans le futur AudioContext, browser attend, switch raté.

## 2026-05-12 soir (pivot complet DSP browser-side)

- `★` **Cause racine** identifiée : transcript en retard ~500 ms sur audio + OpenAI streame ~5× plus vite que temps réel + browser bufferise. Tout mapping at_byte = match_pos / rate est conceptuellement faux.
- `-` Abandon du scheduling at_byte. Abandon du 2-phase narrator/perso switch (demande timing exact qu'on n'a pas server-side).
- `+` Implémentation switch direct serveur : `active_perso = new_perso` immédiat à la détection, antennes via `asyncio.to_thread`, notification `perso.active` au browser. 12 detect appliqués (vs 3 avant), antennes bougent vraiment.
- `+` Fix interruption immédiate : `killLocalAudio()` browser stoppe BufferSource avec `src.stop(0)`, vide schedules. Côté serveur, flag `cancelled["v"]` drop tous audio.delta en transit. Envoie `audio.cancel` au browser comme filet de sécurité.
- `+` Fix glitchs (réponse au reviewer M2/M3) : `dsp_proc.reset()` après flush pour clear `prev_tail` au switch.
- `-` Substitution texte prononciation (Aricie → A-ri-si). Rejetée par Alex : ne scale pas pour milliers d'œuvres. Gardée en V1 minimal mais à remplacer par dictionnaire FR classique global OU TTS découplé.
- `-` Test toujours pas satisfaisant : antennes s'arrêtent encore après ~3 transitions. Logs explicites `→ antenna call` / `← antenna done` ajoutés pour diagnostic.
- `★` **PIVOT FINAL** : abandon DSP server-side pyrubberband. Bascule sur DSP **browser-side via SoundTouch streaming worklet**.
- `+` Téléchargement autorisé par Alex de `@soundtouchjs/audio-worklet@0.1.17` (LGPL) depuis unpkg. 2 fichiers vendus dans `standalone/static/`.
- `+` Ajout d'un AudioWorkletProcessor `streaming-pitch-shifter` à la fin de `soundtouch-worklet.js`. Réutilise les classes internes `SoundTouch` et `FifoSampleBuffer` du package. Mode streaming push/pull (vs buffer pre-loaded du processor original).
- `+` Refonte chaîne audio browser : `BufferSource → pitchNode (SoundTouch) → vibratoGain (modulé AM par LFO) → masterGain → destination`. Tous les params ramped 30 ms via `setTargetAtTime`.
- `+` `applyPersoProfile(persoName)` côté browser lit `currentAnnotations` localement, update les params du worklet sur event `perso.active`.
- `+` Serveur : suppression de l'appel à `dsp_proc.feed()` côté `relay_to_browser`. Le serveur envoie raw PCM, le browser applique le DSP au moment de la lecture.
- `★` Architecture audio finale validée structurellement : pitch + tempo découplés (vraie qualité SoundTouch), latence interne ~50-150 ms uniforme, plus de glitchs au crossfade entre profils.
- `=` Tests fonctionnels du soir non terminés (à reprendre demain 13/05). Antennes restent à diagnostiquer.
- `+` Wiki conteur mis à jour exhaustivement : STATE (pivot complet), DECISIONS (toutes les validations/abandons du jour), KNOWLEDGE_BASE (§1bis Cedar V1/V2, §13 SoundTouch streaming, §14 référentiels temporels, §15 résumé découplage TTS), INDEX (tools/ + docs/).
- `+` MemPalace KG : 18 faits durables ajoutés (status, validated_for, abandoned_for, pinned_version, replaces, disabled_on, required_before).

## 2026-06-11 (lecteur roman Android pour Galiléo)

- `+` Ajout de *L'île au trésor* en livre local JSON chapitré.
- `+` Mode livre mobile : contrôles chapitre précédent/suivant, démarrer/reprendre, pause, stop, reprise depuis sélection, progression locale par offset source.
- `★` Lecture roman fiable via Realtime `gpt-realtime` + voix `cedar` V1. Speech REST abandonné pour cette clé (`missing_scope api.model.audio.request`).
- `★` Enchaînement sans blanc : le segment suivant est demandé quand il reste environ 15 s d'audio local bufferisé, pas après silence complet.
- `+` Test Playwright mobile 90 s : trois segments successifs du chapitre I (`0→776`, `776→1476`, `1476→2112`), statut `speaking`, progression sauvegardée.
- `★` Déploiement pérenne : éviter Vercel statique/serverless pur. Cible : VPS/Fly.io/Render/Railway ou site existant avec backend persistant + proxy WebSocket + HTTPS + code d'accès.
- `+` Déploiement final sur Hetzner : DNS `conteur.eiffelai.io -> 89.167.3.104`, HTTPS Let's Encrypt, Nginx WSS reverse proxy, Basic Auth, Uvicorn local-only `127.0.0.1:7860`, `conteur.service` actif.
- `★` Sécurité production : Basic Auth user `Galiléo`, robot désactivé, `/api/tts` legacy désactivé, app sans accès direct à `/root/vault`.
- `+` UI famille : design Eiffel AI responsive, étagère `Lectures en cours`, bascule entre livres, progression par livre, démarrer/reprendre, pause, stop, chapitres précédent/suivant, reprise depuis sélection.
- `+` Bucéphale live : sync root vault → `/srv/conteur/live/chroniques-de-bucephale` via `conteur-bucephale-sync.timer`, bandeau nouveau chapitre côté app.
- `+` Livres ajoutés/audités : *Lancelot*, *Yvain*, *Perceval*, *Tristan et Iseut*, *Les Voyages de Gulliver*, plus *L'île au trésor* et *Bucéphale* live.
- `+` Script `conteur/scripts/audit_books.py` ajouté et exécuté côté serveur ; audit OK sur 6 livres publics importés.
- `=` Wrap : production accessible à la famille, reste recommandé de faire un test Android long 15-30 min et d'ajouter un contrôle transcript-vers-source si besoin de preuve formelle anti-sauts.
