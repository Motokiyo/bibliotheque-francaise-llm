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
let books = [];
let currentOeuvre = null;
let currentBook = null;
let currentChapterIndex = 0;
let bookAutoReading = false;
let autoStartPending = false;
let bookPlayerState = "stopped";
let bookReadingChapterIndex = null;
let bookReadingStartOffset = 0;
let bookSegmentEndOffset = 0;
let bookTranscriptChars = 0;
let ignoreNextResponseDone = false;
let ignoreResponseDoneUntil = 0;
let currentAnnotations = null;
let isRunning = false;
let appConfig = {};
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
let micWorkletAvailable = false;
let soundtouchAvailable = false;
let ttsSource = null;
let ttsPlayToken = 0;
let ttsNextPromise = null;
let ttsNextMeta = null;
let browserTtsMode = false;
let currentUtterance = null;
let browserSpeechWatchdog = null;
let browserSpeechProgressTimer = null;
let browserSpeechProgress = null;
let lastBookSelection = null;
let pendingBookSegment = null;
let currentBookSegment = null;
let bookRealtimeToken = 0;
let bookAudioWatchKey = null;
let bookPendingResponseDone = false;
const readingShelfKey = "cedar-conteur.reading-shelf";

function seenBookKey(bookId) {
  return `cedar-conteur.book-seen.${bookId}`;
}

async function init() {
  const cfg = await (await fetch("/api/config")).json();
  appConfig = cfg;
  $("model-badge").textContent = cfg.model + " · " + cfg.voice + (cfg.has_key ? "" : " · NO KEY");
  if (!cfg.has_key) {
    setStatus("error");
    alert("OPENAI_API_KEY missing in conteur/.env — fill it then restart.");
  }
  $("model").value = cfg.model;
  if ($("model").querySelector('option[value="gpt-realtime"]')) $("model").value = "gpt-realtime";
  $("voice").value = cfg.voice;
  if (!cfg.allow_robot && $("robot-enabled")) {
    $("robot-enabled").checked = false;
    $("robot-enabled").disabled = true;
  }

  await loadLibrary();
  bindUI();
}

async function loadLibrary() {
  const r = await (await fetch("/api/library")).json();
  oeuvres = r.oeuvres;
  books = r.books || [];
  const genres = new Set(oeuvres.map(o => o.genre));
  const genreSel = $("genre-filter");
  for (const g of genres) {
    const opt = document.createElement("option");
    opt.value = g; opt.textContent = g;
    genreSel.appendChild(opt);
  }
  renderBooks();
  renderReadingShelf();
  renderOeuvres();
}

function renderBooks() {
  const ul = $("books-list");
  const q = ($("search")?.value || "").toLowerCase();
  ul.replaceChildren();
  for (const b of books) {
    const haystack = `${b.id} ${b.title || ""} ${b.author || ""}`.toLowerCase();
    if (q && !haystack.includes(q)) continue;
    const li = document.createElement("li");
    li.textContent = `${b.title || b.id}${b.author ? " — " + b.author : ""}`;
    li.dataset.id = b.id;
    if (currentBook && currentBook.id === b.id) li.classList.add("active");
    if (bookHasNewChapter(b)) li.classList.add("has-new");
    li.onclick = () => loadBook(b.id);
    ul.appendChild(li);
  }
}

function loadSeenBook(bookId) {
  try {
    return JSON.parse(localStorage.getItem(seenBookKey(bookId)) || "null") || {};
  } catch (_) {
    return {};
  }
}

function saveSeenBook(book) {
  if (!book?.id) return;
  localStorage.setItem(seenBookKey(book.id), JSON.stringify({
    version: book.version || "",
    chapterCount: book.chapters?.length || book.chapter_count || 0,
    updatedAt: new Date().toISOString(),
  }));
}

function bookHasNewChapter(book) {
  if (!book?.id || !book.version) return false;
  const seen = loadSeenBook(book.id);
  if (!seen.version || seen.version === book.version) return false;
  const currentCount = book.chapter_count || book.chapters?.length || 0;
  return currentCount > (seen.chapterCount || 0);
}

function showBookUpdateBanner(book, previous = {}) {
  const banner = $("book-update-banner");
  if (!banner || !book) return;
  const count = book.chapters?.length || 0;
  const delta = Math.max(1, count - (previous.chapterCount || count - 1));
  $("book-update-message").textContent =
    `${delta > 1 ? "Nouveaux chapitres accessibles" : "Nouveau chapitre accessible"} dans ${book.title}.`;
  banner.hidden = false;
}

function hideBookUpdateBanner({ markSeen = false } = {}) {
  const banner = $("book-update-banner");
  if (banner) banner.hidden = true;
  if (markSeen && currentBook) {
    saveSeenBook(currentBook);
    renderBooks();
    renderReadingShelf();
  }
}

function loadReadingShelf() {
  try {
    const ids = JSON.parse(localStorage.getItem(readingShelfKey) || "[]");
    return Array.isArray(ids) ? ids.filter(Boolean) : [];
  } catch (_) {
    return [];
  }
}

function saveReadingShelf(ids) {
  localStorage.setItem(readingShelfKey, JSON.stringify([...new Set(ids.filter(Boolean))]));
}

