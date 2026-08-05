// Settings — separate Win95 Control Panel window (not inside chat).

const BG_PRESETS = [
  { id: "default", label: "По умолчанию", swatch: "linear-gradient(135deg,#221a3a,#050610)" },
  { id: "#1a1028", label: "Фиолетовый", swatch: "#1a1028" },
  { id: "#0a1628", label: "Ночной", swatch: "#0a1628" },
  { id: "#1a0a0a", label: "Тёмный", swatch: "#1a0a0a" },
  { id: "#2a1810", label: "Тёплый", swatch: "#2a1810" },
];

const DESK_PRESETS = [
  { id: "default", label: "Teal Win95", swatch: "linear-gradient(180deg,#0a8a8a,#066)" },
  { id: "#008080", label: "Classic teal", swatch: "#008080" },
  { id: "#000080", label: "Navy", swatch: "#000080" },
  { id: "#3a2a5a", label: "Kawaii", swatch: "#3a2a5a" },
  { id: "#1a3a2a", label: "Forest", swatch: "#1a3a2a" },
];

const SECTIONS = [
  { id: "camera", label: "Камера", icon: "cam" },
  { id: "look", label: "Внешний вид", icon: "look" },
  { id: "voice", label: "Голос", icon: "voice" },
  { id: "avatar", label: "Персонаж", icon: "avatar" },
  { id: "llm", label: "LLM", icon: "llm" },
];

export class SettingsPanel {
  constructor(opts) {
    this.avatar = opts.avatar;
    this.onApply = opts.onApply || (() => {});
    this.backdrop = document.getElementById("settings-backdrop");
    this.root = document.getElementById("settings-window");
    this.data = null;
    this.presets = [];
    this._section = "camera";
    this._probeModels = [];
  }

  async init() {
    if (!this.root) return;
    if (this._initPromise) return this._initPromise;
    this._initPromise = (async () => {
      try {
        const res = await fetch("/settings");
        const body = await res.json();
        this.data = body.settings;
        this.presets = body.providerPresets || [];
        try {
          const st = await fetch("/tts/status");
          this._omnivoice = await st.json();
        } catch (_) {
          this._omnivoice = { enabled: true, loaded: false };
        }
        try {
          const vr = await fetch("/vroid/status");
          this._vroid = await vr.json();
        } catch (_) {
          this._vroid = {};
        }
      } catch (err) {
        console.warn("[settings] load failed, using defaults", err);
        this.data = this.data || {
          camera: { yOffset: 0, distance: 1.45, fov: 24 },
          avatar: { vrmUrl: "", background: "default" },
          desktop: { wallpaper: "default" },
          voice: { lang: "ru-RU", rate: 1, pitch: 1.05, voiceName: "", sampleUrl: "", refText: "", instruct: "" },
          llm: { providers: {}, roles: {}, activeProvider: "", activeModel: "" },
        };
        this.presets = this.presets || [];
      }
      this._bindShell();
      this._ready = true;
      this._liveVisual();
    })();
    return this._initPromise;
  }

  async show(section) {
    if (!this._ready) {
      await this.init();
    }
    if (!this.data) await this.init();
    if (section) this._section = section;
    try {
      const st = await fetch("/tts/status");
      this._omnivoice = await st.json();
    } catch (_) { /* keep previous */ }
    try {
      const vr = await fetch("/vroid/status");
      this._vroid = await vr.json();
    } catch (_) { /* keep previous */ }
    this._render();
    this.backdrop?.classList.add("open");
    this.root?.classList.add("open");
    this.backdrop?.setAttribute("aria-hidden", "false");
    this._fillVoices();
  }

  hide() {
    this.backdrop?.classList.remove("open");
    this.root?.classList.remove("open");
    this.backdrop?.setAttribute("aria-hidden", "true");
  }

  get open() {
    return this.root?.classList.contains("open");
  }

