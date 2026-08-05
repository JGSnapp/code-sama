"""VRoid Studio adapter for code-sama-os.

Locates a local VRoid Studio install, launches it, watches a drop folder for
``.vrm`` exports, validates VRM 0.x / 1.0 metadata, and installs models into
``uploads/vrm/`` so the avatar can load them via ``/uploads/...``.
"""

from __future__ import annotations

import json
import logging
import os
import struct
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("code-sama-os.vroid")

ROOT = Path(__file__).resolve().parent.parent
UPLOAD_VRM = ROOT / "uploads" / "vrm"
CHARACTERS = ROOT / "characters"
WATCH_DIR = ROOT / "characters" / "export_drop"

# Common Windows install + shortcut locations.
_DEFAULT_STUDIO_CANDIDATES = [
    Path(os.environ.get("VROID_STUDIO_EXE", "")),
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "VRoidStudio" / "2.12.0" / "VRoidStudio.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "VRoidStudio" / "VRoidStudio.exe",
    Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "VRoidStudio" / "VRoidStudio.exe",
    Path.home() / "Desktop" / "VRoidStudio 2.12.0.lnk",
]


class VRoidAdapter:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or ROOT
        self.upload_dir = self.root / "uploads" / "vrm"
        self.characters_dir = self.root / "characters"
        self.watch_dir = self.characters_dir / "export_drop"
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.characters_dir.mkdir(parents=True, exist_ok=True)
        self.watch_dir.mkdir(parents=True, exist_ok=True)
        self._studio: Path | None = None
        self._watch_stop = threading.Event()
        self._watch_thread: threading.Thread | None = None
        self._last_installed: dict[str, Any] | None = None

    # ─── discovery ────────────────────────────────────────────────────
    def find_studio(self) -> Path | None:
        if self._studio and self._studio.exists():
            return self._studio
        for cand in _DEFAULT_STUDIO_CANDIDATES:
            if not cand or not str(cand).strip() or str(cand).strip() in (".", "./"):
                continue
            resolved = _resolve_shortcut(cand) if cand.suffix.lower() == ".lnk" else cand
            if resolved and resolved.is_file() and resolved.suffix.lower() == ".exe":
                self._studio = resolved
                return resolved
        # Scan versioned installs under Local\\Programs\\VRoidStudio\\*
        base = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "VRoidStudio"
        if base.is_dir():
            for child in sorted(base.iterdir(), reverse=True):
                exe = child / "VRoidStudio.exe"
                if exe.exists():
                    self._studio = exe
                    return exe
        return None

    def status(self) -> dict[str, Any]:
        studio = self.find_studio()
        installed = [
            {
                "name": p.name,
                "url": f"/uploads/vrm/{p.name}",
                "bytes": p.stat().st_size,
                "meta": inspect_vrm(p),
            }
            for p in sorted(self.upload_dir.glob("*.vrm"))
        ]
        desktop_hits = []
        desk = Path.home() / "Desktop"
        if desk.is_dir():
            for p in desk.glob("*.vrm"):
                desktop_hits.append({"path": str(p), "bytes": p.stat().st_size, "meta": inspect_vrm(p)})
        return {
            "studio": str(studio) if studio else None,
            "studio_found": studio is not None,
            "watch_dir": str(self.watch_dir),
            "watching": self._watch_thread is not None and self._watch_thread.is_alive(),
            "installed": installed,
            "desktop_vrms": desktop_hits,
            "last_installed": self._last_installed,
        }

    # ─── launch / install ─────────────────────────────────────────────
    def launch(self) -> dict[str, Any]:
        studio = self.find_studio()
        if not studio:
            return {"ok": False, "error": "VRoid Studio not found. Set VROID_STUDIO_EXE."}
        try:
            subprocess.Popen(
                [str(studio)],
                cwd=str(studio.parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "studio": str(studio),
            "hint": (
                f"В VRoid: File → Export → VRM → сохрани в {self.watch_dir} "
                "или на Desktop как Code-sama.vrm, затем Install."
            ),
            "watch_dir": str(self.watch_dir),
        }

    def install(
        self,
        source: str | Path,
        *,
        name: str = "code-sama.vrm",
        also_characters: bool = True,
    ) -> dict[str, Any]:
        src = Path(source).expanduser()
        if not src.is_file():
            return {"ok": False, "error": f"file not found: {src}"}
        if src.suffix.lower() != ".vrm":
            return {"ok": False, "error": "expected a .vrm file"}
        meta = inspect_vrm(src)
        if meta.get("error"):
            return {"ok": False, "error": meta["error"], "meta": meta}

        safe = _safe_name(name if name.endswith(".vrm") else f"{name}.vrm")
        dest = self.upload_dir / safe
        dest.write_bytes(src.read_bytes())
        if also_characters:
            (self.characters_dir / safe).write_bytes(dest.read_bytes())

        url = f"/uploads/vrm/{safe}"
        result = {
            "ok": True,
            "url": url,
            "path": str(dest),
            "bytes": dest.stat().st_size,
            "meta": meta,
        }
        self._last_installed = result
        log.info("VRoid install: %s → %s (%s bytes)", src, dest, result["bytes"])
        return result

    def install_from_desktop(self, prefer: str = "Code-sama.vrm") -> dict[str, Any]:
        desk = Path.home() / "Desktop"
        preferred = desk / prefer
        if preferred.is_file():
            return self.install(preferred, name="code-sama.vrm")
        # Fallbacks
        for cand in ("Code-sama.vrm", "code-sama.vrm", "Code-sama-nu.vrm"):
            p = desk / cand
            if p.is_file():
                return self.install(p, name="code-sama.vrm")
        vrms = sorted(desk.glob("*.vrm"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not vrms:
            return {"ok": False, "error": "no .vrm on Desktop"}
        return self.install(vrms[0], name="code-sama.vrm")

    # ─── watch folder ─────────────────────────────────────────────────
    def start_watch(self, on_install: Any | None = None) -> dict[str, Any]:
        if self._watch_thread and self._watch_thread.is_alive():
            return {"ok": True, "watching": True, "watch_dir": str(self.watch_dir)}
        self._watch_stop.clear()
        seen: dict[str, float] = {}

        def _loop() -> None:
            while not self._watch_stop.is_set():
                try:
                    for p in self.watch_dir.glob("*.vrm"):
                        key = str(p)
                        mtime = p.stat().st_mtime
                        if seen.get(key) == mtime:
                            continue
                        # Wait until file size is stable (export finishing).
                        time.sleep(0.6)
                        if not p.exists():
                            continue
                        size1 = p.stat().st_size
                        time.sleep(0.4)
                        if not p.exists() or p.stat().st_size != size1:
                            continue
                        seen[key] = mtime
                        result = self.install(p, name=p.name)
                        if on_install and result.get("ok"):
                            try:
                                on_install(result)
                            except Exception:
                                log.exception("on_install callback failed")
                except Exception:
                    log.exception("vroid watch loop error")
                self._watch_stop.wait(1.5)

        self._watch_thread = threading.Thread(target=_loop, name="vroid-watch", daemon=True)
        self._watch_thread.start()
        return {"ok": True, "watching": True, "watch_dir": str(self.watch_dir)}

    def stop_watch(self) -> dict[str, Any]:
        self._watch_stop.set()
        t = self._watch_thread
        if t and t.is_alive():
            t.join(timeout=2.0)
        self._watch_thread = None
        return {"ok": True, "watching": False}


def inspect_vrm(path: Path | str) -> dict[str, Any]:
    """Read GLB JSON chunk and pull VRM 0/1 metadata without full parse."""
    p = Path(path)
    try:
        data = p.read_bytes()
    except Exception as exc:
        return {"error": f"read failed: {exc}"}
    if data[:4] != b"glTF":
        return {"error": "not a GLB/VRM (missing glTF magic)"}
    try:
        off = 12
        length, ctype = struct.unpack_from("<I4s", data, off)
        if ctype != b"JSON":
            return {"error": f"unexpected first chunk {ctype!r}"}
        chunk = data[off + 8 : off + 8 + length]
        j = json.loads(chunk)
    except Exception as exc:
        return {"error": f"parse failed: {exc}"}

    used = list(j.get("extensionsUsed") or [])
    exts = j.get("extensions") or {}
    vrm1 = exts.get("VRMC_vrm")
    vrm0 = exts.get("VRM")
    spec = "1.0" if vrm1 else ("0.x" if vrm0 else "unknown")
    meta: dict[str, Any] = {"spec": spec, "extensions": used}
    block = vrm1 or vrm0 or {}
    m = block.get("meta") if isinstance(block, dict) else None
    if isinstance(m, dict):
        meta["title"] = m.get("name") or m.get("title")
        authors = m.get("authors") or m.get("author")
        meta["author"] = authors
        meta["version"] = m.get("version")
    exprs = block.get("expressions") if isinstance(block, dict) else None
    if isinstance(exprs, dict):
        preset = exprs.get("preset") or exprs.get("presetMap") or {}
        if isinstance(preset, dict):
            meta["expressions"] = sorted(preset.keys())
        else:
            meta["expressions"] = []
    meta["materials"] = len(j.get("materials") or [])
    meta["meshes"] = len(j.get("meshes") or [])
    return meta


def _safe_name(name: str) -> str:
    base = Path(name).name
    keep = "".join(c if c.isalnum() or c in "._-" else "_" for c in base)
    if not keep.lower().endswith(".vrm"):
        keep += ".vrm"
    return keep or "avatar.vrm"


def _resolve_shortcut(lnk: Path) -> Path | None:
    """Resolve a Windows .lnk via PowerShell (no extra deps)."""
    if not lnk.exists():
        return None
    try:
        cmd = (
            f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
            f"Write-Output $s.TargetPath"
        )
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", cmd],
            text=True,
            timeout=10,
        ).strip()
        if out:
            p = Path(out)
            return p if p.exists() else None
    except Exception:
        log.exception("shortcut resolve failed: %s", lnk)
    return None


_adapter: VRoidAdapter | None = None


def get_vroid_adapter() -> VRoidAdapter:
    global _adapter
    if _adapter is None:
        _adapter = VRoidAdapter()
    return _adapter