function rememberReadingBook(bookId) {
  const ids = loadReadingShelf().filter(id => id !== bookId);
  ids.unshift(bookId);
  saveReadingShelf(ids.slice(0, 12));
  renderReadingShelf();
}

function readingProgressFor(book) {
  if (!book) return { chapterIndex: 0, charOffset: 0, percent: 0, label: "0%" };
  const saved = loadBookProgress(book.id) || {};
  const chapterIndex = Math.max(0, Math.min(saved.chapterIndex || 0, (book.chapters?.length || 1) - 1));
  const chapter = book.chapters?.[chapterIndex] || { text: "", roman: saved.chapterRoman || "" };
  const charOffset = Math.max(0, Math.min(saved.charOffset || 0, chapter.text.length));
  const percent = chapter.text.length
    ? Math.round((charOffset / chapter.text.length) * 100)
    : Math.max(0, Math.min(saved.percent || 0, 100));
  return {
    chapterIndex,
    charOffset,
    percent,
    label: saved.label || `${chapter.roman || chapterIndex + 1} · ${percent}%`,
  };
}

function renderReadingShelf() {
  const shelf = $("reading-shelf");
  if (!shelf) return;
  shelf.replaceChildren();
  const ids = loadReadingShelf();
  const visibleBooks = ids
    .map(id => {
      const meta = books.find(b => b.id === id);
      return currentBook && currentBook.id === id ? {...(meta || {}), ...currentBook} : meta;
    })
    .filter(Boolean);
  if (visibleBooks.length === 0) {
    const empty = document.createElement("p");
    empty.className = "shelf-empty";
    empty.textContent = "Aucune lecture commencée";
    shelf.appendChild(empty);
    return;
  }
  for (const book of visibleBooks) {
    const progress = readingProgressFor(book);
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "book-spine";
    if (currentBook && currentBook.id === book.id) btn.classList.add("active");
    if (bookHasNewChapter(book)) btn.classList.add("has-new");
    btn.dataset.progress = progress.percent;
    btn.style.setProperty("--spine-progress", String(progress.percent));
    btn.title = `${book.title || book.id} — ${progress.label}`;
    const title = document.createElement("span");
    title.className = "spine-title";
    title.textContent = book.title || book.id;
    const progressEl = document.createElement("span");
    progressEl.className = "spine-progress";
    progressEl.textContent = progress.label;
    btn.append(title, progressEl);
    btn.onclick = () => loadBook(book.id);
    shelf.appendChild(btn);
  }
}

function progressKey(bookId) {
  return `cedar-conteur.progress.${bookId}`;
}

function loadBookProgress(bookId) {
  try {
    return JSON.parse(localStorage.getItem(progressKey(bookId)) || "null");
  } catch (_) {
    return null;
  }
}

function saveBookProgress(chapterIndex = currentChapterIndex, charOffset = 0) {
  if (!currentBook) return;
  const chapter = currentBook.chapters[chapterIndex];
  if (!chapter) return;
  const safeOffset = Math.max(0, Math.min(charOffset || 0, chapter.text.length));
  const percent = chapter.text.length ? Math.round((safeOffset / chapter.text.length) * 100) : 0;
  const label = `${chapter.roman || chapterIndex + 1} · ${percent}%`;
  localStorage.setItem(progressKey(currentBook.id), JSON.stringify({
    chapterIndex,
    charOffset: safeOffset,
    chapterRoman: chapter.roman || String(chapterIndex + 1),
    chapterTitle: chapter.title || "",
    percent,
    label,
    updatedAt: new Date().toISOString(),
  }));
  rememberReadingBook(currentBook.id);
  updateBookProgressLabel(chapterIndex, safeOffset);
}

function currentBookProgress() {
  if (!currentBook) return { chapterIndex: 0, charOffset: 0 };
  const saved = loadBookProgress(currentBook.id) || {};
  const chapterIndex = Math.max(0, Math.min(saved.chapterIndex || 0, currentBook.chapters.length - 1));
  const chapter = currentBook.chapters[chapterIndex] || { text: "" };
  const charOffset = Math.max(0, Math.min(saved.charOffset || 0, chapter.text.length));
  return { chapterIndex, charOffset };
}

function updateBookProgressLabel(chapterIndex = currentChapterIndex, charOffset = null) {
  if (!currentBook || !$("book-progress")) return;
  const chapter = currentBook.chapters[chapterIndex];
  if (!chapter) {
    $("book-progress").textContent = "";
    return;
  }
  const offset = charOffset === null ? (currentBookProgress().charOffset || 0) : charOffset;
  const percent = chapter.text.length ? Math.round((offset / chapter.text.length) * 100) : 0;
  $("book-progress").textContent = `${chapter.roman} · ${percent}%`;
}

function snapToParagraph(text, offset) {
  if (!offset || offset < 200) return 0;
  const windowStart = Math.max(0, offset - 600);
  const before = text.slice(windowStart, offset);
  const para = before.lastIndexOf("\n\n");
  if (para !== -1) return windowStart + para + 2;
  const sentence = Math.max(before.lastIndexOf(". "), before.lastIndexOf(" !"), before.lastIndexOf(" ?"));
  return sentence !== -1 ? windowStart + sentence + 2 : Math.max(0, offset - 300);
}

