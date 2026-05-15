// Cedar Conteur — browser client.
// Mic capture at 24kHz mono PCM16 via AudioWorklet, WebSocket bridge to FastAPI,
// playback of Cedar audio deltas via Web Audio scheduled queue.

const $ = (id) => document.getElementById(id);
const setStatus = (s) => { const e = $("status"); e.className = "status " + s; e.textContent = s; };

// ── Trace logger: batches events client-side, flushes every 200ms to /api/trace.
// Used to debug audio/transcript sync drift. Server writes them to the same JSONL
// file as its own events so timeline analysis is unified.
const _traceBuf = [];
function trace(kind, data = {}) {
  _traceBuf.push({ kind, ts_client: performance.now() / 1000, ...data });
}
setInterval(() => {
  if (_traceBuf.length === 0) return;
  const batch = _traceBuf.splice(0, _traceBuf.length);
  try {
    fetch("/api/trace", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ events: batch }),
      keepalive: true,
    }).catch(() => {});
  } catch (_) {}
}, 200);

let ws = null;
let audioCtx = null;
let micStream = null;
let micNode = null;
let workletReady = false;
let playbackTime = 0;
let oeuvres = [];
let currentOeuvre = null;
let currentAnnotations = null;
let isRunning = false;
// Audio output chain (built once at session start):
//   BufferSource → pitchNode → vibratoGain → masterGain → destination
// pitchNode = SoundTouch streaming worklet (pitch+tempo, decoupled)
// vibratoGain.gain is modulated by vibratoLFO×vibratoDepthGain for AM tremolo
// masterGain applies the perso's gain_db.
let pitchNode = null;
let vibratoGain = null;
let vibratoLFO = null;
let vibratoDepthGain = null;
let masterGain = null;

async function init() {
  const cfg = await (await fetch("/api/config")).json();
  $("model-badge").textContent = cfg.model + " · " + cfg.voice + (cfg.has_key ? "" : " · NO KEY");
  if (!cfg.has_key) {
    setStatus("error");
    alert("OPENAI_API_KEY missing in conteur/.env — fill it then restart.");
  }
  $("model").value = cfg.model;
  $("voice").value = cfg.voice;

  await loadLibrary();
  bindUI();
}

async function loadLibrary() {
  const r = await (await fetch("/api/library")).json();
  oeuvres = r.oeuvres;
  const genres = new Set(oeuvres.map(o => o.genre));
  const genreSel = $("genre-filter");
  for (const g of genres) {
    const opt = document.createElement("option");
    opt.value = g; opt.textContent = g;
    genreSel.appendChild(opt);
  }
  renderOeuvres();
}

function renderOeuvres() {
  const q = $("search").value.toLowerCase();
  const g = $("genre-filter").value;
  const ul = $("oeuvres-list");
  ul.replaceChildren();
  for (const o of oeuvres) {
    if (g && o.genre !== g) continue;
    if (q && !o.id.toLowerCase().includes(q)) continue;
    const li = document.createElement("li");
    li.textContent = o.id;
    li.dataset.id = o.id;
    if (currentOeuvre && currentOeuvre.id === o.id) li.classList.add("active");
    li.onclick = () => loadOeuvre(o.id);
    ul.appendChild(li);
  }
}

async function loadOeuvre(id) {
  setStatus("connecting");
  try {
    const r = await (await fetch("/api/oeuvre/" + encodeURIComponent(id))).json();
    currentOeuvre = r.oeuvre;
    currentAnnotations = r.annotations;
    $("oeuvre-title").textContent = currentOeuvre.titre || id;
    $("oeuvre-meta").textContent =
      [currentOeuvre.auteur, currentOeuvre.date, currentOeuvre.genre,
       `${currentOeuvre.n_actes||"?"} actes`, `${currentOeuvre.n_scenes||"?"} scènes`,
       `${currentOeuvre.n_repliques||"?"} répliques`]
      .filter(Boolean).join(" · ");

    renderFullText();
    renderAnnotations();
    $("start").disabled = false;
    renderOeuvres();
    setStatus("idle");
  } catch (e) {
    console.error(e);
    setStatus("error");
    alert("DraCor fetch failed for " + id + " — " + e.message);
  }
}

