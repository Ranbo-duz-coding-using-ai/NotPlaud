/* NotPlaud — desktop UI controller */

const state = {
  data: null,
  view: "home",
  folderId: "all",
  query: "",
  selectedNoteId: null,
  menu: null,
  recording: null,
  lastSignature: "",
  busy: false,
};

const navItems = [
  { id: "home", label: "Home", icon: "home" },
  { id: "device", label: "Device", icon: "device" },
  { id: "computer", label: "Calls", icon: "mic" },
  { id: "settings", label: "Settings", icon: "settings" },
];

const sourceOptions = [
  { id: "system-and-mic", label: "Computer audio + my microphone" },
  { id: "system-only", label: "Computer audio only" },
  { id: "screen-audio", label: "A specific app, window, or tab" },
  { id: "mic-only", label: "Microphone only" },
];

const detailOrder = ["low", "medium", "high", "ultra"];

const summaryProviders = [
  { id: "openai", label: "OpenAI", models: ["gpt-4o-mini", "gpt-4.1-mini", "gpt-4.1", "gpt-4o"], env: "OPENAI_API_KEY" },
  { id: "google", label: "Google", models: ["gemini-2.0-flash", "gemini-1.5-pro"], env: "GOOGLE_API_KEY" },
  {
    id: "anthropic",
    label: "Claude",
    models: ["claude-sonnet-5", "claude-opus-5", "claude-haiku-4-5-20251001"],
    env: "ANTHROPIC_API_KEY",
  },
];

const transcriptionProviders = [
  { id: "openai", label: "OpenAI", models: ["whisper-1", "gpt-4o-transcribe"], env: "OPENAI_API_KEY" },
  { id: "google", label: "Google", models: ["gemini-2.0-flash"], env: "GOOGLE_API_KEY" },
];

const whisperSizes = ["tiny", "base", "small", "medium", "large-v3", "distil-large-v3"];

window.addEventListener("DOMContentLoaded", init);

async function init() {
  applyTheme(localStorage.getItem("notplaud-theme") || "dark");

  document.getElementById("newFolderButton").innerHTML = icon("plus");
  document.getElementById("newFolderButton").addEventListener("click", openFolderModal);

  document.addEventListener("click", handleDocumentClick);
  document.addEventListener("input", handleInput);
  document.addEventListener("change", handleChange);
  document.addEventListener("keydown", handleKeydown);
  document.addEventListener("dragstart", handleDragStart);
  document.addEventListener("dragend", handleDragEnd);
  document.addEventListener("dragover", handleDragOver);
  document.addEventListener("dragleave", handleDragLeave);
  document.addEventListener("drop", handleDrop);
  window.addEventListener("beforeunload", () => {
    if (state.recording) cleanupRecording(state.recording);
  });

  try {
    await loadData();
  } catch (error) {
    toast(`Could not reach the NotPlaud service: ${error.message}`, "error");
    return;
  }
  render();
  window.setInterval(poll, 3000);
}

async function loadData() {
  state.data = await request("/api/bootstrap");
  applyTheme(state.data.settings.theme || "dark");
  if (!state.selectedNoteId && state.data.notes.length) {
    state.selectedNoteId = state.data.notes[0].id;
  }
  state.lastSignature = signature(state.data);
}

/* Background refresh. Never redraws while the user is typing, has a menu or
   modal open, or is mid-recording — that would blow away their input. */
async function poll() {
  if (state.busy) return;
  if (state.menu) return;
  if (document.getElementById("modalRoot").children.length) return;
  const active = document.activeElement;
  if (active && ["INPUT", "SELECT", "TEXTAREA"].includes(active.tagName)) return;

  try {
    const data = await request("/api/bootstrap");
    const next = signature(data);
    if (next === state.lastSignature) return;
    state.data = data;
    state.lastSignature = next;
    applyTheme(data.settings.theme || "dark");
    render();
  } catch (error) {
    /* transient — the next tick will retry */
  }
}

function signature(data) {
  return JSON.stringify({
    notes: data.notes.map((note) => [note.id, note.status, note.title, note.folder_id, note.detail_level, note.updated_at]),
    folders: data.folders.map((folder) => folder.id + folder.name),
    settings: data.settings,
    processing: data.processing,
    wifi: data.currentWifi,
    ingest: data.ingest,
    usb: data.usbVolumes,
    device: data.deviceStatus,
  });
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    throw new Error((payload && payload.error) || payload || "Request failed");
  }
  return payload;
}

/* ---------------- Theme ---------------- */

function applyTheme(theme) {
  const value = theme === "light" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", value);
  localStorage.setItem("notplaud-theme", value);
}

async function setTheme(theme) {
  applyTheme(theme);
  renderThemeToggle();
  await saveSettings({ theme }, { silent: true });
}

/* ---------------- Render ---------------- */

function render() {
  if (!state.data) return;
  renderNav();
  renderThemeToggle();
  renderFolders();
  renderTopbar();
  renderPage();
  renderMenu();
}

function renderNav() {
  const processing = state.data.processing || [];
  document.getElementById("primaryNav").innerHTML = navItems
    .map(
      (item) => `
        <button class="nav-button ${state.view === item.id ? "active" : ""}" type="button" data-view="${item.id}">
          ${icon(item.icon)}
          <span>${escapeHtml(item.label)}</span>
          ${item.id === "home" && processing.length ? `<span class="nav-badge">${processing.length}</span>` : ""}
        </button>
      `,
    )
    .join("");
}

function renderThemeToggle() {
  const current = document.documentElement.getAttribute("data-theme") || "dark";
  document.getElementById("themeToggle").innerHTML = `
    <button class="theme-button ${current === "light" ? "active" : ""}" type="button" data-action="theme" data-theme="light">
      ${icon("sun")}<span>Light</span>
    </button>
    <button class="theme-button ${current === "dark" ? "active" : ""}" type="button" data-action="theme" data-theme="dark">
      ${icon("moon")}<span>Dark</span>
    </button>
  `;
}

function renderFolders() {
  const notes = state.data.notes;
  const rows = [
    folderRow({ id: "all", name: "All Notes" }, notes.length, false),
    ...state.data.folders.map((folder) =>
      folderRow(folder, notes.filter((note) => note.folder_id === folder.id).length, folder.id !== "inbox"),
    ),
  ];
  document.getElementById("folderList").innerHTML = rows.join("");
}

function folderRow(folder, count, deletable) {
  return `
    <button class="folder-row ${state.folderId === folder.id ? "active" : ""}" type="button"
      data-folder-id="${escapeAttr(folder.id)}" data-folder-drop="${escapeAttr(folder.id)}">
      ${icon(folder.id === "all" ? "layers" : "folder")}
      <span>${escapeHtml(folder.name)}</span>
      <span class="count">${count}</span>
      ${
        deletable
          ? `<span class="folder-delete" role="button" title="Delete folder"
               data-action="delete-folder" data-folder-id="${escapeAttr(folder.id)}">${icon("x")}</span>`
          : ""
      }
    </button>
  `;
}

function renderTopbar() {
  const homeActions =
    state.view === "home"
      ? `
        <label class="search">
          ${icon("search")}
          <input id="searchInput" type="search" placeholder="Search notes" value="${escapeAttr(state.query)}" autocomplete="off">
        </label>
        <button class="text-button" type="button" data-action="upload-audio">${icon("upload")}<span>Import file</span></button>
        <input id="audioUpload" class="hidden-input" type="file" accept="audio/*">
        <button class="text-button primary" type="button" data-action="sync-incoming">${icon("sync")}<span>Sync device</span></button>
      `
      : "";

  document.getElementById("topbar").innerHTML = `
    <div class="title-block">
      <h1>${escapeHtml(pageTitle())}</h1>
      <p>${escapeHtml(pageSubtitle())}</p>
    </div>
    <div class="top-actions">${homeActions}</div>
  `;
}

function pageTitle() {
  if (state.view === "device") return "Device";
  if (state.view === "computer") return "Calls & Meetings";
  if (state.view === "settings") return "Settings";
  if (state.folderId === "all") return "All Notes";
  return folderName(state.folderId) || "Notes";
}

function pageSubtitle() {
  if (state.view === "device") return "Capture profile and note style for your NotPlaud device.";
  if (state.view === "computer") return "Record calls and lectures straight from this computer.";
  if (state.view === "settings") return "Models, detail, WiFi, and transfer settings.";
  const count = filteredNotes().length;
  const processing = (state.data.processing || []).length;
  const base = `${count} ${count === 1 ? "note" : "notes"} in view`;
  return processing ? `${base} · ${processing} processing` : base;
}

