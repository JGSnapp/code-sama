// Real 3D VRM avatar with OmniVoice TTS (+ browser SpeechSynthesis fallback)
// and viseme-style lip-sync.
//
// Fallback chain:
//   1. AIKEYA_URL set → iframe the aikeya VRM viewer
//   2. AVATAR_VRM_URL loads → render with Three.js + @pixiv/three-vrm
//   3. anything fails → a friendly 2D canvas portrait keeps the panel alive
//
// Speech:
//   1. POST /tts → local OmniVoice (clone from sample / voice design / auto)
//   2. browser speechSynthesis if OmniVoice is down

import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { VRMLoaderPlugin, VRMUtils, VRMExpressionPresetName } from "@pixiv/three-vrm";

const VISEMES = ["aa", "ih", "ou", "ee", "oh"];

export class Avatar {
  constructor() {
    this.iframe = document.getElementById("cam-iframe");
    this.host3d = document.getElementById("cam-3d");
    this.canvas = document.getElementById("cam-canvas");
    this.caption = document.getElementById("cam-caption");
    this.mood = "idle";
    this.mouthValue = 0;
    this.mouthTarget = 0;
    this.blinkValue = 0;
    this.nextBlink = 1.5 + Math.random() * 3;
    this.t = 0;
    this.tts = null;
    this.vrm = null;
    window._avatar = this;
    this.viseme = "aa";
    this._renderer = null;
    this._clock = null;
    this._lastFrame = performance.now();
    this._cameraYOffset = 0;
    this._cameraDistance = 1.45;
    this._cameraFov = 24;
    this._vrmUrl = null;
    this._loopRunning = false;
  }

  configure(cfg) {
    this.applyVisualSettings(cfg);
    if (cfg.aikeyaUrl) {
      this.iframe.src = cfg.aikeyaUrl;
      this.iframe.style.display = "block";
      this.host3d.style.display = "none";
      this.canvas.style.display = "none";
      this._startTts(cfg);
      return;
    }
    const url = cfg.avatarVrmUrl;
    if (url && url !== this._vrmUrl) {
      this._initTHREE(url).then((ok) => {
        if (!ok) this._startCanvasFallback();
      });
    } else if (this.vrm) {
      this._reframe();
    } else if (url) {
      this._initTHREE(url).then((ok) => {
        if (!ok) this._startCanvasFallback();
      });
    }
    this._startTts(cfg);
  }

  /** Live-apply camera / BG / voice / VRM from Settings without full reload. */
  applyVisualSettings(cfg) {
    if (cfg.cameraYOffset != null) this._cameraYOffset = Number(cfg.cameraYOffset);
    if (cfg.cameraDistance != null) this._cameraDistance = Number(cfg.cameraDistance);
    if (cfg.cameraFov != null) this._cameraFov = Number(cfg.cameraFov);
    if (cfg.avatarBackground != null) this.setAvatarBackground(cfg.avatarBackground);
    if (cfg.desktopWallpaper != null) this.setDesktopWallpaper(cfg.desktopWallpaper);
    if (this.camera) this._reframe();
    if (
      cfg.ttsLang ||
      cfg.ttsRate != null ||
      cfg.ttsPitch != null ||
      cfg.ttsVoiceName != null ||
      cfg.ttsSampleUrl != null ||
      cfg.ttsRefText != null ||
      cfg.ttsInstruct != null ||
      cfg.omnivoice != null
    ) {
      this._startTts({
        ttsLang: cfg.ttsLang || this.tts?.lang,
        ttsRate: cfg.ttsRate ?? this.tts?.rate,
        ttsPitch: cfg.ttsPitch ?? this.tts?.pitch,
        ttsVoiceName: cfg.ttsVoiceName ?? this.tts?.voiceName,
        ttsSampleUrl: cfg.ttsSampleUrl ?? this.tts?.sampleUrl,
        ttsRefText: cfg.ttsRefText ?? this.tts?.refText,
        ttsInstruct: cfg.ttsInstruct ?? this.tts?.instruct,
        omnivoice: cfg.omnivoice ?? this.tts?.omnivoice,
      });
    }
    if (cfg.avatarVrmUrl && cfg.avatarVrmUrl !== this._vrmUrl && !cfg.aikeyaUrl) {
      this._initTHREE(cfg.avatarVrmUrl).then((ok) => {
        if (!ok) console.warn("[avatar] VRM reload failed");
      });
    }
  }