function nextSegmentEnd(text, startOffset, targetSize = 900) {
  if (startOffset + targetSize >= text.length) return text.length;
  const minEnd = Math.min(text.length, startOffset + Math.floor(targetSize * 0.65));
  const maxEnd = Math.min(text.length, startOffset + targetSize);
  const window = text.slice(minEnd, maxEnd);
  const para = window.lastIndexOf("\n\n");
  if (para !== -1) return minEnd + para + 2;
  const sentence = Math.max(window.lastIndexOf(". "), window.lastIndexOf(" !"), window.lastIndexOf(" ?"));
  if (sentence !== -1) return minEnd + sentence + 2;
  return maxEnd;
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
    currentBook = null;
    bookAutoReading = false;
    currentAnnotations = r.annotations;
    $("oeuvre-title").textContent = currentOeuvre.titre || id;
    $("oeuvre-meta").textContent =
      [currentOeuvre.auteur, currentOeuvre.date, currentOeuvre.genre,
       `${currentOeuvre.n_actes||"?"} actes`, `${currentOeuvre.n_scenes||"?"} scènes`,
       `${currentOeuvre.n_repliques||"?"} répliques`]
      .filter(Boolean).join(" · ");

    renderFullText();
    renderBookControls();
    renderAnnotations();
    $("start").disabled = false;
    renderOeuvres();
    renderBooks();
    closeResponsiveMenu();
    setStatus("idle");
  } catch (e) {
    console.error(e);
    setStatus("error");
    alert("DraCor fetch failed for " + id + " — " + e.message);
  }
}

async function loadBook(id) {
  setStatus("connecting");
  try {
    if (ws || isRunning) stopSession();
    stopTtsSource();
    const r = await (await fetch("/api/book/" + encodeURIComponent(id))).json();
    const previousSeen = loadSeenBook(id);
    currentBook = r.book;
    currentOeuvre = r.oeuvre;
    currentAnnotations = r.annotations;
    rememberReadingBook(currentBook.id);
    currentChapterIndex = currentBookProgress().chapterIndex;
    bookPlayerState = "stopped";
    bookAutoReading = false;
    $("oeuvre-title").textContent = currentBook.title || id;
    $("oeuvre-meta").textContent =
      [currentBook.author, currentBook.translator ? `trad. ${currentBook.translator}` : "", `${currentBook.chapters.length} chapitres`]
      .filter(Boolean).join(" · ");
    renderFullText();
    renderBookControls();
    renderAnnotations();
    $("start").disabled = false;
    renderOeuvres();
    renderBooks();
    renderReadingShelf();
    if (previousSeen.version && previousSeen.version !== currentBook.version && currentBook.chapters.length > (previousSeen.chapterCount || 0)) {
      showBookUpdateBanner(currentBook, previousSeen);
    } else {
      hideBookUpdateBanner();
      saveSeenBook(currentBook);
    }
    closeResponsiveMenu();
    setStatus("idle");
  } catch (e) {
    console.error(e);
    setStatus("error");
    alert("Livre introuvable — " + e.message);
  }
}

function closeResponsiveMenu() {
  if (window.matchMedia("(max-width: 900px)").matches) {
    document.body.classList.remove("menu-open");
  }
}

function renderBookControls() {
  const box = $("book-controls");
  if (!currentBook) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  const sel = $("chapter-select");
  sel.replaceChildren();
  currentBook.chapters.forEach((ch, i) => {
    const opt = document.createElement("option");
    opt.value = String(i);
    opt.textContent = `${ch.roman}. ${ch.title}`;
    sel.appendChild(opt);
  });
  sel.value = String(currentChapterIndex);
  const saved = currentBookProgress();
  const hasSavedProgress = saved.chapterIndex > 0 || saved.charOffset > 0;
  $("book-prev").disabled = currentChapterIndex <= 0;
  $("book-play").disabled = false;
  $("book-play").textContent = bookPlayerState === "paused" || hasSavedProgress ? "Reprendre" : "Démarrer";
  $("book-pause").disabled = bookPlayerState !== "playing" && bookPlayerState !== "loading";
  $("book-next").disabled = currentChapterIndex >= currentBook.chapters.length - 1;
  $("book-stop").disabled = bookPlayerState === "stopped";
  $("book-from-selection").disabled = false;
  updateBookProgressLabel();
}

let currentPersoFilter = null;