function renderPage() {
  const page = document.getElementById("page");
  if (state.view === "home") page.innerHTML = renderHome();
  if (state.view === "device") page.innerHTML = renderDevice();
  if (state.view === "computer") {
    page.innerHTML = renderComputer();
    if (state.recording) drawLiveMeter();
    else drawIdleMeter();
  }
  if (state.view === "settings") page.innerHTML = renderSettings();
}

/* ---------------- Home ---------------- */

function renderHome() {
  const notes = filteredNotes();
  if (notes.length && !notes.some((note) => note.id === state.selectedNoteId)) {
    state.selectedNoteId = notes[0].id;
  }

  const notesMarkup = notes.length
    ? `<div class="notes-grid">${notes.map(renderNoteCard).join("")}</div>`
    : `
      <div class="empty-state">
        <div>
          <strong>No notes here yet</strong>
          <span>Recordings synced from your device and calls captured on this computer will show up here.</span>
        </div>
      </div>
    `;

  return `<div class="home-layout"><section>${notesMarkup}</section>${renderDetailPanel(notes)}</div>`;
}

function isProcessing(noteId) {
  return (state.data.processing || []).includes(noteId);
}

function renderNoteCard(note) {
  const detail = note.detail_level || state.data.settings.default_detail || "medium";
  const working = isProcessing(note.id) || note.status === "processing";
  return `
    <article class="note-card ${state.selectedNoteId === note.id ? "selected" : ""}" draggable="true"
      data-note-card data-note-id="${escapeAttr(note.id)}">
      <div class="note-head">
        <h2>${escapeHtml(note.title)}</h2>
        <button class="icon-button" type="button" title="Note options" aria-label="Note options"
          data-action="open-menu" data-note-id="${escapeAttr(note.id)}">${icon("more")}</button>
      </div>
      <div class="note-meta">
        <span>${formatDate(note.created_at)}</span>
        <span class="pill ${note.source === "device" ? "device" : "computer"}">${escapeHtml(note.source)}</span>
        ${
          working
            ? `<span class="pill processing">working…</span>`
            : `<span class="pill ${note.status === "needs_ai" ? "needs_ai" : ""}">${escapeHtml(statusLabel(note.status))}</span>`
        }
        <span class="pill">${escapeHtml(presetName(note.preset_id))}</span>
      </div>
      <div class="summary-preview">${escapeHtml(summaryPreview(note))}</div>
      <div class="segment" role="group" aria-label="Detail level">
        ${detailOrder
          .map(
            (level) => `
              <button class="segment-button ${detail === level ? "active" : ""}" type="button"
                data-action="set-detail" data-note-id="${escapeAttr(note.id)}" data-detail="${level}">
                ${detailName(level)}
              </button>
            `,
          )
          .join("")}
      </div>
    </article>
  `;
}

function renderDetailPanel(notes) {
  const note = notes.find((item) => item.id === state.selectedNoteId) || notes[0];
  if (!note) {
    return `
      <aside class="detail-panel">
        <div class="empty-state">
          <div>
            <strong>No note selected</strong>
            <span>Pick a recording to read its note and play the original audio.</span>
          </div>
        </div>
      </aside>
    `;
  }
  const working = isProcessing(note.id) || note.status === "processing";
  return `
    <aside class="detail-panel">
      <div>
        <h2>${escapeHtml(note.title)}</h2>
        <div class="note-meta">
          <span>${formatDate(note.created_at)}</span>
          <span class="pill ${note.source === "device" ? "device" : "computer"}">${escapeHtml(note.source)}</span>
          <span class="pill">${escapeHtml(presetName(note.preset_id))}</span>
          <span class="pill">${detailName(note.detail_level)}</span>
        </div>
      </div>
      ${working ? `<div class="status-line"><span class="spinner"></span><span>Transcribing and writing the note…</span></div>` : ""}
      ${
        note.status === "needs_ai" && note.error
          ? `<div class="status-line"><span class="status-dot warn"></span><span>${escapeHtml(note.error)}</span></div>`
          : ""
      }
      <audio controls preload="none" src="/api/notes/${note.id}/audio"></audio>
      <div class="detail-actions">
        <button class="text-button" type="button" data-action="raw-transcript" data-note-id="${note.id}">
          ${icon("file")}<span>Raw transcript</span>
        </button>
        <button class="text-button" type="button" data-action="download-pdf" data-note-id="${note.id}">
          ${icon("download")}<span>PDF</span>
        </button>
        <button class="text-button" type="button" data-action="reprocess" data-note-id="${note.id}" ${working ? "disabled" : ""}>
          ${icon("spark")}<span>Regenerate</span>
        </button>
      </div>
      ${
        note.action_items && note.action_items.length
          ? `<div class="markdown"><h3>Action Items</h3><ul>${note.action_items
              .map((item) => `<li>${escapeHtml(item)}</li>`)
              .join("")}</ul></div>`
          : ""
      }
      <div class="markdown">${renderMarkdown(note.summary || "")}</div>
    </aside>
  `;
}

/* ---------------- Device ---------------- */

function renderDevice() {
  const settings = state.data.settings;
  const status = state.data.deviceStatus || {};
  return `
    <div class="stack">
      <div class="status-line">
        <span class="status-dot ${status.connected ? "ok" : "off"}"></span>
        <span>${escapeHtml(status.message || "Not connected")}</span>
        <button class="text-button" type="button" data-action="push-config" style="margin-left:auto">
          ${icon("bluetooth")}<span>Push settings to device</span>
        </button>
      </div>

      <section class="panel">
        <div class="section-title">
          <div>
            <h2>Microphone Capture Mode</h2>
            <p>How the 4-mic array on ${escapeHtml(settings.device_name || "your device")} listens.</p>
          </div>
        </div>
        <div class="choice-grid">
          ${state.data.deviceModes
            .map((mode) => choiceCard(mode, settings.device_mode, "device_mode", modeIcon(mode.id)))
            .join("")}
        </div>
      </section>

      <section class="panel">
        <div class="section-title">
          <div>
            <h2>Note Style for Device Recordings</h2>
            <p>Tells the AI what kind of note to write from anything the device records.</p>
          </div>
        </div>
        <div class="choice-grid">
          ${state.data.presets
            .map((preset) => choiceCard(preset, settings.device_preset, "device_preset", presetIcon(preset.id)))
            .join("")}
        </div>
      </section>
    </div>
  `;
}

function choiceCard(item, activeId, setting, iconName) {
  return `
    <button class="choice-card ${activeId === item.id ? "active" : ""}" type="button"
      data-action="setting-card" data-setting="${escapeAttr(setting)}" data-value="${escapeAttr(item.id)}">
      <div class="choice-head">
        ${icon(iconName)}
        <h3>${escapeHtml(item.name)}</h3>
        <span class="check">${icon("check")}</span>
      </div>
      <p>${escapeHtml(item.description)}</p>
    </button>
  `;
}

/* ---------------- Calls ---------------- */

function renderComputer() {
  const settings = state.data.settings;
  const recording = state.recording;
  const label = recording ? (recording.paused ? "Paused" : "Recording") : "Ready";
  return `
    <div class="recorder-grid">
      <section class="panel">
        <div class="section-title">
          <div>
            <h2>Record this computer</h2>
            <p>Captures Zoom, Meet, Teams, or any other app playing through your speakers.</p>
          </div>
        </div>
        <div class="field" style="margin-bottom:14px">
          <label for="computerSource">Audio source</label>
          <select id="computerSource" data-action="computer-source" ${recording ? "disabled" : ""}>
            ${sourceOptions
              .map(
                (source) => `
                  <option value="${source.id}" ${settings.computer_source === source.id ? "selected" : ""}>
                    ${escapeHtml(source.label)}
                  </option>
                `,
              )
              .join("")}
          </select>
          <span class="hint">After you press Start, your system asks which screen, window, or tab to take audio from — pick the meeting there and make sure "Share audio" is checked.</span>
        </div>
        <div class="meter"><canvas id="meterCanvas" width="900" height="260"></canvas></div>
        <div class="record-status">
          <span>${recording && !recording.paused ? '<span class="recording-dot"></span>' : ""}${label}</span>
          <span class="timer" id="recordTimer">${recording ? elapsedLabel() : "00:00"}</span>
        </div>
        <div class="button-row">
          <button class="text-button primary" type="button" data-action="start-recording" ${recording ? "disabled" : ""}>
            ${icon("record")}<span>Start</span>
          </button>
          <button class="text-button warn" type="button" data-action="pause-recording" ${recording ? "" : "disabled"}>
            ${icon(recording && recording.paused ? "play" : "pause")}<span>${recording && recording.paused ? "Resume" : "Pause"}</span>
          </button>
          <button class="text-button danger" type="button" data-action="stop-recording" ${recording ? "" : "disabled"}>
            ${icon("stop")}<span>Stop &amp; save</span>
          </button>
        </div>
      </section>

      <section class="panel">
        <div class="section-title">
          <div>
            <h2>Note Style for Calls</h2>
            <p>Tunes what the AI writes up after the recording stops.</p>
          </div>
        </div>
        <div class="choice-grid">
          ${state.data.presets
            .map((preset) => choiceCard(preset, settings.computer_preset, "computer_preset", presetIcon(preset.id)))
            .join("")}
        </div>
      </section>
    </div>
  `;
}

