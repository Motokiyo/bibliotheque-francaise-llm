# KNOWLEDGE_BASE — conteur

Contraintes techniques validées terrain. Référence canonique pour tout projet voix d'Alexandre : `/4 Ressources/Outils-IA/Modeles/openai-realtime-2-bible-vocale.md`.

## §1 — OpenAI Realtime API

### Modèles
- `gpt-realtime` (V1, août 2025) : 32k contexte, **cedar V1 (timbre chaud, validé Alex)**, pas de reasoning_effort, pas de parallel_tool_calls.
- `gpt-realtime-2` (V2, 07/05/2026) : 128k contexte, reasoning.effort (minimal|low|medium|high|xhigh), parallel_tool_calls, mais cedar V2 retrainé (timbre différent).

### Voix
- 10 voix : `alloy`, `ash`, `ballad`, `coral`, `echo`, `sage`, `shimmer`, `verse`, **`marin`**, **`cedar`**.
- Marin et Cedar sont exclusives Realtime. **Cedar V1 = défaut multi-projets Alex** pour persona Reachy/Douze.
- **VERROUILLAGE** : la voix est figée au premier audio output. `session.update(voice=...)` doit être confirmé par event `session.updated` AVANT le premier mic input. Sinon défaut (`marin` en V2, `alloy` en V1) se verrouille pour toute la session.

### Paramètres bannis
- `temperature` : abandonné sur V2, présent legacy sur V1.
- `session.preamble` : n'existe pas, rejeté avec `Unknown parameter`. Configurer les préambules audibles via section du prompt système.
- `parallel_tool_calls` : V2-only, `Unsupported option for this model` sur V1 — gérer conditionnellement.
- SSML : pas supporté.

### Format session.update validé conteur
```python
{
    "type": "realtime",
    "instructions": SYSTEM_PROMPT,
    "output_modalities": ["audio"],
    "audio": {
        "input": {
            "format": {"type": "audio/pcm", "rate": 24000},
            "noise_reduction": {"type": "far_field"},
            "transcription": {"model": "gpt-4o-transcribe", "language": "fr"},
            "turn_detection": {
                "type": "semantic_vad", "eagerness": "low",
                "create_response": True, "interrupt_response": True,
            },
        },
        "output": {
            "format": {"type": "audio/pcm", "rate": 24000},
            "voice": "cedar", "speed": 0.92,
        },
    },
    "tools": CONTEUR_TOOLS, "tool_choice": "auto",
}
# Si V2: ajouter "reasoning": {"effort": "medium"}, "parallel_tool_calls": True
```

### Récupération clé Pollen hors-Pi
```python
from gradio_client import Client
import os
os.environ["HF_TOKEN"] = open(f"{os.path.expanduser('~')}/.cache/huggingface/token").read().strip()
client = Client("HuggingFaceM4/gradium_setup", verbose=False)
key, status = client.predict(api_name="/claim_b_key")
# → 'Key provided.' sk-proj-XXX (164 chars)
```

## §2 — Reachy Mini SDK

### Connection 3-tier (validé conteur)
1. **Tier 1 — attach** : `ReachyMini(use_sim=True, spawn_daemon=False)` connecte au daemon existant (app Tauri ou nôtre).
2. **Tier 2 — spawn** : si pas de daemon, on spawne via `/bin/sh -c env -i ... "python3" -m reachy_mini.daemon.app.main --mockup-sim` (isolation env obligatoire).
3. **Tier 3 — mock** : si tout échoue, logs only (no 3D).

### SIGSEGV macOS sans USB Reachy
- **Cause** : `gst_device_monitor_stop` dans la stack GStreamer (PyGObject) plante quand aucun USB Reachy n'est dispo et que `--no-media` n'est pas passé.
- **Workaround** : forcer `args.no_media = True` quand `args.mockup_sim` dans `reachy_mini/daemon/app/main.py:create_app`.
- **Pérennité** : `sitecustomize.py` dans `<pollen_venv>/lib/python3.12/site-packages/sitecustomize.py` qui ré-applique le patch via monkey-patch `__import__`.

### Env pollué par pyrubberband
- `pyrubberband + gstreamer_python` (installés via pip dans le venv conteur) injectent ~11 vars d'env via `gstreamer_bundle.pth` au démarrage Python : PATH, PYTHONPATH, GIO_EXTRA_MODULES, GI_TYPELIB_PATH, GST_PLUGIN_*, PYGI_DLL_DIRS, XDG_*.
- Ces vars contaminent les subprocess Python lancés depuis le conteur → SIGSEGV ABI Python 3.13 vs 3.12 Pollen.
- **Solution** : `subprocess.Popen(["/bin/sh", "-c", "exec env -i ... python3 -m ..."])` avec `stdout=open(file)` (pas DEVNULL, qui pose problème avec `start_new_session=True`).