function renderFullText() {
  const txtDiv = $("oeuvre-text");
  txtDiv.replaceChildren();
  if (currentBook) {
    const chapter = currentBook.chapters[currentChapterIndex] || currentBook.chapters[0];
    $("text-pane-summary").textContent = `Chapitre ${chapter.roman} — ${chapter.title}`;
    txtDiv.appendChild(document.createTextNode(chapter.text || ""));
    return;
  }
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
  $("search").oninput = () => {
    renderBooks();
    renderOeuvres();
  };
  $("genre-filter").onchange = renderOeuvres;
  $("toggle-menu").onclick = () => document.body.classList.toggle("menu-open");
  $("close-menu").onclick = closeResponsiveMenu;
  $("toggle-annotations").onclick = () => {
    const panel = document.querySelector(".annotations");
    if (panel) panel.style.display = "block";
    document.body.classList.remove("annotations-hidden");
    document.body.classList.add("annotations-visible");
  };
  $("close-annotations").onclick = () => {
    const panel = document.querySelector(".annotations");
    if (panel) panel.style.display = "none";
    document.body.classList.add("annotations-hidden");
    document.body.classList.remove("annotations-visible");
  };
  $("chapter-select").onchange = (e) => {
    currentChapterIndex = parseInt(e.target.value, 10) || 0;
    resetBookSegmentState();
    saveBookProgress(currentChapterIndex, 0);
    renderFullText();
    renderBookControls();
    if (currentBook && (bookPlayerState === "playing" || bookPlayerState === "loading")) {
      playBookFromOffset(currentChapterIndex, 0);
    }
  };
  $("toggle-player").onclick = () => {
    document.body.classList.toggle("player-collapsed");
    $("toggle-player").textContent = document.body.classList.contains("player-collapsed") ? "Lecteur" : "Masquer";
  };
  $("book-update-dismiss").onclick = () => hideBookUpdateBanner({ markSeen: true });

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

  $("start").onclick = () => {
    if (currentBook) playBookFromProgress();
    else startSession();
  };
  $("cancel").onclick = () => {
    if (currentBook) {
      pauseBookPlayback();
      return;
    }
    // Kill local audio FIRST so the user hears silence immediately,
    // independent of WS round-trip latency.
    bookPlayerState = "paused";
    bookAutoReading = false;
    updateProgressFromTranscript();
    cancelCurrentResponseImmediately();
    renderBookControls();
  };
  $("stop").onclick = () => {
    if (currentBook) stopBookPlayback();
    else stopSession();
  };
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
  $("book-prev").onclick = () => jumpBookChapter(-1);
  $("book-play").onclick = () => {
    if (!currentBook) return;
    playBookFromProgress();
  };
  $("book-pause").onclick = pauseBookPlayback;
  $("book-next").onclick = () => jumpBookChapter(1);
  $("book-stop").onclick = stopBookPlayback;
  $("book-from-selection").onclick = playBookFromSelection;
  $("book-from-selection").onpointerdown = (e) => {
    captureBookSelection();
    e.preventDefault();
  };
  document.addEventListener("selectionchange", captureBookSelection);

}

async function startSession() {
  if (!currentOeuvre) return;
  setStatus("connecting");
  $("start").disabled = true;
  autoStartPending = true;

  try {
    const wsProtocol = location.protocol === "https:" ? "wss://" : "ws://";
    ws = new WebSocket(wsProtocol + location.host + "/ws/session");
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
          robot_enabled: Boolean(appConfig.allow_robot) && $("robot-enabled").checked,
        },
      });
    };
    ws.onmessage = onWsMessage;
    ws.onerror = (e) => { console.error(e); setStatus("error"); $("start").disabled = false; };
    ws.onclose = () => {
      isRunning = false; setStatus("idle");
      $("start").disabled = false; $("cancel").disabled = true; $("stop").disabled = true;
      renderBookControls();
    };
  } catch (e) {
    console.error(e);
    setStatus("error");
    $("start").disabled = false;
    appendTranscript("cedar", "Impossible d'ouvrir la session.");
    return;
  }

  setupAudioBestEffort();
}

async function setupAudioBestEffort() {
  try {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 24000 });
    playbackTime = audioCtx.currentTime;
    masterGain = audioCtx.createGain();
    masterGain.gain.value = 1.0;
    masterGain.connect(audioCtx.destination);

    if (!workletReady && audioCtx.audioWorklet) {
      try {
        await audioCtx.audioWorklet.addModule("/static/mic-worklet.js");
        micWorkletAvailable = true;
      } catch (e) {
        micWorkletAvailable = false;
        appendTranscript("you", "(micro worklet indisponible)");
      }
      try {
        await audioCtx.audioWorklet.addModule("/static/soundtouch-worklet.js");
        soundtouchAvailable = true;
      } catch (e) {
        soundtouchAvailable = false;
        appendTranscript("you", "(DSP indisponible, lecture audio brute)");
      }
      workletReady = true;
    }

    if (soundtouchAvailable) {
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
      pitchNode.connect(vibratoGain);
      vibratoGain.connect(masterGain);
    }
    applyPersoProfile(null);

    if (micWorkletAvailable && navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
      micStream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, sampleRate: 24000, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      const src = audioCtx.createMediaStreamSource(micStream);
      micNode = new AudioWorkletNode(audioCtx, "mic-pcm16-processor");
      micNode.port.onmessage = (e) => {
        if (ws && ws.readyState === WebSocket.OPEN) ws.send(e.data);
      };
      src.connect(micNode);
    }
  } catch (e) {
    appendTranscript("you", "(audio local partiel, lecture texte seule)");
  }
}

function wsSend(obj) { if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj)); }

async function ensureAudioOutput() {
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    masterGain = audioCtx.createGain();
    masterGain.gain.value = 1.0;
    masterGain.connect(audioCtx.destination);
  }
  if (audioCtx.state === "suspended") await audioCtx.resume();
}

function makeBookSegment(chapterIndex, charOffset = 0, { snap = false } = {}) {
  const chapter = currentBook?.chapters?.[chapterIndex];
  if (!chapter) return null;
  const safeOffset = Math.max(0, Math.min(charOffset || 0, chapter.text.length));
  const startOffset = snap ? snapToParagraph(chapter.text, safeOffset) : safeOffset;
  if (startOffset >= chapter.text.length) return null;
  const endOffset = nextSegmentEnd(chapter.text, startOffset);
  const text = chapter.text.slice(startOffset, endOffset);
  return { chapterIndex, startOffset, endOffset, text };
}

