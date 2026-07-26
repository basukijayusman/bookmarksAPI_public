# Build Plan: Bookmarks API

This file tracks how the project will be built. Testing is done in parallel via a TDD
approach, and the CI/CD scripts folder runs `commit.sh` to run the suite before committing.

Verify each stage before committing:

```powershell
bash scripts\test.sh          # or: .venv\Scripts\python.exe -m pytest
```

## Stage 0: Project scaffold [✔]

**Goal:** a runnable, dependency-managed project skeleton with nothing leaking into git.

- [x] `requirements.txt` (fastapi, uvicorn, sqlalchemy, alembic, pydantic[email], pyjwt, bcrypt, python-dotenv; dev: pytest, httpx, jsonschema)
- [x] `pyproject.toml` with `pythonpath = ["."]` so pytest finds the `app` package
- [x] `.gitignore` **before** the first `git add` (`.venv/`, `__pycache__/`, `*.db`, `.env`, caches)
- [x] `.env.example` documenting `SECRET_KEY` and `DATABASE_URL`
- [x] `app/__init__.py` (empty for now)

**Verify:** `python -m venv .venv` then install succeeds; `git status` shows no `.env`, `.venv`, or `*.db`.

---

## Stage 1: Data layer [✔]

**Goal:** the three tables and their validation, testable with no web server.

- [x] `app/db.py`: engine, `SessionLocal`, declarative `Base`, `get_db` dependency
- [x] `app/models.py`: `User`, `Tag`, `Bookmark`, `bookmark_tags` association table
- [x] `app/schemas.py`: Pydantic request/response models
- [x] `tests/test_models_schemas.py`: model + schema tests in isolation
- [x] `tests/test_db.py`: engine, session factory, and `get_db` tests

**Key decisions:**
- Many-to-many tags via an association table with a composite primary key (the pair is the identity, no surrogate id).
- Tags are global and unique by name, so `python` is one shared row. Per-user counts must join through `bookmarks`.
- `lazy="selectin"` on `Bookmark.tags` so listing avoids the N+1.
- Ability to fetch a bookmark without loading its tags.
- `default=utcnow` passes the *function*, not `utcnow()`, so each row gets its own timestamp.

**Verify:** `bash scripts/test.sh models` (7 tests: tag normalization, invalid URL, required title, defaults, persistence, m2m ordering, serialization) and `bash scripts/test.sh db` (5 tests) both green.

---

## Stage 2: Migrations [✔]

**Goal:** build the schema from versioned migrations, not `create_all`.

- [x] `alembic.ini` with `script_location = migrations` and `prepend_sys_path = .`
- [x] `migrations/env.py`: imports `app.models`, pulls `DATABASE_URL` from `app.db` (single source of truth, no hardcoded URL)
- [x] `migrations/script.py.mako`: revision template
- [x] `migrations/versions/0001_initial.py`: `CREATE TABLE` for all four tables, indexes, FKs with `ondelete="CASCADE"`
- [x] `tests/test_migrations.py`: runs the `alembic` console script against a temp DB
- [x] `seed.py`: idempotent (clears the demo user first), reproducible sample bookmarks

**Key decisions:**
- `prepend_sys_path = .` so the `alembic` console script can `import app` (the module form works without it; the console script does not).
- `downgrade()` drops tables in reverse order because of the foreign keys.

**Verify:** `bash scripts/test.sh migrations` green; `bash scripts/migrate.sh` runs clean and the four tables plus `alembic_version` exist.
            `bash scripts/seed.sh` twice in a row leaves the same result.
**Note:** could have done with auto sync :)

---

## Stage 3: Error envelope [✔]

**Goal:** one consistent JSON error shape for every failure, before anything can raise.

- [x] `app/errors.py`: an `ApiError` exception + handlers for `RequestValidationError`, `HTTPException`, `ApiError`, and any unhandled exception. All emit `{"error": {"code", "message", "details"}}`.

**Key decisions:**
- Built before auth and routes because both import `ApiError`.
- The `ErrorOut` schema is registered in OpenAPI so the documented shape and the real one match.

**Verify:** `bash scripts/test.sh api` (no dedicated test file for this layer; every error case in the API tests asserts the envelope).

---

## Stage 4: Auth (registration + login + JWT) [✔]

**Goal:** issue and verify JWTs; scope every later endpoint to the caller.

- [x] `app/auth.py`: bcrypt hashing, `make_token`/verify, `current_user` dependency, `SECRET_KEY` load (fatal if missing), rate limiter
- [x] Auth schemas in `app/schemas.py` (`RegisterIn`, `LoginIn`, `AuthOut`, `UserOut`)
- [x] Register + login routes (can live in `routes.py` at Stage 5)
- [x] `tests/test_auth.py`: hashing, JWT, `current_user`, and rate-limiter tests