  setAvatarBackground(value) {
    const frame = document.getElementById("cam-frame");
    if (!frame) return;
    if (!value || value === "default") {
      frame.style.backgroundImage = "";
      frame.style.backgroundColor = "";
      frame.classList.remove("custom-bg");
      return;
    }
    if (value.startsWith("#") || value.startsWith("rgb")) {
      frame.style.backgroundImage = "none";
      frame.style.backgroundColor = value;
      frame.classList.add("custom-bg");
      return;
    }
    frame.style.backgroundColor = "#110612";
    frame.style.backgroundImage =
      `radial-gradient(ellipse at 50% 60%, rgba(0,0,0,0) 30%, rgba(0,0,0,0.45) 100%),` +
      `url("${value}") center center / cover no-repeat`;
    frame.classList.add("custom-bg");
  }

  setDesktopWallpaper(value) {
    const desk = document.querySelector(".desktop");
    if (!desk) return;
    if (!value || value === "default") {
      desk.style.backgroundImage = "";
      desk.style.backgroundColor = "";
      desk.classList.remove("custom-wallpaper");
      return;
    }
    if (value.startsWith("#") || value.startsWith("rgb")) {
      desk.style.backgroundImage = "none";
      desk.style.backgroundColor = value;
      desk.classList.add("custom-wallpaper");
      return;
    }
    desk.style.backgroundColor = "#066";
    desk.style.backgroundImage = `url("${value}")`;
    desk.style.backgroundSize = "cover";
    desk.style.backgroundPosition = "center";
    desk.classList.add("custom-wallpaper");
  }

  listVoices() {
    if (!("speechSynthesis" in window)) return [];
    return speechSynthesis.getVoices().map((v) => ({
      name: v.name,
      lang: v.lang,
      default: v.default,
    }));
  }

  // ─── TTS ────────────────────────────────────────────
  _startTts(cfg) {
    this.tts = {
      lang: cfg.ttsLang || "ru-RU",
      rate: cfg.ttsRate ?? 1.0,
      pitch: cfg.ttsPitch ?? 1.0,
      voiceName: cfg.ttsVoiceName || "",
      sampleUrl: cfg.ttsSampleUrl || "",
      refText: cfg.ttsRefText || "",
      instruct: cfg.ttsInstruct || "",
      omnivoice: cfg.omnivoice || null,
      voice: null,
      langOverride: null,
    };
    this._stopSpeak();
    if (!("speechSynthesis" in window)) {
      console.warn("[avatar] speechSynthesis not supported — OmniVoice only");
      return;
    }
    const pickVoice = () => {
      const voices = speechSynthesis.getVoices();
      const want = this.tts.lang;
      const family = want.split("-")[0];
      let picked = null;
      if (this.tts.voiceName) {
        picked = voices.find((v) => v.name === this.tts.voiceName) || null;
      }
      if (!picked) {
        const exact = voices.find((v) => v.lang === want);
        const fam = voices.find((v) => v.lang.startsWith(family));
        const female = voices.find((v) => /female|google|samantha|alice|milena|microsoft\s+(zira|hazel|anna|svetlana|irina)/i.test(v.name));
        picked = exact || fam || female || voices[0] || null;
      }
      this.tts.voice = picked;
      this.tts.langOverride = picked ? picked.lang : want;
      console.info(
        "[avatar] tts voice picked:",
        this.tts.voice ? `${this.tts.voice.name} (${this.tts.voice.lang})` : "<none>",
        "for requested",
        want,
        "— total voices:",
        voices.length,
        "omnivoice:",
        this.tts.omnivoice?.enabled ? "on" : "off",
        "sample:",
        this.tts.sampleUrl || "<none>"
      );
    };
    pickVoice();
    if (speechSynthesis.onvoiceschanged !== undefined) {
      speechSynthesis.onvoiceschanged = pickVoice;
    }
  }

