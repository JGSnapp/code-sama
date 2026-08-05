"""OmniVoice TTS engine — voice cloning from a reference audio sample.

Uses the local ``OmniVoice/`` checkout (added to ``sys.path``) or an installed
``omnivoice`` package. The model is loaded lazily on first synthesize call so
the OS still boots without GPU / torch / HF weights. When OmniVoice is
unavailable the client falls back to browser SpeechSynthesis.
"""

from __future__ import annotations

import io
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger("code-sama-os.tts")

ROOT = Path(__file__).resolve().parent.parent
OMNIVOICE_SRC = ROOT / "OmniVoice"

# Map BCP-47 / short codes → OmniVoice language names.
_LANG = {
    "ru": "Russian",
    "ru-ru": "Russian",
    "en": "English",
    "en-us": "English",
    "en-gb": "English",
    "ja": "Japanese",
    "ja-jp": "Japanese",
    "de": "German",
    "de-de": "German",
    "fr": "French",
    "fr-fr": "French",
    "zh": "Chinese",
    "zh-cn": "Chinese",
    "ko": "Korean",
    "ko-kr": "Korean",
}


class OmniVoiceEngine:
    def __init__(self) -> None:
        self._model: Any = None
        self._lock = threading.RLock()
        self._load_error: str | None = None
        self._enabled = os.environ.get("OMNIVOICE_DISABLED", "").lower() not in (
            "1", "true", "yes",
        )
        self.model_id = os.environ.get("OMNIVOICE_MODEL", "k2-fsa/OmniVoice")
        self.num_step = int(os.environ.get("OMNIVOICE_NUM_STEP", "16"))
        self.guidance_scale = float(os.environ.get("OMNIVOICE_GUIDANCE", "2.0"))
        self._prompt_cache: dict[str, Any] = {}
        self._prompt_key: str | None = None

    @property
    def available(self) -> bool:
        return self._enabled and self._load_error is None

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled,
            "loaded": self._model is not None,
            "available": self.available,
            "model": self.model_id,
            "error": self._load_error,
            "src": str(OMNIVOICE_SRC) if OMNIVOICE_SRC.exists() else None,
            "prompt_cached": self._prompt_key is not None,
        }

    def _ensure_path(self) -> None:
        if OMNIVOICE_SRC.exists():
            p = str(OMNIVOICE_SRC)
            if p not in sys.path:
                sys.path.insert(0, p)

    def load(self) -> bool:
        """Load the OmniVoice weights. Safe to call repeatedly."""
        if not self._enabled:
            self._load_error = "disabled via OMNIVOICE_DISABLED"
            return False
        if self._model is not None:
            return True
        with self._lock:
            if self._model is not None:
                return True
            try:
                self._ensure_path()
                import torch
                from omnivoice import OmniVoice
                from omnivoice.utils.common import get_best_device

                device = os.environ.get("OMNIVOICE_DEVICE") or get_best_device()
                dtype = torch.float16 if str(device).startswith("cuda") else torch.float32
                log.info("OmniVoice: loading %s on %s (%s)…", self.model_id, device, dtype)
                self._model = OmniVoice.from_pretrained(
                    self.model_id,
                    device_map=device,
                    dtype=dtype,
                )
                self._load_error = None
                log.info("OmniVoice: ready (sr=%s)", getattr(self._model, "sampling_rate", "?"))
                return True
            except Exception as exc:
                self._load_error = f"{type(exc).__name__}: {exc}"
                log.exception("OmniVoice: failed to load")
                return False

    def _cache_key(self, ref_path: str, ref_text: str | None) -> str:
        try:
            mtime = Path(ref_path).stat().st_mtime_ns
        except OSError:
            mtime = 0
        return f"{ref_path}|{mtime}|{ref_text or ''}"

    def _get_prompt(self, ref_path: str, ref_text: str | None) -> Any | None:
        """Build or reuse a VoiceClonePrompt for faster repeated cloning."""
        key = self._cache_key(ref_path, ref_text)
        if key in self._prompt_cache:
            return self._prompt_cache[key]
        create = getattr(self._model, "create_voice_clone_prompt", None)
        if create is None:
            return None
        try:
            kwargs: dict[str, Any] = {"ref_audio": ref_path}
            if ref_text:
                kwargs["ref_text"] = ref_text
            prompt = create(**kwargs)
            self._prompt_cache.clear()
            self._prompt_cache[key] = prompt
            self._prompt_key = key
            return prompt
        except Exception:
            log.exception("OmniVoice: create_voice_clone_prompt failed — using raw ref_audio")
            return None

    def synthesize(
        self,
        text: str,
        *,
        ref_audio: str | Path | None = None,
        ref_text: str | None = None,
        language: str | None = None,
        instruct: str | None = None,
        speed: float = 1.0,
    ) -> tuple[bytes, int]:
        """Return ``(wav_bytes, sample_rate)`` for ``text``.

        Prefers voice cloning when ``ref_audio`` is set; otherwise voice
        design via ``instruct``, otherwise auto voice.
        """
        text = (text or "").strip()
        if not text:
            raise ValueError("empty text")
        if not self.load() or self._model is None:
            raise RuntimeError(self._load_error or "OmniVoice unavailable")

        lang = _normalize_lang(language)
        ref_path = str(ref_audio) if ref_audio else None
        if ref_path and not Path(ref_path).is_file():
            raise FileNotFoundError(f"ref_audio not found: {ref_path}")

        with self._lock:
            kwargs: dict[str, Any] = {
                "text": text,
                "language": lang,
                "speed": speed,
                "num_step": self.num_step,
                "guidance_scale": self.guidance_scale,
                "denoise": True,
                "postprocess_output": True,
            }
            if ref_path:
                prompt = self._get_prompt(ref_path, ref_text)
                if prompt is not None:
                    kwargs["voice_clone_prompt"] = prompt
                else:
                    kwargs["ref_audio"] = ref_path
                    if ref_text:
                        kwargs["ref_text"] = ref_text
            elif instruct:
                kwargs["instruct"] = instruct

            audios = self._model.generate(**kwargs)
            wav = audios[0]
            sr = int(getattr(self._model, "sampling_rate", None) or 24000)

        import numpy as np
        import soundfile as sf

        arr = np.asarray(wav)
        if arr.ndim > 1:
            arr = arr.squeeze()
        buf = io.BytesIO()
        sf.write(buf, arr, sr, format="WAV")
        return buf.getvalue(), sr


def _normalize_lang(language: str | None) -> str | None:
    if not language:
        return None
    key = language.strip().lower().replace("_", "-")
    if key in _LANG:
        return _LANG[key]
    short = key.split("-")[0]
    return _LANG.get(short, language)


_engine: OmniVoiceEngine | None = None


def get_tts_engine() -> OmniVoiceEngine:
    global _engine
    if _engine is None:
        _engine = OmniVoiceEngine()
    return _engine