/* ---------------- Settings ---------------- */

function renderSettings() {
  const settings = state.data.settings;
  const ingest = state.data.ingest || {};
  const usb = state.data.usbVolumes || [];

  return `
    <div class="settings-layout">
      <section class="panel">
        <div class="section-title">
          <div><h2>General</h2><p>Basics for this install.</p></div>
        </div>
        <div class="field-grid">
          <div class="field">
            <label for="deviceName">Device name</label>
            <input id="deviceName" data-setting-input="device_name" value="${escapeAttr(settings.device_name || "")}">
            <span class="hint">Shown on the device and used for its Bluetooth name.</span>
          </div>
          <div class="field">
            <label for="appearance">Appearance</label>
            <select id="appearance" data-setting-select="theme">
              <option value="dark" ${settings.theme === "dark" ? "selected" : ""}>Dark</option>
              <option value="light" ${settings.theme === "light" ? "selected" : ""}>Light</option>
            </select>
          </div>
        </div>
        <label class="toggle-row">
          <input type="checkbox" data-setting-toggle="auto_process" ${settings.auto_process ? "checked" : ""}>
          <span>Transcribe and summarize new recordings automatically</span>
        </label>
      </section>

      ${renderModelBlock({
        title: "Summary model",
        iconName: "spark",
        sourceKey: "summary_source",
        pathKey: "local_summary_model_path",
        providerKey: "summary_api_provider",
        modelKey: "summary_api_model",
        keyKey: "summary_api_key",
        envKey: "summary_api_key_env",
        providers: summaryProviders,
        pickKind: "summary",
        localHint:
          "Pick a .gguf chat model (Llama, Qwen, Mistral…). Runs on this computer through llama-cpp-python — nothing leaves the machine.",
        settings,
      })}

      ${renderModelBlock({
        title: "Transcription model",
        iconName: "audio",
        sourceKey: "transcription_source",
        pathKey: "local_transcription_model_path",
        providerKey: "transcription_api_provider",
        modelKey: "transcription_api_model",
        keyKey: "transcription_api_key",
        envKey: "transcription_api_key_env",
        providers: transcriptionProviders,
        pickKind: "transcription",
        localHint:
          "Type a Whisper size (downloaded once, then cached) or browse to a converted model folder. Runs locally through faster-whisper.",
        presets: whisperSizes,
        settings,
      })}

      <section class="panel">
        <div class="section-title">
          <div>
            <h2>Default detail</h2>
            <p>How much detail new notes get. You can still change any single note from its ⋯ menu.</p>
          </div>
        </div>
        <div class="choice-grid">
          ${(state.data.detailLevels || [])
            .map((level) => choiceCard(level, settings.default_detail, "default_detail", detailIcon(level.id)))
            .join("")}
        </div>
      </section>

      <section class="panel">
        <div class="section-title">
          <div>
            <h2>WiFi networks</h2>
            <p>The device is told over Bluetooth which network to join — whichever one this computer is on.</p>
          </div>
          <button class="text-button" type="button" data-action="add-wifi">${icon("plus")}<span>Add network</span></button>
        </div>
        <div class="status-line" style="margin-bottom:12px">
          <span class="status-dot ${state.data.currentWifi ? "ok" : "warn"}"></span>
          <span>${
            state.data.currentWifi
              ? `This computer is on <strong>${escapeHtml(state.data.currentWifi)}</strong>`
              : "No WiFi network detected on this computer"
          }</span>
        </div>
        <div class="list-rows">
          ${
            (settings.wifi_networks || []).length
              ? settings.wifi_networks
                  .slice()
                  .sort((a, b) => (b.priority || 0) - (a.priority || 0))
                  .map((network) => renderWifiRow(network, state.data.currentWifi))
                  .join("")
              : `<div class="status-line"><span class="status-dot"></span><span>No saved networks yet. Add the ones you use so the device can reconnect on its own.</span></div>`
          }
        </div>
        <label class="toggle-row">
          <input type="checkbox" data-setting-toggle="auto_wifi_sync" ${settings.auto_wifi_sync ? "checked" : ""}>
          <span>Automatically push the current network to the device over Bluetooth</span>
        </label>
        <div class="button-row">
          <button class="text-button" type="button" data-action="push-config">${icon("bluetooth")}<span>Push to device now</span></button>
          <button class="text-button" type="button" data-action="scan-ble">${icon("search")}<span>Find device</span></button>
        </div>
      </section>

      <section class="panel">
        <div class="section-title">
          <div>
            <h2>Transfers</h2>
            <p>How recordings get from the device to this computer.</p>
          </div>
        </div>

        <div class="model-block" style="margin-bottom:12px">
          <div class="model-block-head">
            ${icon("wifi")}<h3>Over WiFi</h3>
            <span class="tag">${ingest.running ? "Listening" : "Off"}</span>
          </div>
          <p class="hint">When you press Transmit on the device, it uploads everything it has not sent yet to this address.</p>
          <div class="path-box">
            <code>http://${escapeHtml(ingest.host || "")}:${escapeHtml(String(ingest.port || ""))}/upload</code>
            <button class="icon-button" type="button" title="Copy address" data-action="copy"
              data-copy="http://${escapeAttr(ingest.host || "")}:${escapeAttr(String(ingest.port || ""))}/upload">${icon("copy")}</button>
          </div>
          <div class="field-grid">
            <div class="field">
              <label for="ingestPort">Port</label>
              <input id="ingestPort" type="number" data-setting-input="ingest_port" value="${escapeAttr(String(ingest.port || 8788))}">
            </div>
            <div class="field">
              <label for="ingestToken">Pairing token</label>
              <div class="input-row">
                <input id="ingestToken" value="${escapeAttr(ingest.token || "")}" readonly>
                <button class="text-button" type="button" data-action="copy" data-copy="${escapeAttr(ingest.token || "")}">${icon("copy")}</button>
              </div>
              <span class="hint">Sent to the device with its settings. Only uploads carrying this token are accepted.</span>
            </div>
          </div>
          <label class="toggle-row">
            <input type="checkbox" data-setting-toggle="ingest_enabled" ${settings.ingest_enabled ? "checked" : ""}>
            <span>Accept WiFi uploads from the device</span>
          </label>
        </div>

        <div class="model-block">
          <div class="model-block-head">
            ${icon("usb")}<h3>Over USB</h3>
            <span class="tag">${usb.length ? `${usb.length} connected` : "Not connected"}</span>
          </div>
          <p class="hint">Plug the device in with a data cable and its card appears as a drive. Everything not already imported is copied across.</p>
          ${
            usb.length
              ? usb
                  .map(
                    (volume) => `
                      <div class="list-row current">
                        <div class="grow">
                          <strong>${escapeHtml(volume.name)}</strong>
                          <span>${volume.files} recording${volume.files === 1 ? "" : "s"} · ${formatBytes(volume.bytes)}</span>
                        </div>
                      </div>
                    `,
                  )
                  .join("")
              : ""
          }
          <div class="button-row">
            <button class="text-button ${usb.length ? "primary" : ""}" type="button" data-action="usb-import" ${usb.length ? "" : "disabled"}>
              ${icon("download")}<span>Import from USB</span>
            </button>
          </div>
        </div>

        <div class="field" style="margin-top:14px">
          <label>Watched folder</label>
          <div class="path-box">
            <code>${escapeHtml(settings.incoming_path || "")}</code>
            <button class="icon-button" type="button" title="Copy path" data-action="copy"
              data-copy="${escapeAttr(settings.incoming_path || "")}">${icon("copy")}</button>
          </div>
          <span class="hint">Any audio file dropped here is picked up on the next sync.</span>
        </div>
      </section>
    </div>
  `;
}