  _render() {
    if (!this.root || !this.data) return;
    const s = this.data;
    const cam = s.camera || {};
    const avatar = s.avatar || {};
    const desk = s.desktop || {};
    const voice = s.voice || {};
    const llm = s.llm || {};
    const providers = llm.providers || {};

    this.root.innerHTML = `
      <div class="sw-title">
        <span class="sw-title-text">Настройки — code-sama</span>
        <button type="button" class="sw-x" id="set-close" title="Закрыть" aria-label="Закрыть"></button>
      </div>
      <div class="sw-body">
        <nav class="sw-nav">
          ${SECTIONS.map((sec) => `
            <button type="button" class="sw-nav-item ${this._section === sec.id ? "active" : ""}" data-section="${sec.id}">
              <span class="sw-ico sw-ico-${sec.icon}"></span>
              <span>${sec.label}</span>
            </button>
          `).join("")}
        </nav>
        <div class="sw-content" id="set-content">
          ${this._sectionHtml(this._section, { cam, avatar, desk, voice, llm, providers })}
        </div>
      </div>
      <div class="sw-footer">
        <button type="button" class="win-btn" id="set-save">OK</button>
        <button type="button" class="win-btn" id="set-cancel">Отмена</button>
        <button type="button" class="win-btn" id="set-apply">Применить</button>
      </div>
    `;
    this._bindContent();
    this._fillVoices();
  }

