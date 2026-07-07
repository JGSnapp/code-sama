"""Registry of installed app manifests.

The registry is the loader's catalogue. It scans a directory on disk
(``APPS_DIR``) and keeps the parsed manifests in memory. Apps are
addressed by their ``app`` slug (the key in the YAML front matter), so
``app_open("firefox")`` -> find the ``firefox`` manifest -> pass to the
Runtime.

State on disk looks like::

    apps/
      firefox.manifest.yml
      gimp.manifest.yml
      generated/
        inkscape.manifest.json
        audacity.manifest.json

The registry is file-backed so it survives process restarts. ``install``
just writes a new manifest file and re-scans.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from .manifest import AppManifest, load_manifest


log = logging.getLogger("code-sama-os.loader.registry")


def _default_apps_dir() -> Path:
    """Where installed manifests live.

    Prefer ``$WORKSPACE_DIR/apps`` (so they persist in the Docker volume),
    fall back to ``./apps`` next to the repo root.
    """
    ws = os.environ.get("WORKSPACE_DIR")
    if ws:
        return Path(ws) / "apps"
    return Path(__file__).resolve().parent.parent.parent / "apps"


class AppRegistry:
    """In-memory cache of installed :class:`AppManifest` objects."""

    def __init__(self, apps_dir: str | Path | None = None) -> None:
        self.apps_dir = Path(apps_dir) if apps_dir else _default_apps_dir()
        self._apps: dict[str, AppManifest] = {}
        self._lock = asyncio.Lock()

    # ─── discovery ────────────────────────────────────────────────────
    def scan(self) -> None:
        """(Re)scan ``apps_dir`` and rebuild the cache."""
        self._apps.clear()
        self.apps_dir.mkdir(parents=True, exist_ok=True)
        for p in sorted(self.apps_dir.rglob("*")):
            if not p.is_file():
                continue
            if p.suffix.lower() not in {".yml", ".yaml", ".json"}:
                continue
            if p.name.startswith("."):
                continue
            try:
                m = load_manifest(p)
            except Exception:
                log.exception("failed to parse manifest %s", p)
                continue
            if not m.app:
                log.warning("manifest %s has no `app` field — skipping", p)
                continue
            self._apps[m.app] = m
        log.info("loader registry: %d app(s) from %s", len(self._apps), self.apps_dir)

    def list(self) -> list[AppManifest]:
        if not self._apps:
            self.scan()
        return sorted(self._apps.values(), key=lambda m: m.app)

    def get(self, app: str) -> AppManifest | None:
        if not self._apps:
            self.scan()
        return self._apps.get(app)

    def describe(self, app: str) -> dict[str, Any] | None:
        m = self.get(app)
        return m.to_dict() if m else None

    def has(self, app: str) -> bool:
        return self.get(app) is not None

    # ─── mutation ─────────────────────────────────────────────────────
    async def install(self, manifest: AppManifest, *, overwrite: bool = True) -> Path:
        """Persist a manifest to disk and refresh the cache."""
        async with self._lock:
            if not manifest.app:
                raise ValueError("manifest.app is required")
            self.apps_dir.mkdir(parents=True, exist_ok=True)
            subdir = self.apps_dir
            if manifest.source == "generated":
                subdir = self.apps_dir / "generated"
                subdir.mkdir(parents=True, exist_ok=True)
            ext = ".json" if (manifest.path or "").endswith(".json") else ".yml"
            target = subdir / f"{manifest.app}.manifest{ext}"
            if target.exists() and not overwrite:
                raise FileExistsError(target)
            payload = manifest.to_dict()
            if target.suffix == ".json":
                import json
                target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            else:
                try:
                    import yaml  # type: ignore
                except ImportError:
                    # JSON fallback if pyyaml missing.
                    import json
                    target = target.with_suffix(".json")
                    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                else:
                    target.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
            manifest.path = str(target)
            self._apps[manifest.app] = manifest
            log.info("loader registry: installed %s -> %s", manifest.app, target)
            return target

    async def uninstall(self, app: str) -> bool:
        async with self._lock:
            m = self._apps.pop(app, None)
            if not m or not m.path:
                return False
            try:
                Path(m.path).unlink(missing_ok=True)
            except Exception:
                log.exception("failed to unlink %s", m.path)
            log.info("loader registry: uninstalled %s", app)
            return True

    def summary(self) -> list[dict[str, Any]]:
        return [
            {
                "app": m.app,
                "title": m.title,
                "icon": m.icon,
                "description": m.description,
                "version": m.version,
                "source": m.source,
                "actions": sorted((m.actions or {}).keys()),
                "installed": bool(m.path),
            }
            for m in self.list()
        ]