  _stopSpeak() {
    if (this._visemeTimer) {
      clearInterval(this._visemeTimer);
      this._visemeTimer = null;
    }
    if (this._speakSafety) {
      clearTimeout(this._speakSafety);
      this._speakSafety = null;
    }
    if (this._audioEl) {
      try {
        this._audioEl.pause();
        this._audioEl.src = "";
      } catch (_) { /* ignore */ }
      this._audioEl = null;
    }
    if (this._audioUrl) {
      URL.revokeObjectURL(this._audioUrl);
      this._audioUrl = null;
    }
    if ("speechSynthesis" in window) {
      try { speechSynthesis.cancel(); } catch (_) { /* ignore */ }
    }
    this.mouthTarget = 0;
  }

  _beginVisemes(text) {
    this._showCaption(text);
    const rollViseme = () => {
      this.viseme = VISEMES[(Math.random() * VISEMES.length) | 0];
      this.mouthTarget = 0.4 + Math.random() * 0.5;
    };
    rollViseme();
    this._visemeTimer = setInterval(rollViseme, 110 + Math.random() * 60);
  }

  _endVisemes() {
    if (this._visemeTimer) {
      clearInterval(this._visemeTimer);
      this._visemeTimer = null;
    }
    this.mouthTarget = 0;
    setTimeout(() => this._hideCaption(), 600);
  }

  /** Prefer OmniVoice (/tts); fall back to browser SpeechSynthesis. */
  async speak(text) {
    if (!text) return;
    this._stopSpeak();
    const wantOmni = this.tts?.omnivoice?.enabled !== false;
    if (wantOmni) {
      try {
        await this._speakOmni(text);
        return;
      } catch (e) {
        console.warn("[avatar] OmniVoice failed, falling back to speechSynthesis", e);
      }
    }
    this._speakBrowser(text);
  }