  _sectionHtml(id, ctx) {
    const { cam, avatar, desk, voice, llm, providers } = ctx;
    if (id === "camera") {
      return `
        <h2 class="sw-h">Камера вебками</h2>
        <p class="sw-lead">Слайдеры применяются сразу. Смещение двигает персонажа вверх/вниз.</p>
        <div class="sw-field">
          <div class="sw-field-head">
            <label for="set-cam-y">Смещение персонажа (↑↓)</label>
            <span class="sw-num" id="set-cam-y-val">${Number(cam.yOffset ?? 0).toFixed(2)}</span>
          </div>
          <input type="range" id="set-cam-y" min="-0.8" max="0.8" step="0.01" value="${cam.yOffset ?? 0}" />
          <p class="sw-hint">Вправо — персонаж выше в кадре, влево — ниже. Камера не следует.</p>
        </div>
        <div class="sw-field">
          <div class="sw-field-head">
            <label for="set-cam-dist">Приближение</label>
            <span class="sw-num" id="set-cam-dist-val">${Number(cam.distance ?? 1.45).toFixed(2)}</span>
          </div>
          <input type="range" id="set-cam-dist" min="0.85" max="2.4" step="0.05" value="${cam.distance ?? 1.45}" />
          <p class="sw-hint">Меньше — ближе к лицу. Больше — общий план до груди.</p>
        </div>
      `;
    }
    if (id === "look") {
      const camFile = _isUpload(avatar.background) ? avatar.background : "";
      const deskFile = _isUpload(desk.wallpaper) ? desk.wallpaper : "";
      return `
        <h2 class="sw-h">Внешний вид</h2>
        <p class="sw-lead">Фоны загружаются файлом с компьютера.</p>
        <h3 class="sw-h3">Фон вебки</h3>
        <div class="sw-swatches" id="set-cam-bg-chips">
          ${BG_PRESETS.map((p) => `
            <button type="button" class="sw-swatch ${(!_isUpload(avatar.background) && (avatar.background || "default") === p.id) ? "active" : ""}" data-bg="${p.id}" title="${p.label}">
              <i style="background:${p.swatch}"></i>
              <span>${p.label}</span>
            </button>
          `).join("")}
        </div>
        ${_fileField("set-cam-bg-file", "image/*", "Загрузить картинку фона", camFile)}
        <h3 class="sw-h3">Обои рабочего стола</h3>
        <div class="sw-swatches" id="set-desk-chips">
          ${DESK_PRESETS.map((p) => `
            <button type="button" class="sw-swatch ${(!_isUpload(desk.wallpaper) && (desk.wallpaper || "default") === p.id) ? "active" : ""}" data-desk="${p.id}" title="${p.label}">
              <i style="background:${p.swatch}"></i>
              <span>${p.label}</span>
            </button>
          `).join("")}
        </div>
        ${_fileField("set-desk-file", "image/*", "Загрузить обои", deskFile)}
      `;
    }
    if (id === "voice") {
      const sample = voice.sampleUrl || "";
      const ov = this._omnivoice || {};
      const ovLine = ov.enabled === false
        ? "OmniVoice выключен (OMNIVOICE_DISABLED)."
        : ov.loaded
          ? `OmniVoice готов · ${ov.model || "k2-fsa/OmniVoice"}`
          : ov.error
            ? `OmniVoice: ${ov.error}`
            : ov.enabled
              ? "OmniVoice подключится при первой реплике (скачает веса с HF)."
              : "Статус OmniVoice неизвестен — открой настройки ещё раз.";
      return `
        <h2 class="sw-h">Голос · OmniVoice</h2>
        <p class="sw-lead">Реплики агента синтезирует локальный OmniVoice. Загрузи образец голоса (.wav / .mp3) для клонирования.</p>
        <p class="sw-hint" id="set-ov-status">${escapeHtml(ovLine)}</p>
        ${_fileField("set-voice-file", "audio/*", "Загрузить образец голоса", sample)}
        <div class="sw-field">
          <label for="set-voice-reftext">Текст образца (опционально)</label>
          <input type="text" id="set-voice-reftext" class="win-input" placeholder="Что сказано в образце — иначе Whisper распознает сам"
            value="${escapeAttr(voice.refText || "")}" />
        </div>
        <div class="sw-field">
          <label for="set-voice-instruct">Voice design (если нет образца)</label>
          <input type="text" id="set-voice-instruct" class="win-input" placeholder="female, young adult, russian accent, moderate pitch"
            value="${escapeAttr(voice.instruct || "")}" />
          <p class="sw-hint">Только теги OmniVoice через запятую: female/male, young adult/teenager/child, russian accent, whisper…</p>
        </div>
        <div class="sw-actions" style="margin-bottom:12px">
          <button type="button" class="win-btn" id="set-voice-test">Проверить озвучку</button>
          <button type="button" class="win-btn" id="set-voice-sample" ${sample ? "" : "disabled"}>Прослушать образец</button>
        </div>
        <h3 class="sw-h3">Fallback · системный голос</h3>
        <p class="sw-hint">Если OmniVoice недоступен (нет torch/GPU/весов), используется SpeechSynthesis браузера.</p>
        <div class="sw-grid2">
          <div class="sw-field">
            <label for="set-voice-lang">Язык</label>
            <select id="set-voice-lang" class="win-input">
              ${["ru-RU", "en-US", "en-GB", "ja-JP", "de-DE", "fr-FR"].map((l) =>
                `<option value="${l}" ${voice.lang === l ? "selected" : ""}>${l}</option>`
              ).join("")}
            </select>
          </div>
          <div class="sw-field">
            <label for="set-voice-name">Системный голос</label>
            <select id="set-voice-name" class="win-input"><option value="">Авто</option></select>
          </div>
        </div>
        <div class="sw-field">
          <div class="sw-field-head">
            <label for="set-voice-rate">Скорость</label>
            <span class="sw-num" id="set-voice-rate-val">${Number(voice.rate ?? 1).toFixed(2)}</span>
          </div>
          <input type="range" id="set-voice-rate" min="0.6" max="1.6" step="0.05" value="${voice.rate ?? 1}" />
        </div>
        <div class="sw-field">
          <div class="sw-field-head">
            <label for="set-voice-pitch">Высота (только fallback)</label>
            <span class="sw-num" id="set-voice-pitch-val">${Number(voice.pitch ?? 1.05).toFixed(2)}</span>
          </div>
          <input type="range" id="set-voice-pitch" min="0.7" max="1.4" step="0.05" value="${voice.pitch ?? 1.05}" />
        </div>
      `;
    }
    if (id === "avatar") {
      const vrm = avatar.vrmUrl || "";
      const vr = this._vroid || {};
      const meta = (vr.installed || []).find((x) => (x.url || "") === vrm)?.meta
        || (vr.last_installed && vr.last_installed.meta)
        || null;
      const metaLine = meta
        ? `${meta.title || "VRM"} · ${meta.spec || "?"} · ${meta.meshes || "?"} mesh · author ${Array.isArray(meta.author) ? meta.author.join(", ") : (meta.author || "—")}`
        : (vr.studio_found ? `VRoid Studio: ${vr.studio}` : "VRoid Studio не найден");
      return `
        <h2 class="sw-h">Модель персонажа</h2>
        <p class="sw-lead">Уникальная code-sama (VRM 1.0) из VRoid Studio. Можно переэкспортировать и подхватить снова.</p>
        <p class="sw-hint" id="set-vroid-status">${escapeHtml(metaLine)}</p>
        ${_fileField("set-vrm-file", ".vrm,model/gltf-binary,application/octet-stream", "Загрузить .vrm", vrm)}
        <div class="sw-actions" style="margin-bottom:12px">
          <button type="button" class="win-btn" id="set-vroid-desktop">Взять Code-sama.vrm с Desktop</button>
          <button type="button" class="win-btn" id="set-vroid-launch">Открыть VRoid Studio</button>
          <button type="button" class="win-btn" id="set-vroid-watch">${vr.watching ? "Стоп watch" : "Watch export_drop"}</button>
        </div>
        <p class="sw-hint">Экспорт из VRoid → <code>characters/export_drop/</code> (watch) или Desktop → кнопка выше.</p>
      `;
    }
    // llm
    return `
      <h2 class="sw-h">LLM-провайдер</h2>
      <p class="sw-lead">Как в Odysseus: пресет → ключ → модель для ролей.</p>

      <div class="sw-card">
        <h3 class="sw-h3">Добавить провайдера</h3>
        <div class="sw-grid2">
          <div class="sw-field">
            <label for="set-prov-preset">Пресет</label>
            <select id="set-prov-preset" class="win-input">
              ${this.presets.map((p) =>
                `<option value="${p.id}" ${p.auth === "device" && p.id !== "chatgpt-subscription" ? "disabled" : ""}>${p.name}${p.id === "copilot" ? " (скоро)" : ""}${p.id === "chatgpt-subscription" ? " · OAuth" : ""}</option>`
              ).join("")}
            </select>
          </div>
          <div class="sw-field">
            <label for="set-prov-name">Имя</label>
            <input type="text" id="set-prov-name" class="win-input" placeholder="openrouter" />
          </div>
        </div>
        <div class="sw-field" id="set-prov-url-wrap">
          <label for="set-prov-url">Base URL</label>
          <input type="url" id="set-prov-url" class="win-input" placeholder="https://…" />
        </div>
        <div class="sw-field" id="set-prov-key-wrap">
          <label for="set-prov-key">API key</label>
          <input type="password" id="set-prov-key" class="win-input" placeholder="sk-…" autocomplete="off" />
        </div>
        <div class="sw-actions">
          <button type="button" class="win-btn" id="set-prov-add">Добавить</button>
          <button type="button" class="win-btn" id="set-prov-test">Проверить</button>
          <button type="button" class="win-btn" id="set-prov-chatgpt" style="display:none">Подключить ChatGPT</button>
          <span id="set-prov-msg" class="sw-msg"></span>
        </div>
        <p class="sw-hint" id="set-prov-chatgpt-hint" style="display:none">Откроется auth.openai.com — введи код устройства, затем вернись сюда.</p>
      </div>

      <div class="sw-prov-list">
        ${Object.entries(providers).map(([name, spec]) => `
          <div class="sw-prov-item">
            <div>
              <strong>${name}</strong>
              <small>${spec.base_url || ""}</small>
              <small>${spec.auth_mode === "chatgpt" ? "ChatGPT Subscription ✓" : (spec.has_key ? "ключ ✓" : "без ключа")} ${spec.api_key && spec.auth_mode !== "chatgpt" ? "· " + spec.api_key : ""}</small>
            </div>
            <button type="button" class="win-btn sw-prov-del" data-name="${name}">Удалить</button>
          </div>
        `).join("") || `<p class="sw-hint">Пока нет провайдеров — добавь первый выше.</p>`}
      </div>

      <div class="sw-card">
        <h3 class="sw-h3">Активный маршрут</h3>
        <div class="sw-grid2">
          <div class="sw-field">
            <label for="set-active-prov">Провайдер</label>
            <select id="set-active-prov" class="win-input">
              <option value="">—</option>
              ${Object.keys(providers).map((n) =>
                `<option value="${n}" ${llm.activeProvider === n ? "selected" : ""}>${n}</option>`
              ).join("")}
            </select>
          </div>
          <div class="sw-field">
            <label for="set-active-model">Модель</label>
            <input type="text" id="set-active-model" class="win-input" list="set-model-list"
              value="${llm.activeModel || ""}" placeholder="qwen/qwen3-235b-a22b-2507" />
            <datalist id="set-model-list"></datalist>
          </div>
        </div>
        <p class="sw-hint">Пара подставится во все роли (streamer / worker / …), где провайдер пуст.</p>
      </div>
    `;
  }