function renderModelBlock(config) {
  const { settings } = config;
  const isLocal = (settings[config.sourceKey] || "local") === "local";
  const provider = settings[config.providerKey] || config.providers[0].id;
  const providerInfo = config.providers.find((item) => item.id === provider) || config.providers[0];
  const keySet = settings[config.keyKey] === "********";

  return `
    <section class="panel">
      <div class="section-title">
        <div><h2>${escapeHtml(config.title)}</h2><p>Runs on this computer by default. Nothing is uploaded unless you choose an API.</p></div>
      </div>
      <div class="model-block">
        <div class="model-block-head">
          ${icon(config.iconName)}
          <h3>Where it runs</h3>
          <span class="tag">${isLocal ? "On device" : providerInfo.label}</span>
        </div>
        <div class="field">
          <select data-setting-select="${escapeAttr(config.sourceKey)}">
            <option value="local" ${isLocal ? "selected" : ""}>On-device model (offline)</option>
            <option value="api" ${isLocal ? "" : "selected"}>API key (cloud)</option>
          </select>
        </div>

        ${
          isLocal
            ? `
              <div class="field">
                <label>Model</label>
                <div class="input-row">
                  <input data-setting-input="${escapeAttr(config.pathKey)}"
                    ${config.presets ? `list="${escapeAttr(config.pathKey)}-presets"` : ""}
                    placeholder="${config.presets ? "base" : "/path/to/model.gguf"}"
                    value="${escapeAttr(settings[config.pathKey] || "")}">
                  <button class="text-button" type="button" data-action="pick-file" data-kind="${escapeAttr(config.pickKind)}"
                    data-target="${escapeAttr(config.pathKey)}">${icon("folder")}<span>Browse…</span></button>
                </div>
                ${
                  config.presets
                    ? `<datalist id="${escapeAttr(config.pathKey)}-presets">${config.presets
                        .map((item) => `<option value="${escapeAttr(item)}"></option>`)
                        .join("")}</datalist>`
                    : ""
                }
                <span class="hint">${escapeHtml(config.localHint)}</span>
              </div>
            `
            : `
              <div class="field-grid">
                <div class="field">
                  <label>Provider</label>
                  <select data-setting-select="${escapeAttr(config.providerKey)}">
                    ${config.providers
                      .map(
                        (item) =>
                          `<option value="${escapeAttr(item.id)}" ${provider === item.id ? "selected" : ""}>${escapeHtml(item.label)}</option>`,
                      )
                      .join("")}
                  </select>
                </div>
                <div class="field">
                  <label>Model</label>
                  <input data-setting-input="${escapeAttr(config.modelKey)}" list="${escapeAttr(config.modelKey)}-presets"
                    value="${escapeAttr(settings[config.modelKey] || "")}">
                  <datalist id="${escapeAttr(config.modelKey)}-presets">
                    ${providerInfo.models.map((item) => `<option value="${escapeAttr(item)}"></option>`).join("")}
                  </datalist>
                </div>
                <div class="field">
                  <label>API key</label>
                  <div class="input-row">
                    <input type="password" data-setting-input="${escapeAttr(config.keyKey)}"
                      placeholder="${keySet ? "Saved — type to replace" : "Paste your key"}" value="" autocomplete="off">
                    ${
                      keySet
                        ? `<button class="text-button danger" type="button" data-action="clear-key" data-target="${escapeAttr(config.keyKey)}">${icon("x")}</button>`
                        : ""
                    }
                  </div>
                  <span class="hint">${keySet ? "A key is saved for this provider." : `Or leave blank and set $${escapeHtml(providerInfo.env)} in your environment.`}</span>
                </div>
                <div class="field">
                  <label>Environment variable</label>
                  <input data-setting-input="${escapeAttr(config.envKey)}" value="${escapeAttr(settings[config.envKey] || providerInfo.env)}">
                </div>
              </div>
            `
        }
      </div>
    </section>
  `;
}

function renderWifiRow(network, currentSsid) {
  const isCurrent = network.ssid === currentSsid;
  return `
    <div class="list-row ${isCurrent ? "current" : ""}">
      ${icon("wifi")}
      <div class="grow">
        <strong>${escapeHtml(network.ssid)}</strong>
        <span>${network.password ? "Password saved" : "Open network"}${isCurrent ? " · connected now" : ""}${
          network.priority ? ` · priority ${network.priority}` : ""
        }</span>
      </div>
      <button class="icon-button" type="button" title="Edit" data-action="edit-wifi" data-wifi-id="${escapeAttr(network.id)}">${icon("edit")}</button>
      <button class="icon-button" type="button" title="Remove" data-action="delete-wifi" data-wifi-id="${escapeAttr(network.id)}">${icon("trash")}</button>
    </div>
  `;
}

/* ---------------- Note menu ---------------- */

function renderMenu() {
  const root = document.getElementById("menuRoot");
  if (!state.menu) {
    root.innerHTML = "";
    return;
  }
  const note = getNote(state.menu.noteId);
  if (!note) {
    state.menu = null;
    root.innerHTML = "";
    return;
  }
  const left = Math.max(12, Math.min(state.menu.x, window.innerWidth - 260));
  const top = Math.max(12, Math.min(state.menu.y, window.innerHeight - 470));
  root.innerHTML = `
    <div class="menu" style="left:${left}px; top:${top}px;" data-menu-root>
      <div class="menu-section">
        <button class="menu-button" type="button" data-action="rename-note" data-note-id="${note.id}">${icon("edit")}<span>Rename</span></button>
        <button class="menu-button" type="button" data-action="download-pdf" data-note-id="${note.id}">${icon("download")}<span>Download PDF</span></button>
        <button class="menu-button" type="button" data-action="raw-transcript" data-note-id="${note.id}">${icon("file")}<span>View raw transcription</span></button>
        <button class="menu-button" type="button" data-action="play-audio" data-note-id="${note.id}">${icon("audio")}<span>Hear original audio</span></button>
      </div>
      <div class="menu-section">
        <span class="menu-label">Move to</span>
        <select data-action="move-note" data-note-id="${note.id}">
          ${state.data.folders
            .map(
              (folder) =>
                `<option value="${folder.id}" ${note.folder_id === folder.id ? "selected" : ""}>${escapeHtml(folder.name)}</option>`,
            )
            .join("")}
        </select>
      </div>
      <div class="menu-section">
        <span class="menu-label">Detail</span>
        ${detailOrder
          .map(
            (level) => `
              <button class="menu-button ${note.detail_level === level ? "checked" : ""}" type="button"
                data-action="set-detail" data-note-id="${note.id}" data-detail="${level}">
                ${icon(note.detail_level === level ? "check" : detailIcon(level))}<span>${detailName(level)}</span>
              </button>
            `,
          )
          .join("")}
      </div>
      <div class="menu-section">
        <button class="menu-button danger" type="button" data-action="delete-note" data-note-id="${note.id}">${icon("trash")}<span>Delete</span></button>
      </div>
    </div>
  `;
}

/* ---------------- Events ---------------- */

async function handleDocumentClick(event) {
  const actionButton = event.target.closest("[data-action]");
  if (actionButton) {
    await runAction(actionButton.dataset.action, actionButton, event);
    return;
  }

  const navButton = event.target.closest("[data-view]");
  if (navButton) {
    state.view = navButton.dataset.view;
    state.menu = null;
    render();
    return;
  }

  const folderButton = event.target.closest("[data-folder-id]");
  if (folderButton) {
    state.folderId = folderButton.dataset.folderId;
    state.view = "home";
    state.menu = null;
    render();
    return;
  }

  const noteCard = event.target.closest("[data-note-card]");
  if (noteCard) {
    state.selectedNoteId = noteCard.dataset.noteId;
    render();
    return;
  }

  if (!event.target.closest("[data-menu-root]") && state.menu) {
    state.menu = null;
    renderMenu();
  }
}