async function fetchTtsBuffer(segment) {
  const response = await fetch("/api/tts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text: segment.text,
      voice: $("voice")?.value || "cedar",
    }),
  });
  if (!response.ok) throw new Error(await response.text());
  const arr = await response.arrayBuffer();
  await ensureAudioOutput();
  return await audioCtx.decodeAudioData(arr);
}

async function ensureBookRealtimeSession() {
  if (isRunning && ws && ws.readyState === WebSocket.OPEN) return true;
  pendingBookSegment = currentBookSegment;
  startSession();
  return false;
}

async function pushBookRealtimeSegment(segment, { prefetch = false } = {}) {
  if (!currentBook || !segment) return;
  pendingBookSegment = segment;
  if (!prefetch) currentBookSegment = segment;
  const ready = await ensureBookRealtimeSession();
  if (!ready) return;
  if (!prefetch) pendingBookSegment = null;
  const token = prefetch ? bookRealtimeToken : ++bookRealtimeToken;
  await ensureAudioOutput();
  if (!prefetch) {
    currentChapterIndex = segment.chapterIndex;
    $("chapter-select").value = String(currentChapterIndex);
    renderFullText();
    saveBookProgress(segment.chapterIndex, segment.startOffset);
    bookReadingChapterIndex = segment.chapterIndex;
    bookReadingStartOffset = segment.startOffset;
    bookSegmentEndOffset = segment.endOffset;
  }
  bookPlayerState = "playing";
  bookAutoReading = true;
  renderBookControls();
  wsSend({ type: "push_scene", scene_text: segment.text });
  trace("book_segment_push", {
    prefetch,
    chapterIndex: segment.chapterIndex,
    startOffset: segment.startOffset,
    endOffset: segment.endOffset,
    chars: segment.text.length,
  });
  return token;
}

function followingSegment(segment) {
  const chapter = currentBook.chapters[segment.chapterIndex];
  if (segment.endOffset < chapter.text.length) {
    return makeBookSegment(segment.chapterIndex, segment.endOffset, { snap: false });
  }
  const nextChapterIndex = segment.chapterIndex + 1;
  if (nextChapterIndex < currentBook.chapters.length) {
    return makeBookSegment(nextChapterIndex, 0);
  }
  return null;
}

function stopTtsSource({ invalidate = true } = {}) {
  if (invalidate) ttsPlayToken += 1;
  clearBrowserSpeechTimers();
  if (currentUtterance && window.speechSynthesis) {
    try { window.speechSynthesis.cancel(); } catch (_) {}
    currentUtterance = null;
  }
  browserSpeechProgress = null;
  if (ttsSource) {
    try { ttsSource.stop(0); } catch (_) {}
    ttsSource = null;
  }
}

function clearBrowserSpeechTimers() {
  if (browserSpeechWatchdog) {
    clearTimeout(browserSpeechWatchdog);
    browserSpeechWatchdog = null;
  }
  if (browserSpeechProgressTimer) {
    clearInterval(browserSpeechProgressTimer);
    browserSpeechProgressTimer = null;
  }
}

function saveApproxTtsProgress() {
  if (currentBookSegment && bookReadingChapterIndex !== null) {
    saveBookProgress(bookReadingChapterIndex, bookReadingStartOffset);
    return;
  }
  if (browserSpeechProgress) {
    saveBookProgress(browserSpeechProgress.chapterIndex, browserSpeechProgress.charOffset);
    return;
  }
  if (!ttsSource || !currentBook || bookReadingChapterIndex === null || !audioCtx) return;
  const duration = ttsSource.buffer?.duration || 0;
  if (!duration) return;
  const elapsed = Math.max(0, audioCtx.currentTime - (ttsSource._startedAt || audioCtx.currentTime));
  const ratio = Math.max(0, Math.min(1, elapsed / duration));
  const offset = Math.round(bookReadingStartOffset + (bookSegmentEndOffset - bookReadingStartOffset) * ratio);
  saveBookProgress(bookReadingChapterIndex, offset);
}

async function playTtsSegment(segment, bufferPromise = null) {
  if (!currentBook || !segment) return;
  if (browserTtsMode) {
    playBrowserSpeechSegment(segment);
    return;
  }
  const token = ++ttsPlayToken;
  await ensureAudioOutput();
  bookPlayerState = "loading";
  renderBookControls();
  let buffer;
  try {
    buffer = await (bufferPromise || fetchTtsBuffer(segment));
  } catch (e) {
    console.error(e);
    bookPlayerState = "paused";
    bookAutoReading = false;
    renderBookControls();
    setStatus("error");
    appendTranscript("cedar", "OpenAI Cedar indisponible : " + readableTtsError(e));
    return;
  }
  if (token !== ttsPlayToken) return;

  currentChapterIndex = segment.chapterIndex;
  $("chapter-select").value = String(currentChapterIndex);
  renderFullText();
  saveBookProgress(segment.chapterIndex, segment.startOffset);
  bookReadingChapterIndex = segment.chapterIndex;
  bookReadingStartOffset = segment.startOffset;
  bookSegmentEndOffset = segment.endOffset;
  browserSpeechProgress = { chapterIndex: segment.chapterIndex, charOffset: segment.startOffset };
  bookPlayerState = "playing";
  bookAutoReading = true;
  renderBookControls();

  const next = followingSegment(segment);
  ttsNextMeta = next;
  ttsNextPromise = next ? fetchTtsBuffer(next) : null;

  stopTtsSource({ invalidate: false });
  const src = audioCtx.createBufferSource();
  src.buffer = buffer;
  src.connect(masterGain || audioCtx.destination);
  src._startedAt = audioCtx.currentTime;
  ttsSource = src;
  src.onended = () => {
    if (token !== ttsPlayToken || bookPlayerState !== "playing") return;
    saveBookProgress(segment.chapterIndex, segment.endOffset);
    if (ttsNextMeta && ttsNextPromise) {
      playTtsSegment(ttsNextMeta, ttsNextPromise);
    } else {
      bookPlayerState = "stopped";
      bookAutoReading = false;
      appendTranscript("cedar", "(fin du livre)");
      renderBookControls();
    }
  };
  src.start(0);
}