**Key decisions:**
- Hashing (bcrypt), never encryption. Password capped at 72 bytes (bcrypt truncates past that).
- `SECRET_KEY` is mandatory and >= 32 chars; a generated fallback would break tokens across uvicorn workers.
- Login hashes against a dummy hash when the email is unknown, so timing does not reveal which accounts exist.
- `current_user` keeps token verification out of route bodies.

**Verify:** `bash scripts/test.sh auth`; register returns 201 + token, login returns 200, bad password and unknown email both return the same 401.

---

## Stage 5: Bookmark CRUD [✔]

**Goal:** full create/read/update/delete, every query scoped to the owner.

- [x] `app/routes.py`: `POST/GET/PUT/DELETE /api/bookmarks`, tag get-or-create, ownership scoping
- [x] `app/main.py`: assemble the app, install error handlers, include routers, health check
- [x] `tests/conftest.py`: in-memory SQLite via `StaticPool` + `get_db` override (the API test harness)
- [x] `tests/test_api.py`: register/login, auth required, ownership scoping, tag normalization, CRUD round-trip

**Key decisions:**
- Ownership failures return 404, not 403, so IDs cannot be probed.
- `resolve_tags` relies on the unique index and retries once on `IntegrityError` (the read alone is a race).
  It runs *before* the bookmark is added or mutated, because the retry rolls the session back.
- `PUT` replaces tags wholesale; no `PATCH` until partial updates are actually needed.
- Registration lets the unique index reject duplicates (409) instead of pre-checking with a `SELECT`,
  which two concurrent signups would both pass.
- `main.py` calls `load_dotenv()` before importing the routers: `app.auth` reads `SECRET_KEY` at import time.
- `conftest.py` clears the rate limiter per test; it is process-global and every request comes from `testclient`.
- `GET /api/bookmarks` already returns the `BookmarkPage` envelope, so Stage 6 fills it in rather than
  changing the response shape.

**Verify:** `bash scripts/test.sh api`; create, fetch, update, delete round-trip; a second user gets 404 for the first user's bookmark.

---

## Stage 6: Search, filter, pagination, stats

**Goal:** query the list endpoint; one raw-SQL aggregate endpoint.

- [ ] Query params on `GET /api/bookmarks`: `tag`, `q`, `from`, `to`, `page`, `per_page`
- [ ] `GET /api/bookmarks/stats` using raw SQL (declared **before** `/{bookmark_id}` so "stats" is not parsed as an id)
- [ ] extend `tests/test_api.py`: search, filter, date range, pagination totals, stats

**Key decisions:**
- `total` uses a separate count query so pagination reports the full size, not the page size.
- `total_tags` counts distinct tags on the caller's bookmarks, never `COUNT(*) FROM tags`.
- `per_page` is capped (`le=100`) so a client cannot request an unbounded page.

**Verify:** `bash scripts/test.sh api`; filter by tag/keyword/date narrows results, `stats` totals match only the caller's data.

---

## Stage 7: OpenAPI documentation

**Goal:** complete, accurate interactive docs.

- [ ] Confirm `/docs` and `/openapi.json` render (free with FastAPI, generated from the schemas)
- [ ] Response models and auth requirements show correctly per endpoint

**Key decisions:**
- The spec is generated from Pydantic models, so it cannot drift from the code.
- `HTTPBearer` security scheme documents the token requirement.

**Verify:** `bash scripts/test.sh api` (contract checks pass); open `/docs`, click Authorize, exercise a protected endpoint.

---

## Stage 8: OpenAPI contract conformance

**Goal:** assert that real API responses match the served OpenAPI spec. The API tests
themselves are written in Stages 5 and 6 (parallel TDD); this stage adds only the contract
layer, which needs the spec from Stage 7.

- [ ] `tests/conftest.py`: add the `conforms` fixture that validates responses against the live `/openapi.json`
- [ ] extend `tests/test_api.py`: assert each response shape against its OpenAPI component schema

**Key decisions:**
- Contract tests read the served spec, so implementation and documentation are checked against each other.
- Every test file sits with the stage that built it; only spec conformance waits for Stage 7.

**Verify:** `bash scripts/test.sh -v` all green; count >= 10.

---

## Stage 9: Deploy + rate limiting (bonus)

**Goal:** a production start path and basic abuse protection.

- [ ] `deploy.sh`: preflight (refuse missing key, refuse SQLite with >1 worker), migrate, `exec` uvicorn with workers and `--proxy-headers`
- [ ] Rate limiting on the credential endpoints (in-process, keyed on the peer socket)

**Verify:** `deploy.sh` refuses to start on a missing `SECRET_KEY`; serves on a single worker.

---