async function runAction(action, element, event) {
  try {
    if (action !== "open-menu" && !element.closest("[data-menu-root]")) {
      state.menu = null;
    }
    switch (action) {
      case "open-menu":
        event.stopPropagation();
        state.menu = { noteId: element.dataset.noteId, x: event.clientX - 230, y: event.clientY + 8 };
        return renderMenu();
      case "theme":
        return await setTheme(element.dataset.theme);
      case "sync-incoming":
        return await syncIncoming();
      case "upload-audio":
        return document.getElementById("audioUpload").click();
      case "setting-card":
        return await saveSettings({ [element.dataset.setting]: element.dataset.value });
      case "set-detail":
        return await setDetail(element.dataset.noteId, element.dataset.detail);
      case "download-pdf":
        return downloadPdf(element.dataset.noteId);
      case "raw-transcript":
        return await openRawTranscript(element.dataset.noteId);
      case "play-audio":
        return playAudio(element.dataset.noteId);
      case "delete-note":
        return await deleteNote(element.dataset.noteId);
      case "delete-folder":
        event.stopPropagation();
        return await deleteFolder(element.dataset.folderId);
      case "rename-note":
        return openRenameModal(element.dataset.noteId);
      case "reprocess":
        return await reprocessNote(element.dataset.noteId);
      case "clear-key":
        return await saveSettings({ [element.dataset.target]: "" });
      case "pick-file":
        return await pickFile(element.dataset.kind, element.dataset.target);
      case "copy":
        return await copyText(element.dataset.copy);
      case "push-config":
        return await pushConfig();
      case "scan-ble":
        return await scanForDevice();
      case "usb-import":
        return await usbImport();
      case "add-wifi":
        return openWifiModal(null);
      case "edit-wifi":
        return openWifiModal(element.dataset.wifiId);
      case "delete-wifi":
        return await deleteWifi(element.dataset.wifiId);
      case "start-recording":
        return await startRecording();
      case "pause-recording":
        return pauseRecording();
      case "stop-recording":
        return stopRecording();
      case "close-modal":
        return closeModal();
      case "confirm-rename":
        return await confirmRename(element.dataset.noteId);
      case "confirm-folder":
        return await confirmFolder();
      case "confirm-wifi":
        return await confirmWifi(element.dataset.wifiId);
      default:
        return;
    }
  } catch (error) {
    toast(error.message, "error");
  }
}

function handleInput(event) {
  if (event.target.id === "searchInput") {
    state.query = event.target.value;
    renderPage();
  }
}

async function handleChange(event) {
  const target = event.target;
  try {
    if (target.id === "audioUpload" && target.files && target.files[0]) {
      await uploadAudioFile(target.files[0]);
      target.value = "";
      return;
    }
    if (target.dataset.action === "move-note") {
      state.menu = null;
      return await moveNote(target.dataset.noteId, target.value);
    }
    if (target.dataset.action === "computer-source") {
      return await saveSettings({ computer_source: target.value }, { silent: true });
    }
    if (target.dataset.settingSelect) {
      const key = target.dataset.settingSelect;
      if (key === "theme") return await setTheme(target.value);
      return await saveSettings({ [key]: target.value });
    }
    if (target.dataset.settingToggle) {
      return await saveSettings({ [target.dataset.settingToggle]: target.checked });
    }
    if (target.dataset.settingInput) {
      const key = target.dataset.settingInput;
      let value = target.value;
      if (key === "ingest_port") value = Number(value) || 8788;
      if (target.type === "password" && !value) return; // blank means "leave the saved key alone"
      return await saveSettings({ [key]: value });
    }
  } catch (error) {
    toast(error.message, "error");
  }
}

function handleKeydown(event) {
  if (event.key === "Escape") {
    if (document.getElementById("modalRoot").children.length) return closeModal();
    if (state.menu) {
      state.menu = null;
      renderMenu();
    }
  }
  if (event.key === "Enter" && document.getElementById("modalRoot").children.length) {
    const confirmButton = document.querySelector("#modalRoot [data-action^='confirm-']");
    if (confirmButton && event.target.tagName === "INPUT") {
      event.preventDefault();
      confirmButton.click();
    }
  }
}

/* ---------------- Drag & drop ---------------- */

function handleDragStart(event) {
  const card = event.target.closest("[data-note-card]");
  if (!card) return;
  event.dataTransfer.setData("text/plain", card.dataset.noteId);
  event.dataTransfer.effectAllowed = "move";
  card.classList.add("dragging");
}

function handleDragEnd(event) {
  const card = event.target.closest("[data-note-card]");
  if (card) card.classList.remove("dragging");
  document.querySelectorAll(".drop-target").forEach((item) => item.classList.remove("drop-target"));
}

function handleDragOver(event) {
  const folder = event.target.closest("[data-folder-drop]");
  if (!folder) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = "move";
  folder.classList.add("drop-target");
}

function handleDragLeave(event) {
  const folder = event.target.closest("[data-folder-drop]");
  if (folder) folder.classList.remove("drop-target");
}

async function handleDrop(event) {
  const folder = event.target.closest("[data-folder-drop]");
  if (!folder) return;
  event.preventDefault();
  folder.classList.remove("drop-target");
  const noteId = event.dataTransfer.getData("text/plain");
  if (!noteId) return;
  try {
    await moveNote(noteId, folder.dataset.folderDrop === "all" ? "inbox" : folder.dataset.folderDrop);
  } catch (error) {
    toast(error.message, "error");
  }
}

/* ---------------- Actions ---------------- */

function commit(data) {
  state.data = data;
  state.lastSignature = signature(data);
  render();
}

async function syncIncoming() {
  state.busy = true;
  toast("Checking for new recordings…");
  try {
    const data = await request("/api/scan", { method: "POST", body: "{}" });
    if (data.imported > 0) state.selectedNoteId = data.notes[0].id;
    commit(data);
    toast(
      data.imported
        ? `${data.imported} recording${data.imported === 1 ? "" : "s"} imported`
        : "Nothing new to import",
      data.imported ? "success" : "",
    );
  } finally {
    state.busy = false;
  }
}

async function uploadAudioFile(file) {
  state.busy = true;
  toast("Importing audio…");
  try {
    const dataUrl = await blobToDataURL(file);
    const data = await request("/api/notes", {
      method: "POST",
      body: JSON.stringify({
        filename: file.name || "upload.webm",
        data: dataUrl,
        source: "computer",
        preset_id: state.data.settings.computer_preset,
        capture_mode_id: state.data.settings.computer_source,
      }),
    });
    state.selectedNoteId = data.created_note_id;
    state.view = "home";
    commit(data);
    toast("Audio imported", "success");
  } finally {
    state.busy = false;
  }
}

async function moveNote(noteId, folderId) {
  commit(await request(`/api/notes/${noteId}`, { method: "PATCH", body: JSON.stringify({ folder_id: folderId }) }));
  toast(`Moved to ${folderName(folderId)}`);
}

async function setDetail(noteId, detail) {
  state.selectedNoteId = noteId;
  const data = await request(`/api/notes/${noteId}`, { method: "PATCH", body: JSON.stringify({ detail_level: detail }) });
  commit(data);
  if ((data.processing || []).includes(noteId)) toast(`Rewriting at ${detailName(detail).toLowerCase()} detail…`);
}

async function reprocessNote(noteId) {
  const note = getNote(noteId);
  commit(
    await request(`/api/notes/${noteId}/reprocess`, {
      method: "POST",
      body: JSON.stringify({ detail_level: note ? note.detail_level : state.data.settings.default_detail }),
    }),
  );
  state.selectedNoteId = noteId;
  toast("Regenerating note…");
}

function downloadPdf(noteId) {
  window.location.href = `/api/notes/${noteId}/pdf`;
}

async function openRawTranscript(noteId) {
  const raw = await request(`/api/notes/${noteId}/raw`);
  showModal(
    raw.title || "Raw transcription",
    `
      <pre>${escapeHtml(raw.transcript || "Nothing transcribed yet.")}</pre>
      ${raw.raw_transcript ? `<hr><pre>${escapeHtml(raw.raw_transcript)}</pre>` : ""}
      ${raw.error ? `<hr><pre>${escapeHtml(raw.error)}</pre>` : ""}
    `,
  );
}

function playAudio(noteId) {
  const audio = document.getElementById("audioPlayer");
  audio.src = `/api/notes/${noteId}/audio`;
  audio.play().catch((error) => toast(error.message, "error"));
  toast("Playing original audio");
}