let currentPersoFilter = null;

function renderFullText() {
  const txtDiv = $("oeuvre-text");
  txtDiv.replaceChildren();
  if (currentPersoFilter) {
    $("text-pane-summary").textContent =
      `Répliques de ${currentPersoFilter} par acte/scène — clique sur "Tout le texte" pour revenir`;
    renderPersoView(currentPersoFilter);
  } else {
    $("text-pane-summary").textContent =
      "Texte complet de la pièce — clique sur un personnage dans la liste à droite pour ses répliques";
    txtDiv.appendChild(document.createTextNode(currentOeuvre.text_complet || ""));
  }
}

function renderPersoView(perso) {
  const txtDiv = $("oeuvre-text");
  // server returns by_character_structured: { perso: [{act, scene, scene_didascalie, lines: [...]}, ...] }
  const struct = (currentOeuvre.by_character_structured || {})[perso] || [];
  if (struct.length === 0) {
    txtDiv.appendChild(document.createTextNode(`(aucune réplique pour ${perso})`));
    return;
  }
  const backBtn = document.createElement("button");
  backBtn.textContent = "← Tout le texte";
  backBtn.className = "back-btn";
  backBtn.onclick = () => { currentPersoFilter = null; renderFullText(); };
  txtDiv.appendChild(backBtn);

  let lastKey = "";
  for (const entry of struct) {
    const key = `${entry.act}.${entry.scene}`;
    if (key !== lastKey) {
      const h = document.createElement("div");
      h.className = "act-scene-header";
      h.textContent = `ACTE ${entry.act} — SCÈNE ${entry.scene}` +
                       (entry.scene_didascalie ? `  (${entry.scene_didascalie})` : "");
      txtDiv.appendChild(h);
      lastKey = key;
    }
    const block = document.createElement("div");
    block.className = "perso-reply";
    block.textContent = entry.lines.join("\n");
    txtDiv.appendChild(block);
  }
}

function renderAnnotations() {
  const persoDiv = $("personnages-edit");
  persoDiv.replaceChildren();
  for (let i = 0; i < currentAnnotations.personnages.length; i++) {
    persoDiv.appendChild(renderPersoCard(i));
  }
  const pronDiv = $("prononciations-edit");
  pronDiv.replaceChildren();
  for (const [mot, pron] of Object.entries(currentAnnotations.prononciations || {})) {
    pronDiv.appendChild(renderPronRow(mot, pron));
  }
  $("instructions-globales").value = currentAnnotations.instructions_globales || "";
}

