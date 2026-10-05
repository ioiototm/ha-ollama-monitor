/*
 * Ollama Monitor card — ships with the ollama_monitor integration (CC0).
 *
 *   type: custom:ollama-monitor-card
 *   title: Local AI            # optional
 *   hosts: [Desktop, Pi]       # optional, device names or ids; default = all
 *   default_keep_alive: 30m    # optional, preselected in the load row
 *   show_controls: true        # load / unload buttons
 *   show_installed: true       # collapsible list of pulled models
 */

const CARD_VERSION = "0.1.0";
const DOMAIN = "ollama_monitor";

const KEEP_ALIVE_OPTIONS = [
  { value: "default", label: "Server default" },
  { value: "5m", label: "5 minutes" },
  { value: "30m", label: "30 minutes" },
  { value: "1h", label: "1 hour" },
  { value: "4h", label: "4 hours" },
  { value: "24h", label: "24 hours" },
  { value: "-1", label: "Until unloaded" },
  { value: "custom", label: "Custom…" },
];

const ICON = {
  eject: "M12,5L5.33,15H18.67M5,17H19V19H5V17Z",
  pin: "M16,12V4H17V2H7V4H8V12L6,14V16H11.2V22H12.8V16H18V14L16,12Z",
  chevron: "M8.59,16.58L13.17,12L8.59,7.41L10,6L16,12L10,18L8.59,16.58Z",
};

// translation_key → role. Entity-id suffixes are a fallback for old frontends.
const ROLES = {
  loaded_model: "_loaded_model",
  installed_models: "_installed_models",
  online: "_online",
  version: "_ollama_version",
  vram_used: "_vram_used",
  memory_used: "_model_memory_used",
};

const esc = (v) =>
  String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const svg = (path, cls = "") =>
  `<svg class="ico ${cls}" viewBox="0 0 24 24" aria-hidden="true"><path d="${path}"/></svg>`;