function playBrowserSpeechSegment(segment) {
  if (!window.speechSynthesis) {
    appendTranscript("cedar", "Synthèse vocale navigateur indisponible.");
    return;
  }
  const token = ++ttsPlayToken;
  currentChapterIndex = segment.chapterIndex;
  $("chapter-select").value = String(currentChapterIndex);
  renderFullText();
  saveBookProgress(segment.chapterIndex, segment.startOffset);
  bookReadingChapterIndex = segment.chapterIndex;
  bookReadingStartOffset = segment.startOffset;
  bookSegmentEndOffset = segment.endOffset;
  browserSpeechProgress = { chapterIndex: segment.chapterIndex, charOffset: segment.startOffset };
  const segmentStartedAt = performance.now();
  const estimatedMs = Math.max(7000, segment.text.length * 85);
  bookPlayerState = "playing";
  bookAutoReading = true;
  renderBookControls();

  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(segment.text);
  utterance.lang = "fr-FR";
  utterance.rate = Math.max(0.7, Math.min(1.3, parseFloat($("speed")?.value || "0.92")));
  const voices = window.speechSynthesis.getVoices?.() || [];
  const frVoice = voices.find(v => /fr/i.test(v.lang || "")) || voices.find(v => /french|français/i.test(v.name || ""));
  if (frVoice) utterance.voice = frVoice;
  utterance.onboundary = (ev) => {
    if (token !== ttsPlayToken || typeof ev.charIndex !== "number") return;
    const charOffset = Math.max(segment.startOffset, Math.min(segment.endOffset, segment.startOffset + ev.charIndex));
    browserSpeechProgress = { chapterIndex: segment.chapterIndex, charOffset };
    saveBookProgress(segment.chapterIndex, charOffset);
  };
  utterance.onend = () => {
    clearBrowserSpeechTimers();
    if (token !== ttsPlayToken || bookPlayerState !== "playing") return;
    browserSpeechProgress = { chapterIndex: segment.chapterIndex, charOffset: segment.endOffset };
    saveBookProgress(segment.chapterIndex, segment.endOffset);
    const next = followingSegment(segment);
    if (next) playBrowserSpeechSegment(next);
    else {
      bookPlayerState = "stopped";
      bookAutoReading = false;
      appendTranscript("cedar", "(fin du livre)");
      renderBookControls();
    }
  };
  utterance.onerror = (ev) => {
    clearBrowserSpeechTimers();
    if (token !== ttsPlayToken) return;
    if (ev.error === "interrupted" || ev.error === "canceled") return;
    appendTranscript("cedar", "Erreur synthèse vocale : " + (ev.error || "inconnue"));
    bookPlayerState = "paused";
    renderBookControls();
  };
  currentUtterance = utterance;
  window.speechSynthesis.speak(utterance);
  browserSpeechProgressTimer = setInterval(() => {
    if (token !== ttsPlayToken || bookPlayerState !== "playing") return;
    const elapsed = performance.now() - segmentStartedAt;
    const ratio = Math.max(0, Math.min(0.96, elapsed / estimatedMs));
    const charOffset = Math.round(segment.startOffset + (segment.endOffset - segment.startOffset) * ratio);
    if (!browserSpeechProgress || charOffset > browserSpeechProgress.charOffset) {
      browserSpeechProgress = { chapterIndex: segment.chapterIndex, charOffset };
      saveBookProgress(segment.chapterIndex, charOffset);
    }
  }, 1000);
  browserSpeechWatchdog = setTimeout(() => {
    if (token !== ttsPlayToken || bookPlayerState !== "playing") return;
    const resumeOffset = browserSpeechProgress?.charOffset || segment.startOffset;
    stopTtsSource();
    appendTranscript("cedar", "(reprise automatique après blocage vocal)");
    playBookFromOffset(segment.chapterIndex, resumeOffset);
  }, estimatedMs + 6000);
}

function readableTtsError(error) {
  const raw = String(error?.message || error || "");
  if (raw.includes("api.model.audio.request") || raw.includes("missing_scope")) {
    return "la clé OpenAI n'a pas le scope audio `api.model.audio.request`. Il faut une clé projet avec accès Audio/Speech pour utiliser Cedar.";
  }
  return raw.slice(0, 300) || "erreur inconnue";
}

