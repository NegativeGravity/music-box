# Musicbox frontend

Next.js 16.4, React 19 and TypeScript. A responsive player, library browsing, personalized discovery and a private AI Studio.

From `frontend/`:

```powershell
pnpm install --frozen-lockfile
pnpm dev
```

Run Django at `http://127.0.0.1:8000` with a decode worker. Next.js proxies `/api/*` to Django. Set `API_ORIGIN` before building to change the backend address.

| Path | Responsibility |
| --- | --- |
| `app/` | App Router entry points, layout and styles |
| `src/Musicbox.tsx` | Library, playback, favorites, queues and session state |
| `src/DiscoveryPanel.tsx` | Taste mixes and next-track discovery |
| `src/AIStudioPanel.tsx` | Prompt, lyrics, references and generation |
| `src/GenerationVariations.tsx` | Candidate progress, audition and selection |
| `src/lib/` | API contracts and client helpers |
| `tests/e2e/` | Browser checks, including synthetic Studio responses |

```powershell
pnpm typecheck
pnpm build
pnpm start
pnpm test:e2e
```

Typecheck after setup/build so Next.js has generated its types. Browser tests use installed Google Chrome and require the running app with its demo library. Set `PLAYWRIGHT_BASE_URL` for another port. See the [root README](../README.md) for managed startup and generation limitations.