### App Tauri Pollen
- L'app Tauri (`/Applications/Reachy Mini Control.app`) crash au démarrage avec `Process exited with code: 1` côté daemon Python (SIGSEGV GStreamer).
- Le viewer 3D Tauri est compilé dans le binaire Rust → inextractible.
- Avec notre patch `no_media=True`, le daemon Pollen ne crash plus, et l'app Tauri peut afficher son viewer 3D.

### Antennes
- Plage hardware : ±2.73 rad = ±156°.
- API : `mini.set_target(antennas=[left_rad, right_rad])`.

## §3 — Prompting Cedar conteur

### Squelette officiel (cookbook OpenAI)
Sections : Role & Objective | Personality & Tone | Language | Voice and Characters | Pacing | Reference Pronunciations | Preambles | Variety | Conversation Flow | Safety.

### Modulation par personnage
- Cedar ne supporte pas SSML, pas de paramètre `pitch` numérique.
- Marqueurs inline reconnus : **didascalies entre parenthèses** `(à voix basse)`, `(grave)`, `(soupire)` et **points de suspension** pour les pauses.
- Cedar dérive vers la baseline après 5-10 turns sans rappel persona.
- Max 3-4 persos stables en parallèle.

### Préambules audibles
- Pas via param API. Via section dédiée du prompt système.
- Wording cookbook : *"Before calling a tool that takes >300ms, ALWAYS say a short, natural acknowledgement. Vary the phrasing. Keep it under 6 words."*

### Tool `set_speaker` — INTERDIT pour lecture continue
- Test 11/05/2026 : chaque tool call provoque `response.done` → pause sonore.
- Pour signaler le perso actif, utiliser la **détection transcript** (regex stricte + timing browser).

## §4 — DSP audio (pyrubberband)

### Pipeline
1. Audio Cedar arrive en chunks ~40ms (`response.audio.delta`).
2. `DSPBufferedProcessor.feed()` accumule jusqu'à **250ms** (12000 bytes PCM 24kHz mono).
3. `apply_profile()` applique pitch_shift + time_stretch + vibrato + gain avec `rbargs={"-c": "6"}` (crispness).
4. **Crossfade 5ms** sur les bordures avec le `prev_tail` du chunk précédent.
5. Output renvoyé au browser.

### Profils par perso
- `pitch_shift` (-3 à +3 demi-tons, perceptible à partir de ±0.5)
- `speed` (0.7 à 1.3, hors DSP via `audio.output.speed`)
- `vibrato_hz` (0-5 Hz) + `vibrato_depth` (0-0.15) pour tremblements vieillards
- `gain_db` (-6 à +3)

### Limites
- Buffer 250ms = +250ms de latence sur le DSP (acceptable en narration, pas en conv libre).
- Le rapport `rbargs={"-c": "6"}` doit être un **dict** (pas une liste), sinon `'list' object has no attribute 'setdefault'`.

## §5 — Détection perso transcript

### Regex finale (11/05/2026 fin de session)
```python
pat = re.compile(
    rf"(?:\A|\n\n+|[.?!…\d]\s+){escaped_name}[ \t]*(?:\([^)]*\))?[ \t]*[.:][ \t]*(?:\n|\Z|[A-ZÉÈÊÀÂÎÏÔÛÙÇ])",
)
```
- Préfixe obligatoire : start of string, ligne vide, ponctuation forte+espace, ou chiffre+espace (en-tête scène).
- Séparateur `[.:]` obligatoire après le nom (sinon false positives sur vocatifs).
- Suivi de fin de ligne OU début de la réplique en majuscule.

### Normalisation
- `_strip_accents` (NFD + retrait `Mn`) côté Python.
- Ligatures `Œ → OE`, `Æ → AE` traduites avant NFD.
- Comparaison en `.upper()`.

### Filtres
- `scene_speakers` : populé par tool `load_scene`, restreint les persos éligibles à ceux qui parlent vraiment dans la scène en cours.
- Fallback : tous les persos `by_character_structured` si pas de scène active.

## §6 — Sync browser timing (refonte 12/05/2026)

### Architecture (à jour après fix sync)

Trois espaces de coordonnées à ne pas confondre :

| Espace | Unité | Qui le gère |
|---|---|---|
| Bytes OpenAI bruts | `audio_bytes_total` cumulés (PCM 24k = 48000 B/s) | serveur, sert au mapping char transcript → at_byte |
| Bytes DSP-traités | `bytes.byteLength` du `data` envoyé | wire serveur↔browser, taille modifiée par `time_stretch` |
| Temps AudioContext | `audioCtx.currentTime` / `startTime` | browser, autorité absolue pour `apply_switch` |

Chaîne :

