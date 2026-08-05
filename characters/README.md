# Characters / VRoid

Local unique avatar pipeline for **code-sama**.

## Model

- `code-sama.vrm` — VRM 1.0 (`VRMC_vrm`), author JGSnapp, exported from VRoid Studio 2.12.
- Served at `/uploads/vrm/code-sama.vrm` and selected in Settings → Персонаж.

## Adapter

`server/vroid_adapter.py` + HTTP:

| Endpoint | Action |
|---|---|
| `GET /vroid/status` | Studio path, installed VRMs, Desktop `.vrm` |
| `POST /vroid/launch` | Start VRoid Studio |
| `POST /vroid/install` | `{ fromDesktop: true }` or `{ path: "…" }` |
| `POST /vroid/watch/start` | Auto-install anything dropped into `export_drop/` |
| `POST /vroid/watch/stop` | Stop watcher |

CLI:

```powershell
.\.venv\Scripts\python.exe tools\vroid\cli.py status
.\.venv\Scripts\python.exe tools\vroid\cli.py install-desktop
.\.venv\Scripts\python.exe tools\vroid\cli.py launch
```

## Re-export workflow

1. Settings → Персонаж → **Открыть VRoid Studio** (or the Desktop shortcut).
2. Edit the character, **File → Export → VRM**.
3. Save as `Desktop\Code-sama.vrm` **or** into `characters/export_drop/` (with Watch on).
4. Click **Взять Code-sama.vrm с Desktop** (or wait for watch) — avatar hot-reloads.