function mkInput(type, value, attrs = {}) {
  const el = document.createElement("input");
  el.type = type;
  el.value = value ?? "";
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

function mkLabel(text) {
  const el = document.createElement("label");
  el.textContent = text;
  return el;
}

function renderPersoCard(idx) {
  const p = currentAnnotations.personnages[idx];
  const card = document.createElement("div");
  card.className = "perso-card";

  const row = document.createElement("div");
  row.className = "row";
  const nomInput = mkInput("text", p.nom || "", { placeholder: "NOM" });
  nomInput.className = "nom";
  nomInput.oninput = (e) => { currentAnnotations.personnages[idx].nom = e.target.value; };
  const viewBtn = document.createElement("button");
  viewBtn.textContent = "👁";
  viewBtn.title = "voir ses répliques par acte/scène";
  viewBtn.onclick = () => {
    const persoName = currentAnnotations.personnages[idx].nom;
    const struct = currentOeuvre.by_character_structured || {};
    // Accent-insensitive lookup: strip diacritics + ligatures + uppercase
    const norm = (s) => (s || "")
        .normalize("NFD").replace(/[̀-ͯ]/g, "")
        .replace(/Œ/g, "OE").replace(/œ/g, "oe")
        .replace(/Æ/g, "AE").replace(/æ/g, "ae")
        .toUpperCase();
    const target = norm(persoName);
    const match = Object.keys(struct).find(k => norm(k) === target) || persoName;
    currentPersoFilter = match;
    renderFullText();
  };
  const rmBtn = document.createElement("button");
  rmBtn.textContent = "×";
  rmBtn.onclick = () => { currentAnnotations.personnages.splice(idx, 1); renderAnnotations(); };
  row.append(nomInput, viewBtn, rmBtn);
  card.appendChild(row);

  const fields = [
    ["description", "Description", "text", {}],
    ["registre", "Registre vocal", "text", {}],
    ["prompt_instruction", "Instruction de jeu (prompt inline)", "textarea", {}],
    ["speed_hint", "Speed (0.7-1.3)", "number", {step: "0.01", min: "0.7", max: "1.3", default: 1.0}],
    ["pitch_shift", "DSP Pitch (semi-tons, -3 à +3)", "number", {step: "0.5", min: "-3", max: "3", default: 0}],
    ["vibrato_hz", "DSP Vibrato Hz (0-5)", "number", {step: "0.5", min: "0", max: "5", default: 0}],
    ["vibrato_depth", "DSP Vibrato depth (0-0.15)", "number", {step: "0.01", min: "0", max: "0.15", default: 0}],
    ["gain_db", "DSP Gain (dB, -6 à +3)", "number", {step: "0.5", min: "-6", max: "3", default: 0}],
    ["antenna_left", "Antenne gauche (°, -156 à +156 = ±2.73 rad)", "number", {step: "1", min: "-156", max: "156", default: 0}],
    ["antenna_right", "Antenne droite (°, -156 à +156 = ±2.73 rad)", "number", {step: "1", min: "-156", max: "156", default: 0}],
  ];
  for (const [field, label, kind, opts] of fields) {
    card.appendChild(mkLabel(label));
    let el;
    if (kind === "textarea") {
      el = document.createElement("textarea");
      el.rows = 2;
      el.value = p[field] || "";
    } else if (kind === "number") {
      const val = (p[field] !== undefined && p[field] !== null) ? p[field] : (opts.default ?? 0);
      el = mkInput("number", val, { step: opts.step, min: opts.min, max: opts.max });
    } else {
      el = mkInput("text", p[field] || "");
    }
    el.oninput = (e) => {
      currentAnnotations.personnages[idx][field] =
        kind === "number" ? parseFloat(e.target.value) : e.target.value;
    };
    card.appendChild(el);
  }
  return card;
}

function renderPronRow(mot, pron) {
  const row = document.createElement("div");
  row.className = "pron-row";
  const motEl = mkInput("text", mot, { placeholder: "mot" });
  motEl.className = "mot";
  const pronEl = mkInput("text", pron, { placeholder: "prononciation" });
  pronEl.className = "pron";
  const rm = document.createElement("button");
  rm.textContent = "×";

  const sync = () => {
    const newProns = {};
    document.querySelectorAll("#prononciations-edit .pron-row").forEach(r => {
      const m = r.querySelector(".mot").value.trim();
      const p = r.querySelector(".pron").value.trim();
      if (m) newProns[m] = p;
    });
    currentAnnotations.prononciations = newProns;
  };
  motEl.oninput = sync;
  pronEl.oninput = sync;
  rm.onclick = () => { row.remove(); sync(); };
  row.append(motEl, pronEl, rm);
  return row;
}

function bindUI() {
  $("search").oninput = renderOeuvres;
  $("genre-filter").onchange = renderOeuvres;

  $("speed").oninput = (e) => {
    $("speed-val").textContent = parseFloat(e.target.value).toFixed(2);
    if (isRunning) wsSend({ type: "speed", speed: parseFloat(e.target.value) });
  };

  $("add-perso").onclick = () => {
    if (!currentAnnotations) return;
    currentAnnotations.personnages.push({
      nom: "NOUVEAU", description: "", registre: "", prompt_instruction: "", speed_hint: 1.0,
    });
    renderAnnotations();
  };
  $("add-pron").onclick = () => {
    if (!currentAnnotations) return;
    currentAnnotations.prononciations = currentAnnotations.prononciations || {};
    $("prononciations-edit").appendChild(renderPronRow("", ""));
  };
  $("instructions-globales").oninput = (e) => {
    if (currentAnnotations) currentAnnotations.instructions_globales = e.target.value;
  };

  $("dsp-enabled").onchange = (e) => {
    if (isRunning) wsSend({ type: "dsp_toggle", on: e.target.checked });
  };
  $("robot-enabled").onchange = (e) => {
    if (isRunning) wsSend({ type: "robot_toggle", on: e.target.checked });
  };

  $("save-annot").onclick = async () => {
    if (!currentOeuvre) return;
    const r = await fetch("/api/oeuvre/" + encodeURIComponent(currentOeuvre.id) + "/annotations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(currentAnnotations),
    });
    if ((await r.json()).ok) {
      $("save-annot").textContent = "✓ sauvegardé";
      // Hot reload: if a session is running, push new prompt to OpenAI
      if (isRunning) {
        wsSend({ type: "reload_prompt" });
        appendTranscript("you", "(annotations rechargées dans la session en cours)");
      }
      setTimeout(() => $("save-annot").textContent = "Sauvegarder annotations", 1500);
    }
  };

  $("start").onclick = startSession;
  $("cancel").onclick = () => {
    // Kill local audio FIRST so the user hears silence immediately,
    // independent of WS round-trip latency.
    killLocalAudio();
    wsSend({ type: "cancel" });
  };
  $("stop").onclick = stopSession;
  $("push-scene").onclick = () => {
    const txt = $("scene-text").value.trim();
    if (!txt || !isRunning) return;
    wsSend({ type: "push_scene", scene_text: txt });
  };
  $("push-all").onclick = () => {
    if (!isRunning || !currentOeuvre) return;
    const fullText = currentOeuvre.text_complet || "";
    appendTranscript("you", `(je pousse toute la pièce : ${fullText.length} chars / ~${Math.round(fullText.length/4)} tokens)`);
    wsSend({ type: "push_scene", scene_text: fullText });
  };
  $("continue-reading").onclick = () => {
    if (!isRunning) return;
    wsSend({ type: "text", text: "Continue la lecture où tu t'étais arrêté, sans répéter." });
  };

}