async function deleteNote(noteId) {
  const note = getNote(noteId);
  if (!note || !window.confirm(`Delete "${note.title}"? The original audio is deleted too.`)) return;
  const data = await request(`/api/notes/${noteId}`, { method: "DELETE" });
  state.selectedNoteId = data.notes[0] ? data.notes[0].id : null;
  commit(data);
  toast("Note deleted");
}

async function deleteFolder(folderId) {
  if (!window.confirm(`Delete this folder? Its notes move back to Inbox.`)) return;
  const data = await request(`/api/folders/${folderId}`, { method: "DELETE" });
  if (state.folderId === folderId) state.folderId = "all";
  commit(data);
  toast("Folder deleted");
}

async function saveSettings(patch, options = {}) {
  const data = await request("/api/settings", { method: "PATCH", body: JSON.stringify(patch) });
  commit(data);
  if (!options.silent) toast("Saved", "success");
  if (data.devicePush) {
    toast(data.devicePush.ok ? "Device updated over Bluetooth" : `Device not updated: ${data.devicePush.error}`,
      data.devicePush.ok ? "success" : "error");
  }
}

async function pickFile(kind, targetKey) {
  const result = await request("/api/pick-file", { method: "POST", body: JSON.stringify({ kind }) });
  if (!result.path) {
    toast("No file chosen. You can also paste the path directly into the box.");
    return;
  }
  await saveSettings({ [targetKey]: result.path });
}

async function copyText(value) {
  if (!value) return;
  try {
    await navigator.clipboard.writeText(value);
    toast("Copied", "success");
  } catch (error) {
    window.prompt("Copy this", value);
  }
}

async function pushConfig() {
  state.busy = true;
  toast("Talking to the device over Bluetooth…");
  try {
    const data = await request("/api/device/push-config", { method: "POST", body: "{}" });
    commit(data);
    toast(data.ok ? "Device updated" : `Could not reach the device: ${data.result.error}`, data.ok ? "success" : "error");
  } finally {
    state.busy = false;
  }
}

async function scanForDevice() {
  state.busy = true;
  toast("Scanning for NotPlaud devices…");
  try {
    const result = await request("/api/device/scan");
    const devices = result.devices || [];
    showModal(
      "Nearby devices",
      devices.length
        ? `<div class="list-rows">${devices
            .map(
              (device) =>
                `<div class="list-row"><div class="grow"><strong>${escapeHtml(device.name || device.error || "Unknown")}</strong>
                 <span>${escapeHtml(device.address || "")}</span></div></div>`,
            )
            .join("")}</div>`
        : `<pre>No NotPlaud devices found. Make sure the device is powered on and in range.</pre>`,
    );
  } finally {
    state.busy = false;
  }
}

async function usbImport() {
  state.busy = true;
  toast("Copying from the device…");
  try {
    const data = await request("/api/usb/import", { method: "POST", body: "{}" });
    commit(data);
    toast(`${data.imported} recording${data.imported === 1 ? "" : "s"} imported`, "success");
  } finally {
    state.busy = false;
  }
}

async function deleteWifi(networkId) {
  commit(await request(`/api/wifi-networks/${networkId}`, { method: "DELETE" }));
  toast("Network removed");
}

/* ---------------- Modals ---------------- */

function showModal(title, body, actions = "") {
  document.getElementById("modalRoot").innerHTML = `
    <div class="modal-backdrop" data-action="close-modal">
      <div class="modal" role="dialog" aria-modal="true" aria-label="${escapeAttr(title)}">
        <div class="modal-head">
          <h2>${escapeHtml(title)}</h2>
          <button class="icon-button" type="button" title="Close" aria-label="Close" data-action="close-modal">${icon("x")}</button>
        </div>
        <div class="modal-body">${body}</div>
        ${actions ? `<div class="modal-actions">${actions}</div>` : ""}
      </div>
    </div>
  `;
  const modal = document.querySelector("#modalRoot .modal");
  if (modal) modal.addEventListener("click", (event) => event.stopPropagation());
  const firstInput = document.querySelector("#modalRoot input");
  if (firstInput) firstInput.focus();
}

function closeModal() {
  document.getElementById("modalRoot").innerHTML = "";
}

function openRenameModal(noteId) {
  const note = getNote(noteId);
  if (!note) return;
  showModal(
    "Rename note",
    `<div class="field"><label for="renameInput">Title</label><input id="renameInput" value="${escapeAttr(note.title)}"></div>`,
    `<button class="text-button" type="button" data-action="close-modal">Cancel</button>
     <button class="text-button primary" type="button" data-action="confirm-rename" data-note-id="${note.id}">${icon("check")}<span>Save</span></button>`,
  );
}

async function confirmRename(noteId) {
  const title = document.getElementById("renameInput").value.trim();
  if (!title) return toast("Title is required", "error");
  const data = await request(`/api/notes/${noteId}/rename`, { method: "POST", body: JSON.stringify({ title }) });
  closeModal();
  commit(data);
}

function openFolderModal() {
  showModal(
    "New folder",
    `<div class="field"><label for="folderInput">Folder name</label><input id="folderInput" placeholder="e.g. Physics 201"></div>`,
    `<button class="text-button" type="button" data-action="close-modal">Cancel</button>
     <button class="text-button primary" type="button" data-action="confirm-folder">${icon("check")}<span>Create</span></button>`,
  );
}

async function confirmFolder() {
  const name = document.getElementById("folderInput").value.trim();
  if (!name) return toast("Folder name is required", "error");
  const data = await request("/api/folders", { method: "POST", body: JSON.stringify({ name }) });
  closeModal();
  commit(data);
  toast("Folder created", "success");
}

function openWifiModal(networkId) {
  const network = (state.data.settings.wifi_networks || []).find((item) => item.id === networkId);
  const current = state.data.currentWifi || "";
  showModal(
    network ? "Edit network" : "Add WiFi network",
    `
      <div class="field-grid">
        <div class="field">
          <label for="wifiSsid">Network name (SSID)</label>
          <input id="wifiSsid" value="${escapeAttr(network ? network.ssid : current)}" placeholder="My WiFi">
        </div>
        <div class="field">
          <label for="wifiPassword">Password</label>
          <input id="wifiPassword" type="password" autocomplete="off"
            placeholder="${network && network.password ? "Saved — type to replace" : "Leave blank for an open network"}">
        </div>
        <div class="field">
          <label for="wifiPriority">Priority</label>
          <input id="wifiPriority" type="number" value="${escapeAttr(String(network ? network.priority || 0 : 0))}">
          <span class="hint">Higher wins when more than one saved network is in range.</span>
        </div>
      </div>
    `,
    `<button class="text-button" type="button" data-action="close-modal">Cancel</button>
     <button class="text-button primary" type="button" data-action="confirm-wifi" ${
       network ? `data-wifi-id="${escapeAttr(network.id)}"` : ""
     }>${icon("check")}<span>Save</span></button>`,
  );
}

async function confirmWifi(networkId) {
  const ssid = document.getElementById("wifiSsid").value.trim();
  const password = document.getElementById("wifiPassword").value;
  const priority = Number(document.getElementById("wifiPriority").value) || 0;
  if (!ssid) return toast("Network name is required", "error");

  const body = { ssid, priority };
  if (password) body.password = password;

  const data = networkId
    ? await request(`/api/wifi-networks/${networkId}`, { method: "PATCH", body: JSON.stringify(body) })
    : await request("/api/wifi-networks", { method: "POST", body: JSON.stringify({ ...body, password: password || "" }) });

  closeModal();
  commit(data);
  toast("Network saved", "success");
  if (data.devicePush) {
    toast(data.devicePush.ok ? "Device updated over Bluetooth" : `Device not updated: ${data.devicePush.error}`,
      data.devicePush.ok ? "success" : "error");
  }
}

/* ---------------- Recording ---------------- */