  _bindShell() {
    this.backdrop?.addEventListener("click", (e) => {
      if (e.target === this.backdrop) this.hide();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && this.open) this.hide();
    });
  }

  _bindContent() {
    this.root.onclick = (e) => this._onClick(e);
    this.root.oninput = (e) => this._onInput(e);
    this.root.onchange = (e) => this._onChange(e);

    if (this._section === "llm") {
      const sel = this.root.querySelector("#set-prov-preset");
      if (sel) {
        sel.value = "proxyapi";
        this._applyPreset(sel.value);
      }
    }

    // File upload inputs
    const fileMap = {
      "set-cam-bg-file": "avatar_bg",
      "set-desk-file": "desktop_bg",
      "set-voice-file": "voice",
      "set-vrm-file": "vrm",
    };
    for (const [id, kind] of Object.entries(fileMap)) {
      const input = this.root.querySelector("#" + id);
      if (!input) continue;
      input.addEventListener("change", async () => {
        const file = input.files?.[0];
        if (!file) return;
        await this._upload(kind, file, input);
      });
    }
  }

  async _upload(kind, file, inputEl) {
    const status = inputEl?.closest(".sw-file")?.querySelector(".sw-file-status");
    if (status) status.textContent = "Загрузка…";
    try {
      const fd = new FormData();
      fd.append("kind", kind);
      fd.append("file", file, file.name);
      const r = await fetch("/settings/upload", { method: "POST", body: fd });
      const body = await r.json();
      if (!body.ok) {
        if (status) status.textContent = "Ошибка: " + (body.error || r.status);
        return;
      }
      this.data = body.settings;
      if (status) status.textContent = `✓ ${file.name}`;
      this._liveVisual();
      // Re-render so previews / filenames refresh.
      this._render();
    } catch (err) {
      if (status) status.textContent = String(err);
    }
  }

  _fillVoices() {
    const sel = this.root.querySelector("#set-voice-name");
    if (!sel) return;
    const fill = () => {
      const voices = this.avatar.listVoices();
      const current = this.data?.voice?.voiceName || "";
      sel.innerHTML = `<option value="">Авто</option>` + voices.map((v) =>
        `<option value="${escapeAttr(v.name)}" ${v.name === current ? "selected" : ""}>${escapeHtml(v.name)} (${v.lang})</option>`
      ).join("");
    };
    fill();
    if ("speechSynthesis" in window) speechSynthesis.onvoiceschanged = fill;
  }

  _onClick(e) {
    const t = e.target.closest("button, .sw-swatch, .sw-nav-item");
    if (!t) return;

    if (t.id === "set-close" || t.id === "set-cancel") {
      this.hide();
      return;
    }
    if (t.id === "set-save") {
      this._save().then(() => this.hide());
      return;
    }
    if (t.id === "set-apply") {
      this._save();
      return;
    }
    if (t.dataset.section) {
      this._readFormIntoData();
      this._section = t.dataset.section;
      this._render();
      return;
    }
    if (t.id === "set-voice-sample") {
      const sample = this.data?.voice?.sampleUrl;
      if (sample) {
        const audio = new Audio(sample);
        audio.play().catch((err) => console.warn("voice sample play failed", err));
      }
      return;
    }
    if (t.id === "set-voice-test") {
      this._readFormIntoData();
      this._liveVisual();
      const btn = t;
      const prev = btn.textContent;
      btn.disabled = true;
      btn.textContent = "Синтез…";
      Promise.resolve(this.avatar.speak("Привет! Это проверка голоса OmniVoice в code-sama."))
        .catch((err) => console.warn("voice test failed", err))
        .finally(() => {
          btn.disabled = false;
          btn.textContent = prev;
        });
      return;
    }
    if (t.id === "set-prov-add") {
      this._addProvider();
      return;
    }
    if (t.id === "set-prov-test") {
      this._testProvider();
      return;
    }
    if (t.id === "set-prov-chatgpt") {
      this._connectChatGPT();
      return;
    }
    if (t.id === "set-vroid-desktop") {
      this._vroidInstallDesktop();
      return;
    }
    if (t.id === "set-vroid-launch") {
      fetch("/vroid/launch", { method: "POST" })
        .then((r) => r.json())
        .then((body) => {
          const el = this.root.querySelector("#set-vroid-status");
          if (el) el.textContent = body.ok ? (body.hint || "VRoid запущен") : (body.error || "fail");
        })
        .catch((err) => console.warn(err));
      return;
    }
    if (t.id === "set-vroid-watch") {
      const watching = !!this._vroid?.watching;
      const url = watching ? "/vroid/watch/stop" : "/vroid/watch/start";
      fetch(url, { method: "POST" })
        .then((r) => r.json())
        .then(async (body) => {
          const vr = await fetch("/vroid/status").then((r) => r.json()).catch(() => ({}));
          this._vroid = vr;
          this._readFormIntoData();
          this._render();
          const el = this.root.querySelector("#set-vroid-status");
          if (el) el.textContent = body.watching
            ? `Watch: ${body.watch_dir || vr.watch_dir}`
            : "Watch остановлен";
        });
      return;
    }
    if (t.classList.contains("sw-prov-del")) {
      const name = t.dataset.name;
      if (name && this.data.llm?.providers) {
        delete this.data.llm.providers[name];
        if (this.data.llm.activeProvider === name) this.data.llm.activeProvider = "";
        this._render();
      }
      return;
    }
    if (t.dataset.bg != null) {
      this.root.querySelectorAll("#set-cam-bg-chips .sw-swatch").forEach((c) => c.classList.remove("active"));
      t.classList.add("active");
      if (this.data.avatar) this.data.avatar.background = t.dataset.bg;
      this._liveVisual();
      return;
    }
    if (t.dataset.desk != null) {
      this.root.querySelectorAll("#set-desk-chips .sw-swatch").forEach((c) => c.classList.remove("active"));
      t.classList.add("active");
      if (this.data.desktop) this.data.desktop.wallpaper = t.dataset.desk;
      this._liveVisual();
    }
  }

  _onInput(e) {
    const t = e.target;
    if (!(t instanceof HTMLInputElement)) return;
    const map = {
      "set-cam-y": "set-cam-y-val",
      "set-cam-dist": "set-cam-dist-val",
      "set-voice-rate": "set-voice-rate-val",
      "set-voice-pitch": "set-voice-pitch-val",
    };
    if (map[t.id]) {
      const el = this.root.querySelector("#" + map[t.id]);
      if (el) el.textContent = Number(t.value).toFixed(2);
    }
    if (
      t.id.startsWith("set-cam-") ||
      t.id === "set-cam-bg-url" ||
      t.id === "set-desk-url" ||
      t.id === "set-vrm-url"
    ) {
      this._liveVisual();
    }
  }

  _onChange(e) {
    const t = e.target;
    if (t.id === "set-prov-preset") {
      this._applyPreset(t.value);
      return;
    }
    if (t.id?.startsWith("set-voice") || t.id === "set-vrm-url") {
      this._liveVisual();
    }
  }

  _applyPreset(id) {
    const p = this.presets.find((x) => x.id === id);
    if (!p) return;
    const nameEl = this.root.querySelector("#set-prov-name");
    const urlEl = this.root.querySelector("#set-prov-url");
    const keyEl = this.root.querySelector("#set-prov-key");
    const chatgptBtn = this.root.querySelector("#set-prov-chatgpt");
    const addBtn = this.root.querySelector("#set-prov-add");
    const testBtn = this.root.querySelector("#set-prov-test");
    const keyWrap = this.root.querySelector("#set-prov-key-wrap");
    const hint = this.root.querySelector("#set-prov-chatgpt-hint");
    if (nameEl) nameEl.value = id === "custom" ? "" : (id === "chatgpt-subscription" ? "chatgpt" : id);
    if (urlEl) urlEl.value = p.base_url || "";
    if (keyEl && p.default_api_key) keyEl.value = p.default_api_key;
    const isChatgpt = id === "chatgpt-subscription";
    if (chatgptBtn) chatgptBtn.style.display = isChatgpt ? "" : "none";
    if (hint) hint.style.display = isChatgpt ? "" : "none";
    if (keyWrap) keyWrap.style.display = isChatgpt ? "none" : "";
    if (addBtn) addBtn.style.display = isChatgpt ? "none" : "";
    if (testBtn) testBtn.style.display = isChatgpt ? "none" : "";
  }

  async _vroidInstallDesktop() {
    const el = this.root.querySelector("#set-vroid-status");
    if (el) el.textContent = "Ставлю Code-sama.vrm…";
    try {
      const body = await fetch("/vroid/install", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ fromDesktop: true, prefer: "Code-sama.vrm" }),
      }).then((r) => r.json());
      if (!body.ok) {
        if (el) el.textContent = body.error || "install failed";
        return;
      }
      if (body.settings) this.data = body.settings;
      else if (this.data?.avatar) this.data.avatar.vrmUrl = body.url;
      try {
        this._vroid = await fetch("/vroid/status").then((r) => r.json());
      } catch (_) { /* ignore */ }
      this._liveVisual();
      this._render();
      if (el) {
        const m = body.meta || {};
        el.textContent = `Готово: ${m.title || body.url} (${m.spec || "VRM"})`;
      }
    } catch (err) {
      if (el) el.textContent = String(err.message || err);
    }
  }

  async _connectChatGPT() {
    const msg = this.root.querySelector("#set-prov-msg");
    const btn = this.root.querySelector("#set-prov-chatgpt");
    if (msg) msg.textContent = "Запрашиваю код…";
    if (btn) btn.disabled = true;
    try {
      const start = await fetch("/settings/providers/chatgpt/device/start", { method: "POST" }).then((r) => r.json());
      if (!start.ok) throw new Error(start.error || "start failed");
      const uri = start.verification_uri || "https://auth.openai.com/codex/device";
      if (msg) msg.textContent = `Код: ${start.user_code} — подтверди в браузере`;
      window.open(uri, "_blank", "noopener");
      const deadline = Date.now() + (start.expires_in || 900) * 1000;
      const interval = Math.max(3, Number(start.interval) || 5) * 1000;
      while (Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, interval));
        const poll = await fetch("/settings/providers/chatgpt/device/poll", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ flow_id: start.flow_id }),
        }).then((r) => r.json());
        if (poll.status === "pending") {
          if (msg) msg.textContent = `Жду подтверждения… код ${start.user_code}`;
          continue;
        }
        if (poll.status === "authorized" && poll.ok) {
          this.data = poll.settings || this.data;
          this._probeModels = poll.models || [];
          if (msg) msg.textContent = `Подключено · ${(poll.models || []).length} моделей`;
          this._render();
          return;
        }
        throw new Error(poll.error || "authorization failed");
      }
      throw new Error("время ожидания истекло");
    } catch (err) {
      if (msg) msg.textContent = String(err.message || err);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  _collectVisualPatch() {
    const cam = this.data.camera || {};
    const avatar = this.data.avatar || {};
    const desk = this.data.desktop || {};
    const voice = this.data.voice || {};

    const yEl = this.root.querySelector("#set-cam-y");
    const dEl = this.root.querySelector("#set-cam-dist");
    const bgChip = this.root.querySelector("#set-cam-bg-chips .sw-swatch.active");
    const deskChip = this.root.querySelector("#set-desk-chips .sw-swatch.active");
    const langEl = this.root.querySelector("#set-voice-lang");
    const voiceEl = this.root.querySelector("#set-voice-name");
    const rateEl = this.root.querySelector("#set-voice-rate");
    const pitchEl = this.root.querySelector("#set-voice-pitch");
    const refEl = this.root.querySelector("#set-voice-reftext");
    const instructEl = this.root.querySelector("#set-voice-instruct");

    let avatarBackground = avatar.background || "default";
    if (bgChip?.dataset.bg) avatarBackground = bgChip.dataset.bg;
    // Keep uploaded file if no chip was just picked this session
    if (_isUpload(avatar.background) && !bgChip) avatarBackground = avatar.background;

    let desktopWallpaper = desk.wallpaper || "default";
    if (deskChip?.dataset.desk) desktopWallpaper = deskChip.dataset.desk;
    if (_isUpload(desk.wallpaper) && !deskChip) desktopWallpaper = desk.wallpaper;

    return {
      cameraYOffset: yEl ? Number(yEl.value) : Number(cam.yOffset ?? 0),
      cameraDistance: dEl ? Number(dEl.value) : Number(cam.distance ?? 1.45),
      cameraFov: Number(cam.fov ?? 24),
      avatarBackground,
      desktopWallpaper,
      avatarVrmUrl: avatar.vrmUrl || "",
      ttsLang: langEl ? langEl.value : (voice.lang || "ru-RU"),
      ttsRate: rateEl ? Number(rateEl.value) : Number(voice.rate ?? 1),
      ttsPitch: pitchEl ? Number(pitchEl.value) : Number(voice.pitch ?? 1.05),
      ttsVoiceName: voiceEl ? voiceEl.value : (voice.voiceName || ""),
      ttsSampleUrl: voice.sampleUrl || "",
      ttsRefText: refEl ? refEl.value.trim() : (voice.refText || ""),
      ttsInstruct: instructEl ? instructEl.value.trim() : (voice.instruct || ""),
      omnivoice: this._omnivoice || null,
    };
  }

  _liveVisual() {
    const cfg = this._collectVisualPatch();
    this.avatar.applyVisualSettings(cfg);
    this.onApply(cfg);
  }

  _readFormIntoData() {
    const vis = this._collectVisualPatch();
    this.data.camera = {
      yOffset: vis.cameraYOffset,
      distance: vis.cameraDistance,
      fov: vis.cameraFov,
    };
    this.data.avatar = {
      vrmUrl: vis.avatarVrmUrl,
      background: vis.avatarBackground,
    };
    this.data.desktop = {
      wallpaper: vis.desktopWallpaper,
    };
    this.data.voice = {
      lang: vis.ttsLang,
      rate: vis.ttsRate,
      pitch: vis.ttsPitch,
      voiceName: vis.ttsVoiceName,
      sampleUrl: this.data.voice?.sampleUrl || vis.ttsSampleUrl || "",
      refText: vis.ttsRefText || "",
      instruct: vis.ttsInstruct || "",
    };
    this.data.llm = this.data.llm || { providers: {}, roles: {} };
    const ap = this.root.querySelector("#set-active-prov");
    const am = this.root.querySelector("#set-active-model");
    if (ap) this.data.llm.activeProvider = ap.value || "";
    if (am) this.data.llm.activeModel = am.value?.trim() || "";
  }

  async _addProvider() {
    const name = (this.root.querySelector("#set-prov-name")?.value || "").trim();
    const base_url = (this.root.querySelector("#set-prov-url")?.value || "").trim();
    const api_key = this.root.querySelector("#set-prov-key")?.value || "";
    const msg = this.root.querySelector("#set-prov-msg");
    if (!name || !base_url) {
      if (msg) msg.textContent = "Нужны имя и Base URL";
      return;
    }
    this.data.llm = this.data.llm || { providers: {}, roles: {} };
    this.data.llm.providers = this.data.llm.providers || {};
    this.data.llm.providers[name] = {
      base_url,
      api_key,
      preset: this.root.querySelector("#set-prov-preset")?.value || "custom",
      has_key: !!api_key,
    };
    if (!this.data.llm.activeProvider) this.data.llm.activeProvider = name;
    if (msg) msg.textContent = `«${name}» добавлен — нажми Применить`;
    this._render();
  }

  async _testProvider() {
    const msg = this.root.querySelector("#set-prov-msg");
    const name = (this.root.querySelector("#set-prov-name")?.value || "").trim();
    const base_url = (this.root.querySelector("#set-prov-url")?.value || "").trim();
    let api_key = this.root.querySelector("#set-prov-key")?.value || "";
    if (!api_key && name && this.data.llm?.providers?.[name]) {
      api_key = this.data.llm.providers[name].api_key || "";
    }
    if (msg) msg.textContent = "Проверяю…";
    try {
      const r = await fetch("/settings/providers/test", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ name, base_url, api_key }),
      });
      const body = await r.json();
      if (!body.ok) {
        if (msg) msg.textContent = `Ошибка: ${body.error || body.status}`;
        return;
      }
      this._probeModels = body.models || [];
      const dl = this.root.querySelector("#set-model-list");
      if (dl) {
        dl.innerHTML = this._probeModels.map((m) => `<option value="${escapeAttr(m)}"></option>`).join("");
      }
      if (msg) msg.textContent = `OK — ${body.count} моделей`;
    } catch (err) {
      if (msg) msg.textContent = String(err);
    }
  }

  async _save() {
    this._readFormIntoData();
    const r = await fetch("/settings", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ settings: this.data }),
    });
    const body = await r.json();
    if (!body.ok) {
      alert("Не удалось сохранить: " + (body.error || r.status));
      return;
    }
    this.data = body.settings;
    this._liveVisual();
  }
}

function _isUrl(v) {
  return typeof v === "string" && (v.startsWith("http") || v.startsWith("/"));
}

function _isUpload(v) {
  return typeof v === "string" && v.startsWith("/uploads/");
}

function _fileField(id, accept, label, currentUrl) {
  const name = currentUrl ? currentUrl.split("/").pop() : "";
  return `
    <div class="sw-file">
      <label class="sw-file-btn win-btn" for="${id}">${label}</label>
      <input type="file" id="${id}" accept="${accept}" hidden />
      <span class="sw-file-status">${name ? "✓ " + name : "Файл не выбран"}</span>
    </div>
  `;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function escapeAttr(s) {
  return escapeHtml(s);
}