async function startSession() {
  if (!currentOeuvre) return;
  setStatus("connecting");
  $("start").disabled = true;

  audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 24000 });
  playbackTime = audioCtx.currentTime;
  if (!workletReady) {
    await audioCtx.audioWorklet.addModule("/static/mic-worklet.js");
    await audioCtx.audioWorklet.addModule("/static/soundtouch-worklet.js");
    workletReady = true;
  }

  // Build the persistent output chain. Each incoming audio chunk creates an
  // ephemeral BufferSource that connects into pitchNode; pitchNode et al. live
  // for the whole session so their state (pitch, vibrato, gain) survives
  // across chunks and perso switches.
  pitchNode = new AudioWorkletNode(audioCtx, "streaming-pitch-shifter", {
    numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1],
  });
  vibratoGain = audioCtx.createGain();
  vibratoGain.gain.value = 1.0;
  vibratoLFO = audioCtx.createOscillator();
  vibratoLFO.frequency.value = 0;
  vibratoDepthGain = audioCtx.createGain();
  vibratoDepthGain.gain.value = 0;
  vibratoLFO.connect(vibratoDepthGain);
  vibratoDepthGain.connect(vibratoGain.gain);
  vibratoLFO.start();
  masterGain = audioCtx.createGain();
  masterGain.gain.value = 1.0;
  pitchNode.connect(vibratoGain);
  vibratoGain.connect(masterGain);
  masterGain.connect(audioCtx.destination);
  // Start neutral (narrator profile).
  applyPersoProfile(null);

  micStream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, sampleRate: 24000, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });

  const src = audioCtx.createMediaStreamSource(micStream);
  micNode = new AudioWorkletNode(audioCtx, "mic-pcm16-processor");
  micNode.port.onmessage = (e) => {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(e.data);
  };
  src.connect(micNode);

  ws = new WebSocket("ws://" + location.host + "/ws/session");
  ws.binaryType = "arraybuffer";
  ws.onopen = () => {
    wsSend({
      type: "start",
      oeuvre_id: currentOeuvre.id,
      settings: {
        model: $("model").value,
        voice: $("voice").value,
        speed: parseFloat($("speed").value),
        reasoning_effort: $("reasoning").value,
        enable_preambles: $("preambles").checked,
        dsp_enabled: $("dsp-enabled").checked,
        robot_enabled: $("robot-enabled").checked,
      },
    });
  };
  ws.onmessage = onWsMessage;
  ws.onerror = (e) => { console.error(e); setStatus("error"); };
  ws.onclose = () => {
    isRunning = false; setStatus("idle");
    $("start").disabled = false; $("cancel").disabled = true; $("stop").disabled = true;
  };
}

