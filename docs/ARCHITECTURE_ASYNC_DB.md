# Async routes and synchronous SQLAlchemy (AUD-044)

ARIA uses **FastAPI with synchronous SQLAlchemy `Session` objects**. This is the supported production pattern. A full async-SQLAlchemy rewrite is not planned.

## Pattern

1. Use **`def` route handlers** for DB-heavy work that does not await I/O (list/search/dashboard/requisition GET, queue status).
2. Keep **`async def` only** when the handler genuinely awaits:
   - file reads (`UploadFile.read`)
   - outbound HTTP
   - LLM/model calls
   - streaming responses
   - async queue enqueue
3. FastAPI runs sync `def` handlers in a thread pool, so a slow DB request should not block unrelated async I/O routes.
4. **Never share one `Session` across worker threads.** Each request gets its own session from `get_db()` / `get_read_db()`. Do not pass that session into `asyncio.to_thread`.
5. Mixed handlers keep async I/O in the async function and keep long CPU/DB work in sync helpers called on the same request thread (or via `def` sub-routes), not via a shared session in `to_thread`.

## High-volume routes

| Area | Pattern |
|---|---|
| Dashboard / analytics GET | sync `def` |
| Candidates list/search | sync `def` |
| Requisitions list/get | sync `def` |
| Queue GET status/list | sync `def` |
| Analyze / queue submit | `async def` (file + enqueue) |
| Interviews create/stream | `async def` where I/O is required |
| Billing webhook | `async def` for `request.body()`, sync DB helpers |

Internal probes used in tests: `GET /api/internal/async-probe` (async) and `GET /api/internal/sync-sleep` (sync, testing only).
