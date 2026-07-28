# Code Review — Bookmarks API

Reviewer: Opus 5 · Scope: full repo read-through (`app/`, `tests/`, `migrations/`, `scripts/`, `seed.py`, `deploy.sh`, `tripwire.py`)
Test state at review: **62 passed**. Lint (`ruff`) is not installed in the venv despite a `.ruff_cache` being present.

## Summary

Genuinely strong. Ownership scoping, the error envelope, and auth are done with real care, and the
comments explain *why* rather than *what*. **No CRITICAL or HIGH issues** — no injection, no secret
leakage, no auth bypass. Findings below are worst-first.

---

## MEDIUM

### 1. Redundant index on `bookmarks.user_id`
`app/models.py:47` + `migrations/versions/0001_initial.py:45`

`user_id` is declared `index=True` (→ `ix_bookmarks_user_id`) *and* there is a composite
`Index("ix_bookmarks_user_created", "user_id", "created_at")`. The composite already serves any
`user_id`-only lookup via its leftmost-prefix, so the single-column index is dead weight — pure
write-amplification and storage on every insert/update. Postgres/SQLite do not auto-require an FK
index, so it is safe to drop both the `index=True` and the migration's
`create_index("ix_bookmarks_user_id", ...)`.

### 2. `int(subject)` sits outside the try/except
`app/auth.py:65`

```python
subject = jwt.decode(...)["sub"]                                   # inside try -> 401 on failure
user = db.scalar(select(User).where(User.id == int(subject)))     # int() is OUTSIDE the try
```

A validly-*signed* token whose `sub` is not numeric raises `ValueError`, which escapes the
`except jwt.PyJWTError` and falls through to the 500 handler instead of returning 401. Your own
tokens are always numeric, so it cannot happen today — but it is a latent 500-instead-of-401 the
moment any other code path (or a test) mints a token. Move `int(subject)` inside the `try`, or
`except (jwt.PyJWTError, ValueError)`.

---

## LOW

### 3. `tripwire.py` wired into `scripts/commit.sh:25`
Read end to end: it is genuinely inert (no socket, no subprocess, no file reads; `send()` is a
`print`; `sys.exit(0)`). The concern is packaging, not payload: a file named "tripwire" that prints
*"you ran a stranger's code"* on every commit of a repo named `…_public` is a false-positive magnet
for scanners and a WTF for readers. If it must stay, move it out of the commit path and into a
`make lesson`-style target.

### 4. Migration hygiene
`migrations/versions/0001_initial.py:10-11` carries leftover editing notes
(`down_revision = None # ... set this to "last run revision number"`) that contradict the correct
value. Also all four `created_at`/`updated_at` columns are `nullable=False` with no `server_default`;
inserts work only because the ORM supplies the timestamp — a raw `INSERT` would fail. Fine for this
app, worth a one-line comment.

### 5. Dead constraint key
`app/errors.py:23` lists `"max_items"`, a Pydantic **v1** name. Under v2 the list-length violation
reports `max_length` (already in the tuple), so `max_items` never matches. Harmless, just remove it.

### 6. Minor, all documented / acceptable
- Validation envelope reports only `errors()[0]` (one field at a time).
- The rate-limit dict can grow under a unique-IP flood before stale eviction.
- `OFFSET` pagination is O(n) deep.
- The DELETE path still triggers the `selectin` tag load (TODO already at `app/models.py:55`).

---

## Not bugs — worth affirming

- Timing-equalized login via `DUMMY_HASH` (`app/routes.py:75`).
- 404-not-403 on ownership failure to kill the id-oracle (`app/routes.py:120`).
- JWT pinned to HS256, which blocks the `alg=none` confusion attack (`app/auth.py:62`).
- The 500 handler logs and returns a generic message — no stack leak (`app/errors.py:56`).
- `_like()` escapes `%`/`_` so a wildcard search is a literal (`app/routes.py:151`).
- Tie-broken ordering `created_at.desc(), id.desc()` for stable pages (`app/routes.py:201`).
- Get-or-create tag race handled by the unique index + one retry (`app/routes.py:93`).

---

## Suggested next actions

- Apply #1 (drop the redundant index in model + migration) and #2 (pull `int(subject)` into the try).
  Both are small and test-covered by the existing suite once adjusted.
- Decide the fate of #3 (`tripwire.py`) — author's teaching artifact, left as a judgment call.
- Optional: wire `ruff` into the venv/`requirements.txt` and add `pytest-cov` to measure the 80% target.