function wsSend(obj) { if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj)); }

function onWsMessage(ev) {
  let msg;
  try { msg = JSON.parse(ev.data); } catch (e) { return; }
  switch (msg.type) {
    case "session.ready":
      isRunning = true;
      setStatus("ready");
      $("cancel").disabled = false;
      $("stop").disabled = false;
      $("push-scene").disabled = false;
      $("push-all").disabled = false;
      $("continue-reading").disabled = false;
      appendTranscript("cedar", "(prêt — parle.)");
      break;
    case "audio.delta":
      playAudioDelta(msg.data, msg.byte_offset || 0);
      setStatus("speaking");
      break;
    case "schedule_switch":
      scheduleSwitch(msg.perso, msg.at_byte);
      break;
    case "transcript.delta":
      appendTranscriptStream("cedar", msg.data);
      break;
    case "user.transcript":
      appendTranscript("you", msg.data);
      break;
    case "speech.started":
      setStatus("speaking");
      break;
    case "response.done":
      setStatus("ready");
      cedarLineDone = true;
      chunkSchedule.length = 0;
      pendingSwitches.length = 0;
      break;
    case "audio.cancel":
      // Server tells us the user requested an interrupt. The local Interrompre
      // button already killed audio; this is the safety net for cancels that
      // come from elsewhere (programmatic cancel, future tool calls, etc.).
      killLocalAudio();
      appendTranscript("cedar", "(interrompu)");
      break;
    case "error":
      console.error(msg.error);
      setStatus("error");
      appendTranscript("cedar", "⚠ " + msg.error);
      break;
    case "session.stopped":
      isRunning = false;
      setStatus("idle");
      break;
    case "prompt.reloaded":
      appendTranscript("cedar", "(prompt mis à jour, prochaine réponse l'utilisera)");
      break;
    case "tool.call":
      appendTranscript("cedar", `(tool: ${msg.name}(${JSON.stringify(msg.args)}))`);
      break;
    case "perso.active":
      applyPersoProfile(msg.perso);
      appendTranscript("cedar", `🎭 ${msg.perso} (DSP appliqué)`);
      break;
    case "dsp.state":
      appendTranscript("cedar", `(DSP ${msg.on ? "activé" : "désactivé"})`);
      break;
    case "robot.ready":
      appendTranscript("cedar", `🤖 Robot prêt (mode ${msg.mode})`);
      break;
    case "robot.state":
      appendTranscript("cedar", `(Antennes ${msg.on ? "activées" : "désactivées"})`);
      break;
    case "robot.pose":
      appendTranscript("cedar", `🤖 ${msg.perso}: antennes L=${msg.left_deg}° R=${msg.right_deg}° (${msg.mode})`);
      break;
  }
}

// Audio chunk scheduling: maps each chunk's byte_offset (from server) to its
// playback start_time + duration, so we can compute "when will audio reach byte X?"
const chunkSchedule = [];  // [{startByte, endByte, startTime, duration}]
const pendingSwitches = []; // [{perso, at_byte}] waiting for audio to reach the byte
// Track every AudioBufferSource we hand to the speaker so we can kill them all
// instantly on Interrompre / cancel. Sources auto-remove themselves on 'ended'.
const activeSources = new Set();

