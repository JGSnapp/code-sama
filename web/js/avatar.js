// Real 3D VRM avatar with browser TTS + viseme-style lip-sync.
//
// Fallback chain:
//   1. AIKEYA_URL set → iframe the aikeya VRM viewer
//   2. AVATAR_VRM_URL loads → render with Three.js + @pixiv/three-vrm
//   3. anything fails → a friendly 2D canvas portrait keeps the panel alive

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
  }

  configure(cfg) {
    if (cfg.aikeyaUrl) {
      this.iframe.src = cfg.aikeyaUrl;
      this.iframe.style.display = "block";
      this.host3d.style.display = "none";
      this.canvas.style.display = "none";
      this._startTts(cfg);
      return;
    }
    this._initTHREE(cfg.avatarVrmUrl).then((ok) => {
      if (!ok) this._startCanvasFallback();
    });
    this._startTts(cfg);
  }

  // ─── TTS ────────────────────────────────────────────
  _startTts(cfg) {
    if (!("speechSynthesis" in window)) {
      console.warn("[avatar] speechSynthesis not supported in this browser");
      return;
    }
    this.tts = {
      lang: cfg.ttsLang || "ru-RU",
      rate: cfg.ttsRate || 1.0,
      pitch: cfg.ttsPitch || 1.0,
      voice: null,
    };
    const pickVoice = () => {
      const voices = speechSynthesis.getVoices();
      const want = this.tts.lang;
      const family = want.split("-")[0];
      const exact = voices.find((v) => v.lang === want);
      const fam = voices.find((v) => v.lang.startsWith(family));
      const female = voices.find((v) => /female|google|samantha|alice|milena|microsoft\s+(zira|hazel|anna|svetlana|irina)/i.test(v.name));
      this.tts.voice = exact || fam || female || voices[0] || null;
      // If we have voices but none in the requested language, drop the lang
      // hint so the chosen voice actually plays instead of silently failing.
      this.tts.langOverride = (exact || fam) ? want : (this.tts.voice ? this.tts.voice.lang : null);
      console.info(
        "[avatar] tts voice picked:",
        this.tts.voice ? `${this.tts.voice.name} (${this.tts.voice.lang})` : "<none>",
        "for requested",
        want,
        "— total voices:",
        voices.length
      );
    };
    pickVoice();
    if (speechSynthesis.onvoiceschanged !== undefined) {
      speechSynthesis.onvoiceschanged = pickVoice;
    }
  }

  speak(text) {
    if (!this.tts || !("speechSynthesis" in window) || !text) return;
    try {
      speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text);
      // Use the language of the actually-picked voice, not the configured
      // one — otherwise some browsers refuse to play if the lang doesn't
      // match any installed voice.
      u.lang = this.tts.langOverride || this.tts.lang;
      u.rate = this.tts.rate;
      u.pitch = this.tts.pitch;
      if (this.tts.voice) u.voice = this.tts.voice;
      console.info("[avatar] speak:", text.slice(0, 80));
      const ttl = Math.max(800, text.length * 70);
      let speaking = true;
      let visemeTimer = null;
      const rollViseme = () => {
        this.viseme = VISEMES[(Math.random() * VISEMES.length) | 0];
        this.mouthTarget = 0.4 + Math.random() * 0.5;
      };
      u.onstart = () => {
        this._showCaption(text);
        rollViseme();
        visemeTimer = setInterval(rollViseme, 110 + Math.random() * 60);
      };
      const stop = () => {
        speaking = false;
        clearInterval(visemeTimer);
        this.mouthTarget = 0;
        setTimeout(() => this._hideCaption(), 600);
      };
      u.onend = stop;
      u.onerror = stop;
      speechSynthesis.speak(u);
      // safety: some voices never fire onend
      setTimeout(() => { if (speaking) stop(); }, ttl + 4000);
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
      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(16, 4 / 3, 0.05, 50);
      camera.position.set(0, 1.40, 1.4);
      camera.lookAt(0, 1.38, 0);

      const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      this._sizeRenderer(renderer);
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
      VRMUtils.removeUnnecessaryVertices?.(vrm.scene);
      VRMUtils.combineSkeletons?.(vrm.scene);
      vrm.scene.traverse((o) => { if (o.isMesh) o.frustumCulled = false; });
      // VRM 1.0 models face +Z by default, VRM 0.x are auto-rotated by
      // three-vrm. We let three-vrm pick the right orientation and just
      // place the camera in front of the avatar (negative Z, looking +Z).
      vrm.scene.position.y = 0;
      scene.add(vrm.scene);
      this.vrm = vrm;
      this.scene = scene;
      this.camera = camera;

      // Default T-pose looks weird in a "webcam" framing. Force arms into a
      // relaxed A-pose. We re-apply this every frame in _tick because
      // vrm.update() copies normalized bone rotations onto the raw bones.
      this._pose = null;

      // List of bones we want collapsed for the webcam crop. Re-applied
      // every frame in _tick because vrm.update() resets bone transforms.
      this._collapseBones = [
        "leftShoulder", "rightShoulder",
        "leftUpperArm", "rightUpperArm",
        "leftLowerArm", "rightLowerArm",
        "leftHand", "rightHand",
      ];

      // Hide meshes likely to be arm-related so the webcam shot stays clean.
      // Two passes:
      //   • Names matching "arm/sleeve/glove/shoulder/pauldron/hand/wrist"
      //   • Skinned meshes whose local bounding box extends past ±0.30m
      //     horizontally (T-pose arms always do; centered-torso meshes don't)
      //     AND that aren't part of the head/hair group.
      const HIDE_PATTERNS = /(arm|sleeve|glove|shoulder|pauldron|hand|wrist)/i;
      const KEEP_PATTERNS = /(head|hair|face|eye|brow|mouth|tongue|tooth|ear|neck|chest|torso|body)/i;
      try {
        vrm.scene.traverse((obj) => {
          const n = obj.name || "";
          if (HIDE_PATTERNS.test(n) && !KEEP_PATTERNS.test(n)) {
            obj.visible = false;
            return;
          }
          if (obj.isSkinnedMesh && !KEEP_PATTERNS.test(n)) {
            obj.geometry?.computeBoundingBox?.();
            const bb = obj.geometry?.boundingBox;
            if (!bb) return;
            const extent = Math.max(Math.abs(bb.min.x), Math.abs(bb.max.x));
            // The torso clothing mostly stays inside ±0.20; pure arm/sleeve
            // meshes extend past 0.30. We hide ONLY the high-X half by
            // setting a clip plane indirectly — simplest: just hide the
            // whole mesh when it extends well past the shoulder line.
            if (extent > 0.32) {
              obj.visible = false;
            }
          }
        });
      } catch (e) { /* ignore */ }

      // Auto-frame: aim the camera in front of the head bone, close enough
      // that the arms (still in T-pose) are out of frame on the sides.
      try {
        const head = vrm.humanoid?.getNormalizedBoneNode?.("head");
        if (head) {
          const wp = new THREE.Vector3();
          head.getWorldPosition(wp);
          // Webcam framing — head + shoulders, ~50% of the panel. Arms are
          // collapsed (see _initTHREE) so we don't worry about FOV here.
          camera.position.set(wp.x, wp.y + 0.07, wp.z + 1.4);
          camera.lookAt(wp.x, wp.y + 0.05, wp.z);
          this._headWorldY = wp.y;
        }
      } catch (e) { /* ignore */ }

      // Resize handling.
      const ro = new ResizeObserver(() => this._sizeRenderer(renderer));
      ro.observe(this.host3d);

      const clock = new THREE.Clock();
      const loop = () => {
        const dt = clock.getDelta();
        this._tick(dt);
        renderer.render(scene, camera);
        requestAnimationFrame(loop);
      };
      requestAnimationFrame(loop);
      return true;
    } catch (e) {
      console.warn("Three init failed", e);
      return false;
    }
  }

  _sizeRenderer(renderer) {
    const r = this.host3d.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) {
      renderer.setSize(r.width, r.height, false);
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
      // Re-apply any pose offsets each frame so vrm.update doesn't
      // snap them back to the rest pose.
      if (this._pose) {
        const hum = this.vrm.humanoid;
        for (const [name, rot] of Object.entries(this._pose)) {
          const node = hum?.getNormalizedBoneNode?.(name);
          if (!node) continue;
          if (rot.x !== undefined) node.rotation.x = rot.x;
          if (rot.y !== undefined) node.rotation.y = rot.y;
          if (rot.z !== undefined) node.rotation.z = rot.z;
        }
      }
      // Re-collapse arm bones EVERY frame so vrm.update can't restore
      // them. Costs about a dozen Vector3 writes — cheap.
      if (this._collapseBones) {
        const hum = this.vrm.humanoid;
        for (const n of this._collapseBones) {
          const node = hum?.getNormalizedBoneNode?.(n);
          if (node) node.scale.set(0.001, 0.001, 0.001);
        }
      }
      this.vrm.update(dt);
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
      // Breathe / idle sway + head movements toward the smoothed targets.
      const hum = this.vrm.humanoid;
      if (hum) {
        const spine = hum.getNormalizedBoneNode?.("spine");
        if (spine) spine.rotation.x = Math.sin(this.t * 1.6) * 0.012;
        const head = hum.getNormalizedBoneNode?.("head");
        if (head) {
          // micro tremor + smoothed pose target
          head.rotation.x = (this._headPitch ?? 0) + Math.sin(this.t * 0.9) * 0.018;
          head.rotation.y = (this._headYaw   ?? 0) + Math.sin(this.t * 0.6) * 0.025;
          head.rotation.z = (this._headRoll  ?? 0) + Math.sin(this.t * 0.45) * 0.015;
        }
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