function formatBytes(bytes) {
  const n = Number(bytes) || 0;
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)} GB`;
  if (n >= 1e6) return `${Math.round(n / 1e6)} MB`;
  if (n > 0) return `${Math.max(1, Math.round(n / 1e3))} kB`;
  return "0 GB";
}

function formatContext(n) {
  if (!n) return "";
  return n >= 1024 ? `${Math.round(n / 1024)}K context` : `${n} context`;
}

function formatRemaining(ms) {
  if (ms <= 0) return "Unloading…";
  const s = Math.round(ms / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `Unloads in ${h}h ${m}m`;
  if (m > 0) return `Unloads in ${m}m ${String(sec).padStart(2, "0")}s`;
  return `Unloads in ${sec}s`;
}

function findHosts(hass) {
  const byDevice = new Map();
  for (const [entityId, entry] of Object.entries(hass.entities || {})) {
    if (entry.platform !== DOMAIN || !entry.device_id) continue;
    let role = entry.translation_key;
    if (!ROLES[role]) {
      role = Object.keys(ROLES).find((r) => entityId.endsWith(ROLES[r]));
    }
    if (!role) continue;
    if (!byDevice.has(entry.device_id)) byDevice.set(entry.device_id, {});
    byDevice.get(entry.device_id)[role] = entityId;
  }
  return [...byDevice.entries()]
    .map(([deviceId, entities]) => {
      const device = (hass.devices || {})[deviceId] || {};
      return { deviceId, name: device.name_by_user || device.name || deviceId, entities };
    })
    .sort((a, b) => {
      // Online hosts first, then by name.
      const up = (h) => (hass.states[h.entities.online]?.state === "on" ? 0 : 1);
      return up(a) - up(b) || a.name.localeCompare(b.name);
    });
}

class OllamaMonitorCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._ui = {}; // per-host UI state that must survive re-renders
    this._fuse = {}; // "host|model" → {expires, total}
    this._lastKey = "";
  }

  static getConfigElement() {
    return document.createElement("ollama-monitor-card-editor");
  }

  static getStubConfig() {
    return {};
  }

  setConfig(config) {
    this._config = {
      show_controls: true,
      show_installed: true,
      default_keep_alive: "default",
      ...(config || {}),
    };
    this._lastKey = "";
    if (this._hass) this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return 2 + 3 * (this._hosts?.length || 1);
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: "auto" };
  }

  connectedCallback() {
    this._timer = setInterval(() => this._tick(), 1000);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
  }

  _selectedHosts() {
    const all = findHosts(this._hass);
    const wanted = this._config.hosts;
    if (!wanted || !wanted.length) return all;
    const want = wanted.map((w) => String(w).toLowerCase());
    return want
      .map((w) => all.find((h) => h.deviceId.toLowerCase() === w || h.name.toLowerCase() === w))
      .filter(Boolean);
  }

  _uiFor(host) {
    if (!this._ui[host.deviceId]) {
      this._ui[host.deviceId] = {
        model: "",
        keepAlive: String(this._config.default_keep_alive || "default"),
        custom: "",
        loading: false,
        unloading: new Set(),
        error: "",
        installedOpen: false,
      };
    }
    return this._ui[host.deviceId];
  }

  _render() {
    if (!this._config || !this._hass) return;
    const hosts = this._selectedHosts();
    this._hosts = hosts;
    // Only rebuild when something we show actually changed.
    const key = JSON.stringify([
      this._config,
      hosts.map((h) => [h.name, Object.values(h.entities).map((e) => this._hass.states[e]?.last_updated)]),
      Object.values(this._ui).map((u) => [u.loading, [...u.unloading], u.error]),
    ]);
    if (key === this._lastKey) return;
    this._lastKey = key;

    // Remember focus so a background refresh doesn't yank it away.
    const active = this.shadowRoot.activeElement;
    const focusId = active?.dataset?.focus;

    const body = hosts.length
      ? hosts.map((h) => this._renderHost(h)).join("")
      : `<p class="empty">No Ollama hosts yet. Add one under Settings, Devices &amp; services, Ollama Monitor.</p>`;

    this.shadowRoot.innerHTML = `
      <style>${STYLE}</style>
      <ha-card>
        ${this._config.title ? `<h1 class="title">${esc(this._config.title)}</h1>` : ""}
        ${body}
      </ha-card>`;

    this._bind();
    this._tick();
    if (focusId) this.shadowRoot.querySelector(`[data-focus="${focusId}"]`)?.focus();
  }

  _renderHost(host) {
    const st = (role) => this._hass.states[host.entities[role]];
    const online = st("online");
    const isOnline = online?.state === "on";
    const loadedState = st("loaded_model");
    const installedState = st("installed_models");
    const version = st("version")?.state;
    const loaded = (isOnline && loadedState?.attributes?.models) || [];
    const installed = (isOnline && installedState?.attributes?.models) || [];
    const ui = this._uiFor(host);
    const id = esc(host.deviceId);

    const status = isOnline
      ? `<span class="status on"><span class="dot"></span>Online${version && version !== "unavailable" ? `, v${esc(version)}` : ""}</span>`
      : `<span class="status off"><span class="dot"></span>${online ? "Offline" : "Unknown"}</span>`;

    let main;
    if (!isOnline) {
      const url = online?.attributes?.url;
      main = `<p class="note">Can't reach ${url ? `<span class="url">${esc(url)}</span>` : "this host"}. Loaded models will show up here when it's back.</p>`;
    } else if (!loaded.length) {
      main = `<p class="note idle">Nothing loaded${this._config.show_controls ? ", pick a model below to warm one up" : ""}.</p>`;
    } else {
      main = `<ul class="models">${loaded.map((m) => this._renderModel(host, m, ui)).join("")}</ul>`;
    }

    const unloadAll =
      isOnline && this._config.show_controls && loaded.length > 1
        ? `<button class="text-btn" data-act="unload-all" data-host="${id}" ${ui.unloading.size ? "disabled" : ""}>Unload all</button>`
        : "";

    return `
      <section class="host ${isOnline ? "" : "is-off"}" data-host="${id}">
        <header>
          <h2>${esc(host.name)}</h2>
          ${status}
          ${unloadAll}
        </header>
        ${main}
        ${isOnline && this._config.show_controls ? this._renderControls(host, loaded, installed, ui) : ""}
        ${isOnline && this._config.show_installed && installed.length ? this._renderInstalled(host, installed, loaded, ui) : ""}
      </section>`;
  }

  _renderModel(host, m, ui) {
    const fuseKey = `${host.deviceId}|${m.name}`;
    const busy = ui.unloading.has(m.name);
    const meta = [m.parameter_size, m.quantization, formatContext(m.context_length)].filter(Boolean).join(", ");
    const gpu = Number(m.gpu_percent ?? 100);
    const split =
      gpu >= 100
        ? `<span class="proc gpu">On GPU</span>`
        : gpu <= 0
          ? `<span class="proc cpu">CPU only</span>`
          : `<span class="proc mixed" title="${esc(m.processor)}">${100 - gpu}% on CPU</span>`;

    let fuse;
    if (m.pinned) {
      fuse = `<div class="fuse pinned"><span class="burn" style="width:100%"></span></div>
              <span class="when">${svg(ICON.pin, "pin")}Pinned until unloaded</span>`;
    } else if (m.expires_at) {
      const expires = Date.parse(m.expires_at);
      const prev = this._fuse[fuseKey];
      if (!prev || prev.expires !== expires) {
        // We only ever see when a model will unload, not how long it was asked to stay.
        // When the deadline moves while we watch, the model was just used, so what's left
        // now is (nearly) the whole window. On first sight we can't know, so assume at
        // least Ollama's 5 minute default rather than drawing a full bar for 40 s left.
        const left = Math.max(1000, expires - Date.now());
        this._fuse[fuseKey] = { expires, total: prev ? left : Math.max(left, 300000) };
      }
      fuse = `<div class="fuse"><span class="burn" data-fuse="${esc(fuseKey)}"></span></div>
              <span class="when" data-when="${esc(fuseKey)}"></span>`;
    } else {
      fuse = `<div class="fuse"><span class="burn" style="width:100%"></span></div><span class="when">In use</span>`;
    }

    const unload = this._config.show_controls
      ? `<button class="icon-btn" data-act="unload" data-host="${esc(host.deviceId)}" data-model="${esc(m.name)}"
           data-focus="unload-${esc(fuseKey)}" aria-label="Unload ${esc(m.name)}" title="Unload ${esc(m.name)}" ${busy ? "disabled" : ""}>
           ${busy ? `<span class="spinner"></span>` : svg(ICON.eject)}
         </button>`
      : "";

    return `
      <li class="model ${busy ? "busy" : ""}">
        <div class="row1">
          <span class="name">${esc(m.name)}</span>
          <span class="size">${formatBytes(m.size)}</span>
          ${unload}
        </div>
        <div class="row2">
          <span class="meta">${esc(meta)}</span>
          ${split}
        </div>
        <div class="row3">${fuse}</div>
      </li>`;
  }

  _renderControls(host, loaded, installed, ui) {
    const id = esc(host.deviceId);
    const loadedNames = new Set(loaded.map((m) => m.name));
    const choices = installed.filter((m) => !m.remote);
    if (!choices.length) return "";
    if (!ui.model || !choices.some((m) => m.name === ui.model)) {
      ui.model = (choices.find((m) => !loadedNames.has(m.name)) || choices[0]).name;
    }
    const opts = choices
      .map(
        (m) =>
          `<option value="${esc(m.name)}" ${m.name === ui.model ? "selected" : ""}>${esc(m.name)}${
            loadedNames.has(m.name) ? " (loaded)" : ""
          }</option>`,
      )
      .join("");
    const kaKnown = KEEP_ALIVE_OPTIONS.some((o) => o.value === ui.keepAlive);
    if (!kaKnown) {
      ui.custom = ui.keepAlive;
      ui.keepAlive = "custom";
    }
    const kaOpts = KEEP_ALIVE_OPTIONS.map(
      (o) => `<option value="${o.value}" ${o.value === ui.keepAlive ? "selected" : ""}>${o.label}</option>`,
    ).join("");
    const reload = loadedNames.has(ui.model);

    return `
      <div class="controls">
        <label class="field grow">
          <span>Model</span>
          <select data-act="model" data-host="${id}" data-focus="model-${id}" ${ui.loading ? "disabled" : ""}>${opts}</select>
        </label>
        <label class="field">
          <span>Keep loaded for</span>
          <select data-act="keep" data-host="${id}" data-focus="keep-${id}" ${ui.loading ? "disabled" : ""}>${kaOpts}</select>
        </label>
        ${
          ui.keepAlive === "custom"
            ? `<label class="field narrow"><span>Duration</span>
                 <input data-act="custom" data-host="${id}" data-focus="custom-${id}" value="${esc(ui.custom)}"
                   placeholder="1h30m" spellcheck="false" ${ui.loading ? "disabled" : ""}></label>`
            : ""
        }
        <button class="primary" data-act="load" data-host="${id}" data-focus="load-${id}" ${ui.loading ? "disabled" : ""}>
          ${ui.loading ? `<span class="spinner"></span>Loading…` : reload ? "Extend" : "Load"}
        </button>
      </div>
      ${ui.error ? `<p class="error" role="alert">${esc(ui.error)}</p>` : ""}`;
  }

  _renderInstalled(host, installed, loaded, ui) {
    const loadedNames = new Set(loaded.map((m) => m.name));
    const total = installed.reduce((a, m) => a + (Number(m.size) || 0), 0);
    const rows = installed
      .map(
        (m) => `
        <li>
          <span class="name">${esc(m.name)}${loadedNames.has(m.name) ? `<span class="tag">loaded</span>` : ""}${
            m.remote ? `<span class="tag">cloud</span>` : ""
          }</span>
          <span class="meta">${esc([m.parameter_size, m.quantization].filter(Boolean).join(", "))}</span>
          <span class="size">${m.remote ? "" : formatBytes(m.size)}</span>
        </li>`,
      )
      .join("");
    return `
      <details class="installed" data-host="${esc(host.deviceId)}" ${ui.installedOpen ? "open" : ""}>
        <summary>${svg(ICON.chevron, "chev")}${installed.length} installed model${installed.length === 1 ? "" : "s"}, ${formatBytes(total)}</summary>
        <ul>${rows}</ul>
      </details>`;
  }

  _bind() {
    const root = this.shadowRoot;
    root.querySelectorAll("[data-act]").forEach((el) => {
      const host = this._hosts.find((h) => h.deviceId === el.dataset.host);
      if (!host) return;
      const ui = this._uiFor(host);
      switch (el.dataset.act) {
        case "model":
          el.addEventListener("change", () => {
            ui.model = el.value;
            ui.error = "";
            this._lastKey = "";
            this._render();
          });
          break;
        case "keep":
          el.addEventListener("change", () => {
            ui.keepAlive = el.value;
            ui.error = "";
            this._lastKey = "";
            this._render();
            if (el.value === "custom") root.querySelector(`[data-act="custom"][data-host="${CSS.escape(host.deviceId)}"]`)?.focus();
          });
          break;
        case "custom":
          el.addEventListener("input", () => (ui.custom = el.value));
          el.addEventListener("keydown", (e) => e.key === "Enter" && this._load(host));
          break;
        case "load":
          el.addEventListener("click", () => this._load(host));
          break;
        case "unload":
          el.addEventListener("click", () => this._unload(host, el.dataset.model));
          break;
        case "unload-all":
          el.addEventListener("click", () => this._unload(host, null));
          break;
      }
    });
    root.querySelectorAll("details.installed").forEach((el) => {
      const host = this._hosts.find((h) => h.deviceId === el.dataset.host);
      if (host) el.addEventListener("toggle", () => (this._uiFor(host).installedOpen = el.open));
    });
  }

  async _load(host) {
    const ui = this._uiFor(host);
    if (ui.loading || !ui.model) return;
    const keep = ui.keepAlive === "custom" ? ui.custom.trim() : ui.keepAlive;
    if (ui.keepAlive === "custom" && !keep) {
      ui.error = "Enter a duration such as 45m or 2h, or pick one from the list.";
      this._lastKey = "";
      this._render();
      return;
    }
    const data = { device_id: host.deviceId, model: ui.model };
    if (keep && keep !== "default") data.keep_alive = keep;
    ui.loading = true;
    ui.error = "";
    this._render();
    try {
      await this._hass.callService(DOMAIN, "load_model", data);
    } catch (err) {
      ui.error = err?.message || String(err);
    } finally {
      ui.loading = false;
      this._lastKey = "";
      this._render();
    }
  }

  async _unload(host, model) {
    const ui = this._uiFor(host);
    const states = this._hass.states[host.entities.loaded_model];
    const names = model ? [model] : (states?.attributes?.models || []).map((m) => m.name);
    names.forEach((n) => ui.unloading.add(n));
    ui.error = "";
    this._render();
    try {
      const data = { device_id: host.deviceId };
      if (model) data.model = model;
      await this._hass.callService(DOMAIN, "unload_model", data);
    } catch (err) {
      ui.error = err?.message || String(err);
    } finally {
      names.forEach((n) => ui.unloading.delete(n));
      this._lastKey = "";
      this._render();
    }
  }

  _tick() {
    const now = Date.now();
    this.shadowRoot.querySelectorAll("[data-fuse]").forEach((el) => {
      const f = this._fuse[el.dataset.fuse];
      if (!f) return;
      const left = f.expires - now;
      const pct = Math.max(0, Math.min(100, (100 * left) / f.total));
      el.style.width = `${pct}%`;
      el.classList.toggle("low", left < 60000);
    });
    this.shadowRoot.querySelectorAll("[data-when]").forEach((el) => {
      const f = this._fuse[el.dataset.when];
      if (f) el.textContent = formatRemaining(f.expires - now);
    });
  }
}

const STYLE = `
  :host { display: block; }
  ha-card { padding: 4px 0 8px; overflow: hidden; }
  .title { font-size: 1.25rem; font-weight: 500; margin: 12px 16px 0; color: var(--primary-text-color); letter-spacing: -0.005em; }
  .empty { margin: 16px; color: var(--secondary-text-color); }
  .host { padding: 12px 16px 14px; }
  .host + .host { border-top: 1px solid var(--divider-color); }
  header { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
  h2 { font-size: 1.05rem; font-weight: 600; margin: 0; color: var(--primary-text-color); }
  .status { display: inline-flex; align-items: center; gap: 6px; font-size: 0.85rem; color: var(--secondary-text-color); }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--disabled-text-color, #999); }
  .status.on .dot { background: var(--success-color, #43a047); }
  .status.off .dot { background: var(--error-color, #db4437); }
  .text-btn { margin-left: auto; background: none; border: 0; padding: 4px 2px; font: inherit; font-size: 0.85rem;
    color: var(--primary-color); cursor: pointer; border-radius: 4px; }
  .text-btn:disabled { color: var(--disabled-text-color); cursor: default; }
  .note { margin: 8px 0 0; color: var(--secondary-text-color); font-size: 0.9rem; line-height: 1.4; }
  .note .url { color: var(--primary-text-color); word-break: break-all; }
  .models { list-style: none; margin: 10px 0 0; padding: 0; display: grid; gap: 14px; }
  .model.busy { opacity: 0.55; }
  .row1 { display: flex; align-items: center; gap: 10px; min-height: 28px; }
  .name { font-weight: 500; color: var(--primary-text-color); overflow-wrap: anywhere; }
  .row1 .name { font-size: 1rem; flex: 1; }
  .size { color: var(--secondary-text-color); font-size: 0.9rem; font-variant-numeric: tabular-nums; white-space: nowrap; }
  .row2 { display: flex; gap: 10px; align-items: baseline; font-size: 0.82rem; color: var(--secondary-text-color); margin-top: 1px; }
  .meta { flex: 1; }
  /* Split across CPU and GPU is the slow case worth flagging; CPU-only is normal on a Pi. */
  .proc.mixed { color: var(--warning-color, #ffa600); }
  .row3 { display: flex; align-items: center; gap: 10px; margin-top: 6px; }
  .fuse { flex: 1; height: 4px; border-radius: 2px; background: var(--divider-color); overflow: hidden; }
  .burn { display: block; height: 100%; width: 100%; background: var(--primary-color); border-radius: 2px;
    transition: width 1s linear, background-color 0.3s; }
  .burn.low { background: var(--warning-color, #ffa600); }
  .fuse.pinned .burn { background: var(--primary-text-color); opacity: 0.55; }
  .when { font-size: 0.8rem; color: var(--secondary-text-color); white-space: nowrap; font-variant-numeric: tabular-nums;
    display: inline-flex; align-items: center; gap: 3px; min-width: 9.5em; justify-content: flex-end; }
  .ico { width: 20px; height: 20px; fill: currentColor; flex: none; }
  .ico.pin { width: 14px; height: 14px; }
  .icon-btn { width: 32px; height: 32px; margin: -4px -6px -4px 0; display: grid; place-items: center; border-radius: 50%;
    border: 0; background: none; color: var(--secondary-text-color); cursor: pointer; }
  .icon-btn:hover:not(:disabled) { color: var(--primary-text-color); background: var(--secondary-background-color, rgba(127,127,127,.12)); }
  .icon-btn:disabled { cursor: default; }
  .controls { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 8px 10px; margin-top: 16px; }
  .field { display: grid; gap: 3px; font-size: 0.75rem; color: var(--secondary-text-color); min-width: 0; }
  .field.grow { flex: 1 1 150px; }
  .field.narrow input { width: 6.5em; }
  select, input { font: inherit; font-size: 0.9rem; color: var(--primary-text-color); height: 36px; box-sizing: border-box;
    background: var(--secondary-background-color, transparent); border: 1px solid var(--divider-color);
    border-radius: 6px; padding: 0 8px; min-width: 0; max-width: 100%; }
  select { padding-right: 4px; }
  .primary { height: 36px; padding: 0 16px; border-radius: 18px; border: 0; font: inherit; font-size: 0.9rem; font-weight: 500;
    background: var(--primary-color); color: var(--text-primary-color, #fff); cursor: pointer;
    display: inline-flex; align-items: center; gap: 8px; }
  .primary:disabled { opacity: 0.7; cursor: progress; }
  :is(button, select, input, summary):focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
  .error { margin: 8px 0 0; font-size: 0.85rem; color: var(--error-color, #db4437); line-height: 1.4; }
  .installed { margin-top: 14px; }
  .installed summary { list-style: none; cursor: pointer; display: inline-flex; align-items: center; gap: 2px;
    font-size: 0.85rem; color: var(--secondary-text-color); border-radius: 4px; margin-left: -4px; padding-right: 4px; }
  .installed summary::-webkit-details-marker { display: none; }
  .chev { width: 18px; height: 18px; transition: transform 0.15s; }
  .installed[open] .chev { transform: rotate(90deg); }
  .installed ul { list-style: none; margin: 8px 0 0; padding: 0; display: grid; gap: 6px; }
  .installed li { display: grid; grid-template-columns: 1fr auto; column-gap: 10px; font-size: 0.88rem; }
  .installed li .meta { grid-column: 1; font-size: 0.78rem; color: var(--secondary-text-color); }
  .installed li .size { grid-column: 2; grid-row: 1; font-size: 0.85rem; }
  .tag { margin-left: 6px; font-size: 0.72rem; font-weight: 500; color: var(--primary-color); }
  .spinner { width: 14px; height: 14px; border-radius: 50%; border: 2px solid currentColor; border-right-color: transparent;
    animation: spin 0.8s linear infinite; display: inline-block; }
  @keyframes spin { to { transform: rotate(360deg); } }
  @media (prefers-reduced-motion: reduce) {
    .burn, .chev { transition: none; }
    .spinner { animation-duration: 2.4s; }
  }
`;

class OllamaMonitorCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { ...(config || {}) };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (s) =>
        ({
          title: "Title",
          hosts: "Hosts (empty shows all)",
          default_keep_alive: "Default keep-alive",
          show_controls: "Show load and unload controls",
          show_installed: "Show installed models",
        })[s.name] || s.name;
      this._form.addEventListener("value-changed", (ev) => {
        const config = { ...ev.detail.value };
        Object.keys(config).forEach((k) => (config[k] === "" || config[k] == null) && delete config[k]);
        if (config.default_keep_alive === "default") delete config.default_keep_alive;
        if (Array.isArray(config.hosts) && !config.hosts.length) delete config.hosts;
        this._config = config;
        this.dispatchEvent(new CustomEvent("config-changed", { detail: { config }, bubbles: true, composed: true }));
      });
      this.appendChild(this._form);
    }
    const hostNames = findHosts(this._hass).map((h) => h.name);
    this._form.hass = this._hass;
    this._form.schema = [
      { name: "title", selector: { text: {} } },
      { name: "hosts", selector: { select: { multiple: true, options: hostNames } } },
      {
        name: "default_keep_alive",
        selector: {
          select: {
            mode: "dropdown",
            options: KEEP_ALIVE_OPTIONS.filter((o) => o.value !== "custom").map((o) => ({ value: o.value, label: o.label })),
          },
        },
      },
      { name: "show_controls", selector: { boolean: {} } },
      { name: "show_installed", selector: { boolean: {} } },
    ];
    this._form.data = { show_controls: true, show_installed: true, default_keep_alive: "default", ...this._config };
  }
}

function register() {
  if (customElements.get("ollama-monitor-card")) return;
  customElements.define("ollama-monitor-card", OllamaMonitorCard);
  customElements.define("ollama-monitor-card-editor", OllamaMonitorCardEditor);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "ollama-monitor-card",
    name: "Ollama Monitor",
    description: "What each Ollama host has loaded, with load and unload controls.",
    preview: true,
    documentationURL: "https://github.com/ioiototm/ha-ollama-monitor",
  });
  console.info(
    `%c ollama-monitor-card %c v${CARD_VERSION} `,
    "background:#333;color:#fff;border-radius:3px 0 0 3px",
    "background:#666;color:#fff;border-radius:0 3px 3px 0",
  );
}

// The integration loads this file on every frontend page, which can be before Home
// Assistant has swapped in its scoped custom-element registry. Anything defined before
// that swap lands in a registry Lovelace never looks at ("Custom element doesn't
// exist"), so wait for the app shell to be defined first. Outside HA, register now.
if (document.querySelector("home-assistant")) {
  customElements.whenDefined("home-assistant").then(register);
} else {
  register();
}