  async _speakOmni(text) {
    if (!this.tts) this._startTts({});
    console.info("[avatar] OmniVoice speak:", text.slice(0, 80));
    const res = await fetch("/tts", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        text,
        sampleUrl: this.tts.sampleUrl || "",
        refText: this.tts.refText || "",
        instruct: this.tts.instruct || "",
        language: this.tts.lang || "ru-RU",
        speed: this.tts.rate ?? 1,
      }),
    });
    if (!res.ok) {
      let detail = "";
      try {
        const body = await res.json();
        detail = body.error || JSON.stringify(body);
      } catch (_) {
        detail = await res.text();
      }
      throw new Error(`TTS ${res.status}: ${detail}`);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    this._audioUrl = url;
    const audio = new Audio(url);
    this._audioEl = audio;
    this._beginVisemes(text);
    const ttl = Math.max(2000, text.length * 90);
    this._speakSafety = setTimeout(() => this._endVisemes(), ttl + 8000);
    await new Promise((resolve, reject) => {
      audio.onended = () => {
        this._endVisemes();
        resolve();
      };
      audio.onerror = () => {
        this._endVisemes();
        reject(new Error("audio playback failed"));
      };
      audio.play().catch(reject);
    });
  }

  _speakBrowser(text) {
    if (!this.tts || !("speechSynthesis" in window)) return;
    try {
      const u = new SpeechSynthesisUtterance(text);
      u.lang = this.tts.langOverride || this.tts.lang;
      u.rate = this.tts.rate;
      u.pitch = this.tts.pitch;
      if (this.tts.voice) u.voice = this.tts.voice;
      console.info("[avatar] browser speak:", text.slice(0, 80));
      const ttl = Math.max(800, text.length * 70);
      let speaking = true;
      u.onstart = () => this._beginVisemes(text);
      const stop = () => {
        if (!speaking) return;
        speaking = false;
        this._endVisemes();
      };
      u.onend = stop;
      u.onerror = stop;
      speechSynthesis.speak(u);
      this._speakSafety = setTimeout(() => { if (speaking) stop(); }, ttl + 4000);
    } catch (e) { console.warn("TTS error", e); }
  }

  _showCaption(text) {
    this.caption.textContent = text.length > 160 ? text.slice(0, 157) + "…" : text;
    this.caption.classList.add("visible");
  }
  _hideCaption() {
    this.caption.classList.remove("visible");
  }

  setMood(m) { this.mood = m; }
  pulse() { /* legacy hook from old canvas avatar — not used in 3D mode */ }
  setExpression(name) {
    // Map intent → VRM expression preset weight.
    this.expression = (name || "neutral").toLowerCase();
  }

  // ─── 3D VRM ────────────────────────────────────────
  async _initTHREE(url) {
    try {
      this._disposeScene();
      this._vrmUrl = url;
      this.host3d.style.display = "";
      this.canvas.style.display = "none";

      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(this._cameraFov || 24, 4 / 3, 0.05, 50);
      camera.position.set(0, 1.25, this._cameraDistance || 1.45);
      camera.lookAt(0, 1.25, 0);

      const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      this._sizeRenderer(renderer);
      this.host3d.innerHTML = "";
      this.host3d.appendChild(renderer.domElement);
      this._renderer = renderer;

      const key = new THREE.DirectionalLight(0xffffff, 1.6);
      key.position.set(0.4, 1.5, 1.2); scene.add(key);
      const rim = new THREE.DirectionalLight(0x88aaff, 0.6);
      rim.position.set(-1, 1, -1); scene.add(rim);
      scene.add(new THREE.AmbientLight(0xffffff, 0.55));

      const loader = new GLTFLoader();
      loader.register((parser) => new VRMLoaderPlugin(parser));

      let gltf;
      try {
        gltf = await loader.loadAsync(url);
      } catch (e) {
        console.warn("VRM load failed", e);
        return false;
      }
      const vrm = gltf.userData?.vrm;
      if (!vrm) return false;
      // Do NOT call combineSkeletons here — it can leave the mesh stuck in
      // the bind T-pose while bones move underneath.
      vrm.scene.traverse((o) => { if (o.isMesh) o.frustumCulled = false; });
      VRMUtils.rotateVRM0?.(vrm);

      vrm.scene.position.y = 0;
      scene.add(vrm.scene);
      this.vrm = vrm;
      this.scene = scene;
      this.camera = camera;

      this._armDown = 1.35;
      this._armSign = 1;
      this._poseCalibrated = false;

      this._applyAPose();
      this.vrm.update(0);
      this._calibrateArmSign();
      this._applyAPose();
      this.vrm.update(0);

      this._reframe();

      console.info(
        "[avatar] VRM ready",
        "meta=", vrm.meta?.metaVersion ?? vrm.meta?.version,
        "armSign=", this._armSign,
        "bones=", ["leftUpperArm", "rightUpperArm"].map(
          (n) => `${n}:${!!vrm.humanoid?.getNormalizedBoneNode?.(n)}`
        ).join(" ")
      );

      if (!this._ro) {
        this._ro = new ResizeObserver(() => this._sizeRenderer(renderer));
        this._ro.observe(this.host3d);
      }

      if (!this._loopRunning) {
        this._loopRunning = true;
        const clock = new THREE.Clock();
        const loop = () => {
          const dt = clock.getDelta();
          this._tick(dt);
          if (this._renderer && this.scene && this.camera) {
            this._renderer.render(this.scene, this.camera);
          }
          requestAnimationFrame(loop);
        };
        requestAnimationFrame(loop);
      }
      return true;
    } catch (e) {
      console.warn("Three init failed", e);
      return false;
    }
  }

  _disposeScene() {
    if (this.vrm) {
      try { this.scene?.remove(this.vrm.scene); } catch (_) { /* ignore */ }
      this.vrm = null;
    }
    if (this._renderer) {
      try { this._renderer.dispose(); } catch (_) { /* ignore */ }
      this._renderer = null;
    }
    this.scene = null;
    this.camera = null;
  }

  _reframe() {
    if (!this.camera || !this.vrm) return;
    try {
      const yOff = this._cameraYOffset ?? 0;
      // Measure framing with the model at y=0, then slide the character.
      // If we measure after moving, lookAt tracks the offset and the slider
      // appears to do nothing.
      this.vrm.scene.position.y = 0;
      this.vrm.scene.updateMatrixWorld(true);

      const hum = this.vrm.humanoid;
      const head = hum?.getNormalizedBoneNode?.("head");
      const chest =
        hum?.getNormalizedBoneNode?.("upperChest") ||
        hum?.getNormalizedBoneNode?.("chest") ||
        hum?.getNormalizedBoneNode?.("spine");
      if (!head) {
        this.vrm.scene.position.y = yOff;
        return;
      }
      const headPos = new THREE.Vector3();
      const chestPos = new THREE.Vector3();
      head.getWorldPosition(headPos);
      if (chest) chest.getWorldPosition(chestPos);
      else chestPos.set(headPos.x, headPos.y - 0.28, headPos.z);

      const focus = headPos.clone().lerp(chestPos, 0.45);
      const dist = this._cameraDistance ?? 1.45;
      this.camera.fov = this._cameraFov ?? 24;
      this.camera.position.set(focus.x, focus.y, focus.z + dist);
      this.camera.lookAt(focus.x, focus.y, focus.z);
      this.camera.updateProjectionMatrix();

      this.vrm.scene.position.y = yOff;
      this.vrm.scene.updateMatrixWorld(true);
      this._headWorldY = headPos.y + yOff;
      this._focusY = focus.y;
    } catch (e) { /* ignore */ }
  }

  _setBoneEuler(name, x, y, z) {
    const hum = this.vrm?.humanoid;
    if (!hum) return;
    const node = hum.getNormalizedBoneNode?.(name);
    if (!node) return;
    node.rotation.set(x, y, z);
    // Force quaternion sync in case a consumer reads quaternion directly.
    node.quaternion.setFromEuler(node.rotation);
  }

  _applyAPose() {
    if (!this.vrm?.humanoid) return;
    const s = this._armSign || 1;
    const down = this._armDown || 1.35;
    // Common three-vrm convention: left +Z / right -Z brings arms down.
    // `_armSign` flips both if calibration finds the opposite convention.
    this._setBoneEuler("leftUpperArm", 0.05, 0.08, s * down);
    this._setBoneEuler("rightUpperArm", 0.05, -0.08, -s * down);
    this._setBoneEuler("leftLowerArm", 0.1, -0.2, s * 0.15);
    this._setBoneEuler("rightLowerArm", 0.1, 0.2, -s * 0.15);
    this._setBoneEuler("leftHand", 0, 0, s * 0.05);
    this._setBoneEuler("rightHand", 0, 0, -s * 0.05);
    this._setBoneEuler("leftShoulder", 0, 0, s * 0.05);
    this._setBoneEuler("rightShoulder", 0, 0, -s * 0.05);
  }

  _calibrateArmSign() {
    if (this._poseCalibrated || !this.vrm?.humanoid) return;
    const leftHand = this.vrm.humanoid.getNormalizedBoneNode?.("leftHand");
    const head = this.vrm.humanoid.getNormalizedBoneNode?.("head");
    if (!leftHand || !head) {
      this._poseCalibrated = true;
      return;
    }
    const hand = new THREE.Vector3();
    const hd = new THREE.Vector3();
    leftHand.getWorldPosition(hand);
    head.getWorldPosition(hd);
    // In a correct A-pose the hand sits below the head. If it's above,
    // we rotated the wrong way — flip and re-apply next frame.
    if (hand.y > hd.y - 0.05) {
      this._armSign = -(this._armSign || 1);
      console.info("[avatar] arm sign flipped →", this._armSign, "handY=", hand.y.toFixed(3), "headY=", hd.y.toFixed(3));
    } else {
      console.info("[avatar] arm pose OK handY=", hand.y.toFixed(3), "headY=", hd.y.toFixed(3));
    }
    this._poseCalibrated = true;
  }

  _sizeRenderer(renderer) {
    const r = this.host3d.getBoundingClientRect();
    const rend = renderer || this._renderer;
    if (r.width > 0 && r.height > 0 && rend) {
      rend.setSize(r.width, r.height, false);
      if (this.camera) {
        this.camera.aspect = r.width / r.height;
        this.camera.updateProjectionMatrix();
      }
    }
  }

  _tick(dt) {
    this.t += dt;
    // Smooth the mouth values toward target.
    this.mouthValue += (this.mouthTarget - this.mouthValue) * Math.min(1, dt * 18);

    // Idle motion targets — sampled at irregular intervals so the head
    // never sits perfectly still. Includes occasional squints and "thinking"
    // tilts so the avatar reads as alive even when not talking.
    if (this._nextIdle === undefined || this.t > this._nextIdle) {
      this._nextIdle = this.t + 2.0 + Math.random() * 3.0;
      this._headPitchTarget = (Math.random() - 0.5) * 0.18;
      this._headYawTarget = (Math.random() - 0.5) * 0.30;
      this._headRollTarget = (Math.random() - 0.5) * 0.08;
      // 30% of the time, hold a squint or a small smile for a beat.
      const r = Math.random();
      this._squintTarget = r < 0.30 ? (0.30 + Math.random() * 0.25) : 0;
      this._smileTarget = r > 0.65 ? (0.20 + Math.random() * 0.25) : 0;
    }
    this._headPitch = (this._headPitch ?? 0) + ((this._headPitchTarget ?? 0) - (this._headPitch ?? 0)) * Math.min(1, dt * 2.2);
    this._headYaw   = (this._headYaw   ?? 0) + ((this._headYawTarget   ?? 0) - (this._headYaw   ?? 0)) * Math.min(1, dt * 2.0);
    this._headRoll  = (this._headRoll  ?? 0) + ((this._headRollTarget  ?? 0) - (this._headRoll  ?? 0)) * Math.min(1, dt * 1.8);
    this._squint    = (this._squint    ?? 0) + ((this._squintTarget    ?? 0) - (this._squint    ?? 0)) * Math.min(1, dt * 3.0);
    this._smileIdle = (this._smileIdle ?? 0) + ((this._smileTarget     ?? 0) - (this._smileIdle ?? 0)) * Math.min(1, dt * 2.0);

    // Blink scheduling — a real blink is a fast spike to 1.
    this.nextBlink -= dt;
    if (this.nextBlink <= 0) {
      this.blinkValue = 1;
      this.nextBlink = 2 + Math.random() * 4;
    } else {
      this.blinkValue *= Math.max(0, 1 - dt * 8);
    }

    if (this.vrm) {
      const hum = this.vrm.humanoid;

      // A-pose first, then idle overlays on spine/head.
      this._applyAPose();

      if (hum) {
        const spine = hum.getNormalizedBoneNode?.("spine");
        if (spine) {
          spine.rotation.x = Math.sin(this.t * 1.6) * 0.012;
          spine.quaternion.setFromEuler(spine.rotation);
        }
        const head = hum.getNormalizedBoneNode?.("head");
        if (head) {
          head.rotation.x = (this._headPitch ?? 0) + Math.sin(this.t * 0.9) * 0.018;
          head.rotation.y = (this._headYaw   ?? 0) + Math.sin(this.t * 0.6) * 0.025;
          head.rotation.z = (this._headRoll  ?? 0) + Math.sin(this.t * 0.45) * 0.015;
          head.quaternion.setFromEuler(head.rotation);
        }
      }

      this.vrm.update(dt);

      if (!this._poseCalibrated) this._calibrateArmSign();

      const em = this.vrm.expressionManager;
      if (em) {
        for (const v of VISEMES) em.setValue(v, v === this.viseme ? this.mouthValue : 0);
        // Blink + squint share the eye blendshape — take the bigger value.
        const eye = Math.max(this.blinkValue, this._squint ?? 0);
        em.setValue("blink", eye);
        const exprMap = { happy: "happy", sad: "sad", angry: "angry", relaxed: "relaxed", surprised: "surprised", neutral: "neutral" };
        for (const k of Object.values(exprMap)) {
          if (k && k !== "neutral") em.setValue(k, 0);
        }
        const active = exprMap[this.expression || "neutral"];
        if (active && active !== "neutral") em.setValue(active, 0.7);
        // Layered idle smile so she doesn't look bored.
        em.setValue("happy", Math.max(em.getValue?.("happy") || 0, this._smileIdle || 0));
        if (this.mood === "thinking") em.setValue("happy", 0.18);
      }
    }
  }

  // ─── Canvas fallback ─────────────────────────────
  _startCanvasFallback() {
    this.host3d.style.display = "none";
    this.canvas.style.display = "block";
    const ctx = this.canvas.getContext("2d");
    const loop = () => {
      this._drawFallback(ctx);
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  }
  _drawFallback(c) {
    this.t += 1 / 60;
    this.mouthValue += (this.mouthTarget - this.mouthValue) * 0.18;
    const W = this.canvas.width, H = this.canvas.height;
    const g = c.createRadialGradient(W / 2, H * 0.65, 10, W / 2, H * 0.65, W * 0.8);
    g.addColorStop(0, "#221a3a"); g.addColorStop(1, "#070713");
    c.fillStyle = g; c.fillRect(0, 0, W, H);
    const cx = W / 2, cy = H * 0.55;
    const breathe = Math.sin(this.t * 1.8) * 2;
    const headR = 46 + breathe;
    c.fillStyle = "#3a3268";
    c.beginPath(); c.moveTo(cx - 80, H);
    c.bezierCurveTo(cx - 70, cy + 30, cx + 70, cy + 30, cx + 80, H);
    c.closePath(); c.fill();
    c.fillStyle = "#f7d6b6";
    c.beginPath(); c.ellipse(cx, cy, headR * 0.78, headR, 0, 0, Math.PI * 2); c.fill();
    c.fillStyle = "#1b1330";
    c.beginPath(); c.ellipse(cx, cy - headR * 0.45, headR * 0.86, headR * 0.55, 0, Math.PI, 2 * Math.PI); c.fill();
    const eyeY = cy - 4;
    c.fillStyle = "#fff";
    c.beginPath(); c.ellipse(cx - 16, eyeY, 8, 6, 0, 0, Math.PI * 2); c.fill();
    c.beginPath(); c.ellipse(cx + 16, eyeY, 8, 6, 0, 0, Math.PI * 2); c.fill();
    c.fillStyle = this.mood === "acting" ? "#4f8" : (this.mood === "thinking" ? "#fa3" : "#67e");
    c.beginPath(); c.arc(cx - 16, eyeY, 3.5, 0, Math.PI * 2); c.fill();
    c.beginPath(); c.arc(cx + 16, eyeY, 3.5, 0, Math.PI * 2); c.fill();
    const mouth = 3 + this.mouthValue * 9;
    c.fillStyle = "#9a3a5a";
    c.beginPath(); c.ellipse(cx, cy + 18, 9, mouth, 0, 0, Math.PI * 2); c.fill();
  }

  // Legacy hook for older callers (kept for compatibility).
  setIframe(url) { this.iframe.src = url; this.iframe.style.display = "block"; }
  startCanvas() { this._startCanvasFallback(); }
}