function playBookFromOffset(chapterIndex, charOffset) {
  if (!currentBook) return;
  bookRealtimeToken += 1;
  stopTtsSource();
  killLocalAudio();
  resetBookSegmentState();
  const segment = makeBookSegment(chapterIndex, charOffset, { snap: false });
  if (!segment) return;
  pushBookRealtimeSegment(segment);
}

function playBookFromProgress() {
  if (!currentBook) return;
  const saved = currentBookProgress();
  currentChapterIndex = saved.chapterIndex;
  $("chapter-select").value = String(currentChapterIndex);
  renderFullText();
  playBookFromOffset(saved.chapterIndex, saved.charOffset);
}

function playBookFromSelection() {
  if (!currentBook) return;
  const chapter = currentBook.chapters[currentChapterIndex];
  if (!chapter) return;
  const selection = lastBookSelection || captureBookSelection();
  if (!selection || selection.chapterIndex !== currentChapterIndex) {
    appendTranscript("you", "(sélectionne un morceau du texte affiché puis appuie sur Depuis sélection)");
    return;
  }
  const charOffset = selection.charOffset;
  bookAutoReading = true;
  bookPlayerState = "playing";
  resetBookSegmentState();
  saveBookProgress(currentChapterIndex, charOffset);
  renderBookControls();
  playBookFromOffset(currentChapterIndex, charOffset);
}

function captureBookSelection() {
  if (!currentBook) return null;
  const txtDiv = $("oeuvre-text");
  const sel = window.getSelection && window.getSelection();
  if (!txtDiv || !sel || !sel.rangeCount || sel.isCollapsed) return lastBookSelection;
  const range = sel.getRangeAt(0);
  if (!txtDiv.contains(range.startContainer)) return lastBookSelection;
  let offset = 0;
  if (range.startContainer.nodeType === Node.TEXT_NODE) {
    offset = range.startOffset;
  } else {
    const selected = String(sel).trim();
    if (!selected) return lastBookSelection;
    offset = selectedOffsetInDisplayedChapter(selected, currentBook.chapters[currentChapterIndex]?.text || "");
  }
  const chapter = currentBook.chapters[currentChapterIndex];
  if (!chapter) return lastBookSelection;
  lastBookSelection = {
    chapterIndex: currentChapterIndex,
    charOffset: Math.max(0, Math.min(offset, chapter.text.length)),
  };
  return lastBookSelection;
}

function selectedOffsetInDisplayedChapter(selected, chapterText) {
  const txtDiv = $("oeuvre-text");
  const sel = window.getSelection && window.getSelection();
  if (sel && sel.rangeCount) {
    const range = sel.getRangeAt(0);
    if (txtDiv.contains(range.startContainer) && range.startContainer.nodeType === Node.TEXT_NODE) {
      return range.startOffset;
    }
  }
  const idx = normalizeTextForSearch(chapterText).indexOf(normalizeTextForSearch(selected).slice(0, 180));
  if (idx < 0) return 0;
  return offsetFromNormalizedIndex(chapterText, idx);
}

function pauseBookPlayback() {
  if (!currentBook) return;
  saveApproxTtsProgress();
  bookPlayerState = "paused";
  bookAutoReading = false;
  bookRealtimeToken += 1;
  pendingBookSegment = null;
  currentBookSegment = null;
  bookAudioWatchKey = null;
  bookPendingResponseDone = false;
  stopTtsSource();
  cancelCurrentResponseImmediately();
  renderBookControls();
}

function stopBookPlayback() {
  if (!currentBook) return;
  bookPlayerState = "stopped";
  bookAutoReading = false;
  bookRealtimeToken += 1;
  pendingBookSegment = null;
  currentBookSegment = null;
  bookAudioWatchKey = null;
  bookPendingResponseDone = false;
  stopTtsSource();
  cancelCurrentResponseImmediately();
  saveBookProgress(currentChapterIndex, 0);
  renderBookControls();
}

function jumpBookChapter(delta) {
  if (!currentBook) return;
  const nextIndex = Math.max(0, Math.min(currentChapterIndex + delta, currentBook.chapters.length - 1));
  if (nextIndex === currentChapterIndex) return;
  bookAutoReading = false;
  bookPlayerState = "loading";
  stopTtsSource();
  currentChapterIndex = nextIndex;
  resetBookSegmentState();
  saveBookProgress(currentChapterIndex, 0);
  $("chapter-select").value = String(currentChapterIndex);
  renderFullText();
  playBookFromOffset(currentChapterIndex, 0);
}

function normalizeTextForSearch(text) {
  return (text || "").replace(/\s+/g, " ").trim();
}

function offsetFromNormalizedIndex(text, normalizedIndex) {
  let normCount = 0;
  let inSpace = false;
  for (let i = 0; i < text.length; i++) {
    const isSpace = /\s/.test(text[i]);
    if (isSpace) {
      if (!inSpace) {
        if (normCount >= normalizedIndex) return i;
        normCount += 1;
        inSpace = true;
      }
    } else {
      if (normCount >= normalizedIndex) return i;
      normCount += 1;
      inSpace = false;
    }
  }
  return text.length;
}