async function startRecording() {
  if (!navigator.mediaDevices || !window.MediaRecorder) {
    throw new Error("This build cannot record audio. Use the device instead.");
  }
  const sourceId = state.data.settings.computer_source;
  const streams = [];

  try {
    if (["system-and-mic", "system-only", "screen-audio"].includes(sourceId)) {
      streams.push(await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true }));
    }
    if (["system-and-mic", "mic-only"].includes(sourceId)) {
      streams.push(await navigator.mediaDevices.getUserMedia({ audio: true }));
    }
  } catch (error) {
    streams.forEach((stream) => stream.getTracks().forEach((track) => track.stop()));
    throw new Error(`Could not start capture: ${error.message}`);
  }

  const mix = await mixAudioStreams(streams);
  const mimeType = bestRecordingMimeType();
  const recorder = new MediaRecorder(mix.stream, mimeType ? { mimeType } : undefined);
  const chunks = [];
  recorder.addEventListener("dataavailable", (event) => {
    if (event.data && event.data.size > 0) chunks.push(event.data);
  });
  recorder.addEventListener("stop", () => finishRecording());

  // If the user stops sharing from the OS bar, treat it as pressing Stop.
  streams.forEach((stream) =>
    stream.getTracks().forEach((track) => {
      track.addEventListener("ended", () => {
        if (state.recording && state.recording.recorder.state !== "inactive") stopRecording();
      });
    }),
  );

  state.recording = {
    recorder,
    chunks,
    streams,
    mixedStream: mix.stream,
    audioContext: mix.audioContext,
    analyser: mix.analyser,
    startAt: Date.now(),
    paused: false,
    mimeType: mimeType || "audio/webm",
    timer: window.setInterval(updateTimer, 500),
    raf: null,
  };
  recorder.start(1000);
  render();
  drawLiveMeter();
  toast("Recording started");
}

function pauseRecording() {
  const recording = state.recording;
  if (!recording) return;
  if (recording.recorder.state === "recording") {
    recording.recorder.pause();
    recording.paused = true;
  } else if (recording.recorder.state === "paused") {
    recording.recorder.resume();
    recording.paused = false;
  }
  render();
  if (!state.recording.paused) drawLiveMeter();
}

function stopRecording() {
  const recording = state.recording;
  if (!recording) return;
  if (recording.recorder.state !== "inactive") recording.recorder.stop();
}

async function finishRecording() {
  const recording = state.recording;
  if (!recording) return;
  cleanupRecording(recording, false);
  const extension = recording.mimeType.includes("ogg") ? "ogg" : recording.mimeType.includes("mp4") ? "m4a" : "webm";
  const blob = new Blob(recording.chunks, { type: recording.mimeType });
  state.recording = null;
  render();

  if (!blob.size) {
    toast("Recording was empty — no audio track was shared", "error");
    return;
  }

  state.busy = true;
  toast("Saving recording…");
  try {
    const dataUrl = await blobToDataURL(blob);
    const data = await request("/api/notes", {
      method: "POST",
      body: JSON.stringify({
        filename: `call-${Date.now()}.${extension}`,
        data: dataUrl,
        source: "computer",
        preset_id: state.data.settings.computer_preset,
        capture_mode_id: state.data.settings.computer_source,
      }),
    });
    state.view = "home";
    state.selectedNoteId = data.created_note_id;
    commit(data);
    toast("Recording saved — writing the note now", "success");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    state.busy = false;
  }
}

async function mixAudioStreams(streams) {
  const audioTracks = streams.flatMap((stream) => stream.getAudioTracks());
  if (!audioTracks.length) {
    streams.forEach((stream) => stream.getTracks().forEach((track) => track.stop()));
    throw new Error('No audio track was shared. Re-run and tick "Share audio" in the picker.');
  }
  const audioContext = new AudioContext();
  const destination = audioContext.createMediaStreamDestination();
  const analyser = audioContext.createAnalyser();
  analyser.fftSize = 256;

  audioTracks.forEach((track) => {
    const source = audioContext.createMediaStreamSource(new MediaStream([track]));
    source.connect(destination);
    source.connect(analyser);
  });

  return { stream: destination.stream, audioContext, analyser };
}

function cleanupRecording(recording, clearState = true) {
  window.clearInterval(recording.timer);
  if (recording.raf) cancelAnimationFrame(recording.raf);
  recording.streams.forEach((stream) => stream.getTracks().forEach((track) => track.stop()));
  recording.mixedStream.getTracks().forEach((track) => track.stop());
  if (recording.audioContext && recording.audioContext.state !== "closed") {
    recording.audioContext.close();
  }
  if (clearState) state.recording = null;
}

function updateTimer() {
  const timer = document.getElementById("recordTimer");
  if (timer) timer.textContent = elapsedLabel();
}

function elapsedLabel() {
  if (!state.recording) return "00:00";
  const seconds = Math.max(0, Math.floor((Date.now() - state.recording.startAt) / 1000));
  const minutes = Math.floor(seconds / 60)
    .toString()
    .padStart(2, "0");
  return `${minutes}:${(seconds % 60).toString().padStart(2, "0")}`;
}

function meterColors() {
  const styles = getComputedStyle(document.documentElement);
  return {
    bg: styles.getPropertyValue("--surface-2").trim() || "#182120",
    accent: styles.getPropertyValue("--accent").trim() || "#2fb8a4",
    info: styles.getPropertyValue("--info").trim() || "#6f8dff",
    warn: styles.getPropertyValue("--warn").trim() || "#e0a33a",
  };
}

function drawIdleMeter() {
  const canvas = document.getElementById("meterCanvas");
  if (!canvas || state.recording) return;
  const ctx = canvas.getContext("2d");
  const colors = meterColors();
  ctx.fillStyle = colors.bg;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  for (let i = 0; i < 42; i += 1) {
    const barHeight = 16 + ((i * 17) % 70);
    ctx.fillStyle = i % 5 === 0 ? colors.warn : i % 3 === 0 ? colors.info : colors.accent;
    ctx.globalAlpha = 0.22;
    ctx.fillRect(24 + i * 20, (canvas.height - barHeight) / 2, 8, barHeight);
  }
  ctx.globalAlpha = 1;
}

function drawLiveMeter() {
  const recording = state.recording;
  const canvas = document.getElementById("meterCanvas");
  if (!recording || !canvas) return;
  const ctx = canvas.getContext("2d");
  const data = new Uint8Array(recording.analyser.frequencyBinCount);
  const colors = meterColors();

  const draw = () => {
    if (!state.recording || !document.getElementById("meterCanvas")) return;
    recording.analyser.getByteFrequencyData(data);
    const { width, height } = canvas;
    ctx.fillStyle = colors.bg;
    ctx.fillRect(0, 0, width, height);
    const bars = 48;
    const gap = 8;
    const barWidth = (width - gap * (bars + 1)) / bars;
    for (let i = 0; i < bars; i += 1) {
      const value = recording.paused ? 0 : data[Math.floor((i / bars) * data.length)] || 0;
      const barHeight = Math.max(8, (value / 255) * (height - 34));
      ctx.fillStyle = i % 6 === 0 ? colors.warn : i % 4 === 0 ? colors.info : colors.accent;
      ctx.globalAlpha = recording.paused ? 0.3 : 1;
      ctx.fillRect(gap + i * (barWidth + gap), (height - barHeight) / 2, barWidth, barHeight);
    }
    ctx.globalAlpha = 1;
    recording.raf = requestAnimationFrame(draw);
  };
  draw();
}

function bestRecordingMimeType() {
  const types = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];
  return types.find((type) => MediaRecorder.isTypeSupported(type)) || "";
}

function blobToDataURL(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}

/* ---------------- Helpers ---------------- */

function toast(message, type = "") {
  const root = document.getElementById("toastRoot");
  const item = document.createElement("div");
  item.className = `toast ${type}`;
  item.textContent = message;
  root.appendChild(item);
  window.setTimeout(() => item.remove(), 3600);
}

function filteredNotes() {
  const query = state.query.trim().toLowerCase();
  return state.data.notes.filter((note) => {
    const folderMatch = state.folderId === "all" || note.folder_id === state.folderId;
    const haystack = `${note.title} ${note.summary} ${note.transcript}`.toLowerCase();
    return folderMatch && (!query || haystack.includes(query));
  });
}

function getNote(noteId) {
  return state.data.notes.find((note) => note.id === noteId);
}

function folderName(folderId) {
  if (folderId === "all") return "All Notes";
  const folder = state.data.folders.find((item) => item.id === folderId);
  return folder ? folder.name : "";
}

function presetName(presetId) {
  const preset = state.data.presets.find((item) => item.id === presetId);
  return preset ? preset.name : "General Notes";
}

function detailName(level) {
  const card = (state.data.detailLevels || []).find((item) => item.id === level);
  if (card) return card.name;
  return level ? level.charAt(0).toUpperCase() + level.slice(1) : "Medium";
}