1. `response.audio.delta` arrive serveur. Si DSP actif : `dsp_proc.feed(raw, profile)` accumule pendant ~6 chunks bruts puis flushe un bloc.
2. **Tracking `dsp_buffer_state["start_offset"]`** : initialisé au `chunk_start_offset` du premier chunk entrant dans un buffer vide. Reste figé pendant l'accumulation. Reset après émission.
3. Quand un bloc sort de `feed()` (ou des flushes manuels à `apply_switch` / `response.done`) : émettre `{audio.delta, data, byte_offset: dsp_buffer_state["start_offset"], perso}`.
4. `response.audio_transcript.delta` : `chars_recv_total += len(text)`. Quand un nouveau perso est détecté :
   - `rate = chars_recv_total / (audio_bytes_total / 48000)` si `chars_recv_total >= MIN_CHARS_FOR_ADAPTIVE` (60), sinon fallback 17
   - `at_byte = int(match_end_pos / rate × 48000)` → envoyer `{schedule_switch, perso, at_byte}` au browser
5. Browser `playAudioDelta` : décode b64 → AudioBuffer 24k → `src.start(playbackTime)` → `chunkSchedule.push({startByte: byteOffset, endByte: byteOffset + bytes.byteLength, startTime, duration})`
6. Browser `scheduleSwitch` : cherche le chunk qui couvre `at_byte` → `playTime = startTime + (at_byte - startByte) / 48000` → `setTimeout(apply_switch)` au moment réel speaker
7. Serveur `apply_switch` reçu : `active_perso = new`, antennes Reachy via `RobotController.set_perso_antennas`, flush DSP buffer (tail avec ancien profil), `perso.active` notifié

### Logger structuré (instrumentation)

Trace JSONL par session dans `/tmp/cedar-conteur-trace-{ts}.jsonl`, alimentée par serveur et browser (browser bat via POST `/api/trace` toutes les 200 ms). Events :

- `audio_delta_emit` (serveur) : `chunk_raw_offset, buffer_start_offset, raw_size, processed_size, perso, dsp`
- `detect` (serveur) : `match_end_pos, new_perso, audio_bytes_total, chars_recv_total, rate_used, rate_source, at_byte, transcript_len`
- `apply_switch_recv` (serveur) : `perso, ms_since_schedule`
- `response_done` (serveur) : `audio_bytes_total, chars_recv_total, transcript_len, pending_count`
- `audio_delta_browser` : `byteOffset, endByte, startTime, duration, audioCtxCurrentTime`
- `schedule_switch_recv` : `perso, at_byte`
- `schedule_lookup` : `perso, at_byte, chunk_found, gap_to_nearest_endByte` (ou playTime, currentTime, delayMs si trouvé)
- `apply_switch_emit` : `perso, currentTime, scheduled_playTime`

### Limites résiduelles connues

- **time_stretch sur le mapping** : si profil avec `speed != 1.0` (ex. Œnone 0.90), le bloc DSP-processed a une longueur différente de `raw_size`. Côté browser, `endByte = startByte + bytes.byteLength` couvre l'espace DSP, alors que `at_byte` est calculé en espace raw. Déviation 5-15% selon profil. Acceptable en V1 ; correction propre = envoyer `byte_size_raw` séparé.
- **Cedar mono-ligne** (P0 #2) : regex de détection rate des transitions quand Cedar streame sans `\n` ni ponctuation forte.
- **prev_tail crossfade entre profils différents** (P0 #3) : artefact au switch DSP, à reset à zéros au changement de profil.

## §7 — DraCor TEI parsing

### Endpoints
- `GET /api/v1/corpora/fre` : 1940 pièces FR.
- `GET /api/v1/corpora/fre/plays/<id>` : metadata (auteur, date, genre, characters list).
- `GET /api/v1/corpora/fre/plays/<id>/tei` : XML TEI complet (Accept text/xml, default).
- `GET /api/v1/corpora/fre/plays/<id>/spoken-text-by-character` : JSON (avec `Accept: application/json`).
- `GET /api/v1/corpora/fre/plays/<id>/stage-directions` : refuse JSON (seul `text/plain` accepté).

### TEI structure
- `<div type="act" n="N">` ... `</div>`
- `<div type="scene" n="N"> <head>...</head> <stage>...</stage> <sp who="#perso"> <l>...</l> </sp> </div>`
- `<sp>` contient `<l>` (vers) ou `<p>` (prose).

### Format poussé à Cedar
On strippe toutes les `<stage>` et `<head>` pour ne pousser que `ACTE N — SCÈNE N\n\nPERSO.\n  vers\n  vers\n\nPERSO2.\n  vers...`. Sans didascalies entre parenthèses → Cedar n'a rien d'autre à lire que les noms + répliques.

## Sources externes

- Bible vocale canonique : `/4 Ressources/Outils-IA/Modeles/openai-realtime-2-bible-vocale.md` (13 sections)
- MAP.md reachy_care : `1 Projets/reachy_care/app/MAP.md §8 CONFIG OPENAI REALTIME`
- VOIX_CEDAR_MODE_HISTOIRE.md reachy_care (mars 2026 + errata 11/05/2026)
