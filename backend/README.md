# Musicbox backend

Django handles HTTP and native views. SQLAlchemy owns the domain schema and SQLite persistence. Waitress serves the local Windows app. Pydantic validates JSON contracts.

Run the root setup first. From `backend/`:

```powershell
..\.venv\Scripts\python.exe manage.py check
..\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

In separate terminals, also from `backend/`:

```powershell
..\.venv\Scripts\python.exe -m app.worker --stage decode
..\.venv\Scripts\python.exe -m app.worker --stage clap
```

For YuE2, install assets using the root installer, set `MUSICBOX_AI_ENGINE=yue2`, and run `..\.venv\Scripts\python.exe -m app.generation_worker`. Independent terminals can set `MUSICBOX_YUE2_ROOT` to the absolute directory containing the installed `runtime.json`, `runtime/` and `models/`. The managed launcher sets this automatically.

| Module | Responsibility |
| --- | --- |
| `app/views.py`, `app/http.py` | Library, playback, sessions and request protection |
| `app/service.py`, `app/db.py` | Authorized domain operations and persistence |
| `app/recommendation_v2.py`, `app/taste_profiles.py` | Audio ranking and taste evidence |
| `app/taste_discovery.py` | Distinct taste groups and next-track discovery |
| `app/worker.py`, `app/audio.py` | Decode and embedding queues |
| `app/music_generation.py`, `app/generation_views.py` | Private generation, candidates and saves |
| `app/yue2_worker.py`, `app/yue2_runner.py` | Durable generation and isolated native processing |

Routes use `/v1/`; health is `/health`. The frontend proxies `/api/*` to Django. Private browser identity uses an HttpOnly cookie; mutations require the session's `X-CSRF-Token`. Account recovery and cross-device sync are not implemented.

From the root, run `.venv\Scripts\python.exe -m pytest -q`. Tests use temporary data and synthetic audio/process fixtures without downloaded generation models. See the [root README](../README.md) for setup and limitations.