function statusLabel(status) {
  if (status === "needs_ai") return "needs setup";
  if (status === "processed") return "ready";
  return status || "queued";
}

function summaryPreview(note) {
  const raw = (note.summary || note.transcript || "").replace(/^#+\s*/gm, "").replace(/\s+/g, " ").trim();
  return raw || "Audio saved. The note is still being written.";
}

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  return `${(bytes / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`;
}

function renderMarkdown(markdown) {
  if (!markdown.trim()) return "<p>Audio saved. The note is still being written.</p>";
  const lines = markdown.split(/\r?\n/);
  let html = "";
  let inList = false;
  const closeList = () => {
    if (inList) {
      html += "</ul>";
      inList = false;
    }
  };
  lines.forEach((line) => {
    const trimmed = line.trim();
    if (!trimmed) return closeList();
    if (trimmed.startsWith("#")) {
      closeList();
      html += `<h3>${escapeHtml(trimmed.replace(/^#+\s*/, ""))}</h3>`;
      return;
    }
    if (trimmed.startsWith("-") || trimmed.startsWith("*")) {
      if (!inList) {
        html += "<ul>";
        inList = true;
      }
      html += `<li>${inlineMarkdown(trimmed.replace(/^[-*]\s*/, ""))}</li>`;
      return;
    }
    closeList();
    html += `<p>${inlineMarkdown(trimmed)}</p>`;
  });
  closeList();
  return html;
}

function inlineMarkdown(text) {
  return escapeHtml(text).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
}

function formatDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function presetIcon(id) {
  if (id === "online-class") return "book";
  if (id === "interview") return "chat";
  if (id === "voice-memo") return "audio";
  if (id === "conference") return "users";
  return "file";
}

function modeIcon(id) {
  if (id === "wide-spectrum") return "waves";
  if (id === "voice-isolation") return "focus";
  return "device";
}

function detailIcon(id) {
  if (id === "low") return "minus";
  if (id === "high") return "layers";
  if (id === "ultra") return "spark";
  return "file";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value);
}

function icon(name) {
  const paths = {
    home: '<path d="M3 10.5 12 3l9 7.5"></path><path d="M5 10v10h5v-6h4v6h5V10"></path>',
    device: '<rect x="6" y="3" width="12" height="18" rx="3"></rect><path d="M10 7h4"></path><path d="M11 17h2"></path>',
    mic: '<path d="M12 3a3 3 0 0 0-3 3v5a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3Z"></path><path d="M5 10v1a7 7 0 0 0 14 0v-1"></path><path d="M12 18v3"></path>',
    settings:
      '<path d="M12 15.5A3.5 3.5 0 1 0 12 8a3.5 3.5 0 0 0 0 7.5Z"></path><path d="M19 12a7 7 0 0 0-.1-1.1l2-1.5-2-3.4-2.4 1a7.5 7.5 0 0 0-1.9-1.1L14.2 3h-4.4l-.4 2.9A7.5 7.5 0 0 0 7.5 7l-2.4-1-2 3.4 2 1.5A7 7 0 0 0 5 12c0 .4 0 .8.1 1.1l-2 1.5 2 3.4 2.4-1a7.5 7.5 0 0 0 1.9 1.1l.4 2.9h4.4l.4-2.9a7.5 7.5 0 0 0 1.9-1.1l2.4 1 2-3.4-2-1.5c.1-.3.1-.7.1-1.1Z"></path>',
    plus: '<path d="M12 5v14"></path><path d="M5 12h14"></path>',
    minus: '<path d="M5 12h14"></path>',
    folder: '<path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"></path>',
    layers: '<path d="m12 3 9 5-9 5-9-5 9-5Z"></path><path d="m3 12 9 5 9-5"></path><path d="m3 16 9 5 9-5"></path>',
    search: '<circle cx="11" cy="11" r="7"></circle><path d="m20 20-3.5-3.5"></path>',
    upload: '<path d="M12 16V4"></path><path d="m7 9 5-5 5 5"></path><path d="M5 20h14"></path>',
    sync: '<path d="M20 6v5h-5"></path><path d="M4 18v-5h5"></path><path d="M19 11a7 7 0 0 0-12-4l-3 3"></path><path d="M5 13a7 7 0 0 0 12 4l3-3"></path>',
    more: '<circle cx="5" cy="12" r="1.5"></circle><circle cx="12" cy="12" r="1.5"></circle><circle cx="19" cy="12" r="1.5"></circle>',
    file: '<path d="M6 3h8l4 4v14H6Z"></path><path d="M14 3v5h5"></path><path d="M9 13h6"></path><path d="M9 17h6"></path>',
    download: '<path d="M12 3v12"></path><path d="m7 10 5 5 5-5"></path><path d="M5 21h14"></path>',
    audio: '<path d="M4 14V10"></path><path d="M8 18V6"></path><path d="M12 21V3"></path><path d="M16 18V6"></path><path d="M20 14V10"></path>',
    edit: '<path d="M4 20h4l11-11a2.8 2.8 0 0 0-4-4L4 16v4Z"></path><path d="m13.5 6.5 4 4"></path>',
    trash: '<path d="M4 7h16"></path><path d="M9 7V4h6v3"></path><path d="M6 7l1 14h10l1-14"></path><path d="M10 11v6"></path><path d="M14 11v6"></path>',
    spark: '<path d="M12 3 9.5 9.5 3 12l6.5 2.5L12 21l2.5-6.5L21 12l-6.5-2.5L12 3Z"></path>',
    check: '<path d="m5 13 4 4L19 7"></path>',
    x: '<path d="M6 6l12 12"></path><path d="M18 6 6 18"></path>',
    copy: '<rect x="8" y="8" width="12" height="12" rx="2"></rect><path d="M4 16V6a2 2 0 0 1 2-2h10"></path>',
    record: '<circle cx="12" cy="12" r="7"></circle>',
    pause: '<path d="M9 5v14"></path><path d="M15 5v14"></path>',
    play: '<path d="m8 5 11 7-11 7Z"></path>',
    stop: '<rect x="7" y="7" width="10" height="10"></rect>',
    book: '<path d="M4 5a3 3 0 0 1 3-3h13v18H7a3 3 0 0 0-3 3V5Z"></path><path d="M8 6h8"></path><path d="M8 10h8"></path>',
    chat: '<path d="M4 5h16v11H8l-4 4Z"></path><path d="M8 9h8"></path><path d="M8 13h5"></path>',
    users: '<path d="M16 21v-2a4 4 0 0 0-8 0v2"></path><circle cx="12" cy="7" r="4"></circle><path d="M22 21v-2a4 4 0 0 0-3-3.8"></path><path d="M19 3.5a4 4 0 0 1 0 7"></path>',
    waves: '<path d="M3 12c2-3 4-3 6 0s4 3 6 0 4-3 6 0"></path><path d="M3 17c2-3 4-3 6 0s4 3 6 0 4-3 6 0"></path><path d="M3 7c2-3 4-3 6 0s4 3 6 0 4-3 6 0"></path>',
    focus: '<circle cx="12" cy="12" r="3"></circle><path d="M12 2v4"></path><path d="M12 18v4"></path><path d="M2 12h4"></path><path d="M18 12h4"></path>',
    sun: '<circle cx="12" cy="12" r="4"></circle><path d="M12 2v2"></path><path d="M12 20v2"></path><path d="M4.9 4.9l1.4 1.4"></path><path d="M17.7 17.7l1.4 1.4"></path><path d="M2 12h2"></path><path d="M20 12h2"></path><path d="M4.9 19.1l1.4-1.4"></path><path d="M17.7 6.3l1.4-1.4"></path>',
    moon: '<path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5Z"></path>',
    wifi: '<path d="M2 8.5a16 16 0 0 1 20 0"></path><path d="M5 12a11 11 0 0 1 14 0"></path><path d="M8.5 15.5a6 6 0 0 1 7 0"></path><path d="M12 19h.01"></path>',
    bluetooth: '<path d="m7 7 10 10-5 4V3l5 4L7 17"></path>',
    usb: '<circle cx="12" cy="20" r="1.6"></circle><path d="M12 18V5"></path><path d="m9 8 3-5 3 5"></path><path d="M12 12h4V9"></path><path d="M12 15H8v-3"></path>',
  };
  return `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.file}</svg>`;
}