function resetBookSegmentState() {
  bookReadingChapterIndex = null;
  bookReadingStartOffset = 0;
  bookSegmentEndOffset = 0;
  bookTranscriptChars = 0;
  currentBookSegment = null;
  pendingBookSegment = null;
  bookAudioWatchKey = null;
  bookPendingResponseDone = false;
}

function updateProgressFromTranscript(extraChars = 0) {
  if (!currentBook || bookReadingChapterIndex === null) return;
  const chapter = currentBook.chapters[bookReadingChapterIndex];
  if (!chapter) return;
  bookTranscriptChars += extraChars;
  const offset = Math.min(chapter.text.length, bookReadingStartOffset + bookTranscriptChars);
  saveBookProgress(bookReadingChapterIndex, offset);
}

function cancelCurrentResponseImmediately() {
  ignoreNextResponseDone = true;
  ignoreResponseDoneUntil = Date.now() + 1500;
  killLocalAudio();
  wsSend({ type: "cancel" });
}

function waitForBookAudioEnd(token, segment) {
  const watchKey = `${token}:${segment.chapterIndex}:${segment.startOffset}:${segment.endOffset}`;
  if (bookAudioWatchKey === watchKey) return;
  bookAudioWatchKey = watchKey;
  const started = performance.now();
  const targetEndTime = audioCtx ? playbackTime : 0;
  let nextRequested = false;
  let requestedNext = null;
  const check = () => {
    if (token !== bookRealtimeToken || bookPlayerState !== "playing") {
      if (bookAudioWatchKey === watchKey) bookAudioWatchKey = null;
      return;
    }
    const remaining = audioCtx ? targetEndTime - audioCtx.currentTime : 0;
    const next = followingSegment(segment);
    if (next && !nextRequested && remaining <= 15) {
      nextRequested = true;
      requestedNext = next;
      pushBookRealtimeSegment(next, { prefetch: true });
    }
    const audioDone = remaining <= 0.08;
    const timedOut = performance.now() - started > Math.max(20000, segment.text.length * 180);
    if (!audioDone && !timedOut) {
      setTimeout(check, 120);
      return;
    }
    if (bookAudioWatchKey === watchKey) bookAudioWatchKey = null;
    saveBookProgress(segment.chapterIndex, segment.endOffset);
    if (requestedNext) {
      currentBookSegment = requestedNext;
      pendingBookSegment = null;
      currentChapterIndex = requestedNext.chapterIndex;
      $("chapter-select").value = String(currentChapterIndex);
      renderFullText();
      bookReadingChapterIndex = requestedNext.chapterIndex;
      bookReadingStartOffset = requestedNext.startOffset;
      bookSegmentEndOffset = requestedNext.endOffset;
      renderBookControls();
      if (bookPendingResponseDone) {
        bookPendingResponseDone = false;
        waitForBookAudioEnd(token, requestedNext);
      }
    } else if (next) {
      pushBookRealtimeSegment(next);
    } else {
      currentBookSegment = null;
      pendingBookSegment = null;
      bookPlayerState = "stopped";
      bookAutoReading = false;
      appendTranscript("cedar", "(fin du livre)");
      renderBookControls();
    }
  };
  setTimeout(check, 120);
}

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
      renderBookControls();
      if (!currentBook) appendTranscript("cedar", "(prêt — parle.)");
      if (autoStartPending) {
        autoStartPending = false;
        if (currentBook) {
          pushBookRealtimeSegment(pendingBookSegment || currentBookSegment || makeBookSegment(currentChapterIndex, currentBookProgress().charOffset, { snap: false }));
        } else if (currentOeuvre && currentOeuvre.text_complet) {
          appendTranscript("you", `(lecture automatique : ${currentOeuvre.text_complet.length} caractères)`);
          wsSend({ type: "push_scene", scene_text: currentOeuvre.text_complet });
        }
      }
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
      if (currentBook && currentBookSegment && bookPlayerState === "playing") setStatus("speaking");
      else setStatus("ready");
      cedarLineDone = true;
      chunkSchedule.length = 0;
      pendingSwitches.length = 0;
      if (ignoreNextResponseDone || Date.now() < ignoreResponseDoneUntil) {
        ignoreNextResponseDone = false;
        renderBookControls();
        break;
      }
      if (currentBook && currentBookSegment && bookPlayerState === "playing") {
        if (bookAudioWatchKey && pendingBookSegment) {
          bookPendingResponseDone = true;
          renderBookControls();
          break;
        }
        waitForBookAudioEnd(bookRealtimeToken, currentBookSegment);
      }
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
  if (!audioCtx) return;
  const p = profileForPerso(persoName);
  const t = audioCtx.currentTime;
  const ramp = 0.03;
  if (pitchNode) {
    pitchNode.port.postMessage({ name: "pitchSemitones", value: p.pitch_shift });
    pitchNode.port.postMessage({ name: "tempo",          value: p.speed_hint || 1.0 });
  }
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
  src.connect(pitchNode || masterGain || audioCtx.destination);
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
  bookAutoReading = false;
  autoStartPending = false;
  wsSend({ type: "stop" });
  if (micStream) { micStream.getTracks().forEach(t => t.stop()); micStream = null; }
  if (ws) { ws.close(); ws = null; }
  isRunning = false;
  renderBookControls();
  setStatus("idle");
}

init().catch(e => { console.error(e); setStatus("error"); });