// Look up a perso's voice profile in currentAnnotations. Returns the neutral
// narrator profile (pitch=0, no vibrato, unit gain) for null/unknown persos.
function profileForPerso(persoName) {
  const neutral = { pitch_shift: 0, speed_hint: 1.0, vibrato_hz: 0, vibrato_depth: 0, gain_db: 0 };
  if (!persoName || !currentAnnotations) return neutral;
  const norm = (s) => (s || "")
      .normalize("NFD").replace(/[̀-ͯ]/g, "")
      .replace(/Œ/g, "OE").replace(/œ/g, "oe")
      .replace(/Æ/g, "AE").replace(/æ/g, "ae")
      .toUpperCase();
  const target = norm(persoName);
  for (const p of (currentAnnotations.personnages || [])) {
    if (norm(p.nom) === target) {
      return {
        pitch_shift: Number(p.pitch_shift) || 0,
        speed_hint:  Number(p.speed_hint)  || 1.0,
        vibrato_hz:  Number(p.vibrato_hz)  || 0,
        vibrato_depth: Number(p.vibrato_depth) || 0,
        gain_db:     Number(p.gain_db)     || 0,
      };
    }
  }
  return neutral;
}

// Apply a perso's profile to the persistent audio chain. Ramps params over
// 30 ms to avoid clicks. Called on perso.active events from the server.
function applyPersoProfile(persoName) {
  if (!pitchNode || !audioCtx) return;
  const p = profileForPerso(persoName);
  const t = audioCtx.currentTime;
  const ramp = 0.03;
  pitchNode.port.postMessage({ name: "pitchSemitones", value: p.pitch_shift });
  pitchNode.port.postMessage({ name: "tempo",          value: p.speed_hint || 1.0 });
  // Vibrato: LFO frequency + depth (depth is fraction of unit gain to modulate)
  if (vibratoLFO && vibratoDepthGain) {
    vibratoLFO.frequency.setTargetAtTime(p.vibrato_hz || 0, t, ramp);
    vibratoDepthGain.gain.setTargetAtTime(p.vibrato_depth || 0, t, ramp);
  }
  // Gain: dB → linear, ramp.
  if (masterGain) {
    const lin = Math.pow(10, (p.gain_db || 0) / 20);
    masterGain.gain.setTargetAtTime(lin, t, ramp);
  }
}

function killLocalAudio() {
  for (const src of activeSources) {
    try { src.stop(0); } catch (_) {}
  }
  activeSources.clear();
  if (audioCtx) playbackTime = audioCtx.currentTime;
  chunkSchedule.length = 0;
  pendingSwitches.length = 0;
  cedarLineDone = true;
  setStatus("ready");
}

function playAudioDelta(b64, byteOffset = 0) {
  const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
  const samples = new Int16Array(bytes.buffer, bytes.byteOffset, bytes.byteLength / 2);
  const float32 = new Float32Array(samples.length);
  for (let i = 0; i < samples.length; i++) float32[i] = samples[i] / 32768;
  const buf = audioCtx.createBuffer(1, float32.length, 24000);
  buf.copyToChannel(float32, 0);
  const src = audioCtx.createBufferSource();
  src.buffer = buf;
  // Route through the persistent pitch/vibrato/gain chain instead of speaker
  // directly. pitchNode is shared across chunks so SoundTouch state survives
  // perso switches — only its params change.
  src.connect(pitchNode);
  src.onended = () => activeSources.delete(src);
  activeSources.add(src);
  const now = audioCtx.currentTime;
  if (playbackTime < now) playbackTime = now;
  const startTime = playbackTime;
  src.start(startTime);
  playbackTime += buf.duration;
  // Record schedule (note byteOffset is from server, in raw PCM bytes BEFORE DSP)
  chunkSchedule.push({
    startByte: byteOffset,
    endByte: byteOffset + bytes.byteLength,
    startTime,
    duration: buf.duration,
  });
  trace("audio_delta_browser", {
    byteOffset,
    endByte: byteOffset + bytes.byteLength,
    startTime,
    duration: buf.duration,
    audioCtxCurrentTime: now,
  });
  // Trim old entries (keep last 200 chunks ~= 8s)
  if (chunkSchedule.length > 200) chunkSchedule.splice(0, chunkSchedule.length - 200);
  // Check if any pending switch is now schedulable
  checkPendingSwitches();
}

function checkPendingSwitches() {
  const remaining = [];
  for (const sw of pendingSwitches) {
    const chunk = chunkSchedule.find(c => sw.at_byte >= c.startByte && sw.at_byte < c.endByte);
    if (!chunk) {
      // Measure how far at_byte sits from the currently-known chunk window.
      // Positive = at_byte is past the last chunk we've seen (waiting for it
      // to arrive — normal during DSP buffering). Negative = at_byte is before
      // the first chunk we have (the chunk got trimmed, schedule too old).
      // Zero = falls in a gap between two adjacent chunks (shouldn't happen).
      let gap = null;
      let direction = null;
      if (chunkSchedule.length > 0) {
        const first = chunkSchedule[0];
        const last = chunkSchedule[chunkSchedule.length - 1];
        if (sw.at_byte < first.startByte) {
          gap = first.startByte - sw.at_byte;
          direction = "before_window";
        } else if (sw.at_byte >= last.endByte) {
          gap = sw.at_byte - last.endByte;
          direction = "after_window";
        } else {
          gap = 0;
          direction = "inside_gap";
        }
      }
      trace("schedule_lookup", {
        perso: sw.perso,
        at_byte: sw.at_byte,
        chunk_found: false,
        gap_bytes: gap,
        gap_direction: direction,
        chunkSchedule_size: chunkSchedule.length,
      });
      remaining.push(sw);
      continue;
    }
    const playTime = chunk.startTime + (sw.at_byte - chunk.startByte) / 48000;
    const delayMs = Math.max(0, (playTime - audioCtx.currentTime) * 1000);
    trace("schedule_lookup", {
      perso: sw.perso,
      at_byte: sw.at_byte,
      chunk_found: true,
      playTime,
      currentTime: audioCtx.currentTime,
      delayMs,
    });
    setTimeout(() => {
      trace("apply_switch_emit", {
        perso: sw.perso,
        currentTime: audioCtx.currentTime,
        scheduled_playTime: playTime,
      });
      wsSend({ type: "apply_switch", perso: sw.perso });
    }, delayMs);
  }
  pendingSwitches.length = 0;
  pendingSwitches.push(...remaining);
}

function scheduleSwitch(perso, atByte) {
  trace("schedule_switch_recv", { perso, at_byte: atByte });
  pendingSwitches.push({ perso, at_byte: atByte });
  checkPendingSwitches();
}

let cedarLineBuffer = "";
let cedarLineDone = true;
function appendTranscriptStream(kind, chunk) {
  const t = $("transcript");
  let line = t.querySelector(".line." + kind + ":last-child");
  if (!line || cedarLineDone) {
    line = document.createElement("div");
    line.className = "line " + kind;
    t.appendChild(line);
    cedarLineBuffer = "";
    cedarLineDone = false;
  }
  cedarLineBuffer += chunk;
  line.textContent = cedarLineBuffer;
  t.scrollTop = t.scrollHeight;
}
function appendTranscript(kind, text) {
  const t = $("transcript");
  const line = document.createElement("div");
  line.className = "line " + kind;
  line.textContent = text;
  t.appendChild(line);
  t.scrollTop = t.scrollHeight;
}

async function stopSession() {
  wsSend({ type: "stop" });
  if (micStream) { micStream.getTracks().forEach(t => t.stop()); micStream = null; }
  if (ws) { ws.close(); ws = null; }
  isRunning = false;
  setStatus("idle");
}

init().catch(e => { console.error(e); setStatus("error"); });
