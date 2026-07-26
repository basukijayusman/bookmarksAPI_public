"""End-to-end API tests for Stage 5: auth routes and bookmark CRUD.

These go through the real ASGI app against an in-memory database (see conftest.py),
so routing, dependencies, the error handlers, and serialization are all covered.
"""

SAMPLE = {
    "url": "https://example.com/post",
    "title": "A post worth keeping",
    "description": "Notes for later",
    "tags": ["Python", " python ", "FastAPI"],
}


def register(client, name: str = "alice") -> dict[str, str]:
    """Create a user and return the Authorization header for it."""
    response = client.post(
        "/api/auth/register",
        json={"username": name, "email": f"{name}@example.com", "password": "hunter2-long"},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def assert_error(response, status: int, code: str) -> dict:
    """Every failure, whatever raised it, must leave as the same envelope."""
    assert response.status_code == status, response.text
    payload = response.json()
    assert set(payload) == {"error"}
    assert set(payload["error"]) == {"code", "message", "details"}
    assert payload["error"]["code"] == code
    assert payload["error"]["message"]
    return payload["error"]


# --- health -----------------------------------------------------------------


def test_health_is_open_and_reports_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# --- registration and login -------------------------------------------------


def test_register_returns_201_with_the_user_and_a_token(client):
    response = client.post(
        "/api/auth/register",
        json={"username": "alice", "email": "alice@example.com", "password": "hunter2-long"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["user"] == {"id": 1, "username": "alice", "email": "alice@example.com"}
    assert body["token"]
    assert "password" not in str(body)  # neither the plaintext nor the hash leaks


def test_register_rejects_a_duplicate_email(client):
    register(client)
    response = client.post(
        "/api/auth/register",
        json={"username": "alice2", "email": "alice@example.com", "password": "hunter2-long"},
    )
    assert_error(response, 409, "ALREADY_EXISTS")


def test_register_rejects_a_password_under_the_minimum(client):
    response = client.post(
        "/api/auth/register",
        json={"username": "alice", "email": "alice@example.com", "password": "short"},
    )
    assert assert_error(response, 422, "VALIDATION_ERROR")["details"]["field"] == "password"


def test_login_returns_a_token_for_the_right_password(client):
    register(client)
    response = client.post(
        "/api/auth/login", json={"email": "alice@example.com", "password": "hunter2-long"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["token"]


def test_login_answers_a_wrong_password_and_an_unknown_email_identically(client):
    register(client)
    wrong = client.post(
        "/api/auth/login", json={"email": "alice@example.com", "password": "not-the-password"}
    )
    unknown = client.post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": "hunter2-long"}
    )
    # Same status, same code, same message: the response must not reveal which
    # accounts exist. (auth.py covers the timing half of that.)
    assert_error(wrong, 401, "INVALID_CREDENTIALS")
    assert_error(unknown, 401, "INVALID_CREDENTIALS")
    assert wrong.json() == unknown.json()


# --- authentication is required ---------------------------------------------


def test_bookmarks_require_a_token(client):
    assert_error(client.get("/api/bookmarks"), 401, "UNAUTHENTICATED")
    assert_error(client.post("/api/bookmarks", json=SAMPLE), 401, "UNAUTHENTICATED")


def test_a_garbage_token_is_rejected(client):
    response = client.get("/api/bookmarks", headers={"Authorization": "Bearer not-a-jwt"})
    assert_error(response, 401, "INVALID_TOKEN")


# --- CRUD -------------------------------------------------------------------


def test_create_returns_201_with_the_stored_bookmark(client):
    response = client.post("/api/bookmarks", json=SAMPLE, headers=register(client))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["url"] == SAMPLE["url"]
    assert body["title"] == SAMPLE["title"]
    assert body["description"] == SAMPLE["description"]
    assert body["id"] and body["created_at"] and body["updated_at"]


def test_create_normalises_and_deduplicates_tags(client):
    response = client.post("/api/bookmarks", json=SAMPLE, headers=register(client))
    # "Python" and " python " are the same tag; the response is lowercased and sorted.
    assert response.json()["tags"] == ["fastapi", "python"]


def test_a_tag_is_shared_between_users_rather_than_duplicated(client):
    alice = client.post(
        "/api/bookmarks", json={**SAMPLE, "tags": ["python"]}, headers=register(client, "alice")
    )
    bob = client.post(
        "/api/bookmarks", json={**SAMPLE, "tags": ["PYTHON"]}, headers=register(client, "bob")
    )
    # Tags are global and unique by name, so the second create reuses the first row.
    assert alice.json()["tags"] == bob.json()["tags"] == ["python"]


def test_create_fetch_update_delete_round_trip(client):
    headers = register(client)
    created = client.post("/api/bookmarks", json=SAMPLE, headers=headers).json()

    fetched = client.get(f"/api/bookmarks/{created['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json() == created

    updated = client.put(
        f"/api/bookmarks/{created['id']}",
        json={"url": "https://example.org/other", "title": "Renamed", "tags": ["rust"]},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["title"] == "Renamed"
    assert updated.json()["description"] is None  # PUT replaces, it does not merge
    assert updated.json()["tags"] == ["rust"]  # and the old tags are gone, not added to

    assert client.delete(f"/api/bookmarks/{created['id']}", headers=headers).status_code == 204
    assert_error(client.get(f"/api/bookmarks/{created['id']}", headers=headers), 404, "NOT_FOUND")


def test_the_list_returns_only_the_callers_bookmarks(client):
    alice, bob = register(client, "alice"), register(client, "bob")
    client.post("/api/bookmarks", json=SAMPLE, headers=alice)
    client.post("/api/bookmarks", json={**SAMPLE, "title": "Second"}, headers=alice)
    client.post("/api/bookmarks", json={**SAMPLE, "title": "Bob's"}, headers=bob)

    body = client.get("/api/bookmarks", headers=alice).json()
    assert body["total"] == 2
    assert [b["title"] for b in body["items"]] == ["Second", SAMPLE["title"]]  # newest first
    assert client.get("/api/bookmarks", headers=bob).json()["total"] == 1


def test_another_users_bookmark_is_404_on_every_verb(client):
    alice, bob = register(client, "alice"), register(client, "bob")
    bookmark_id = client.post("/api/bookmarks", json=SAMPLE, headers=alice).json()["id"]

    # 404 and not 403: a 403 would confirm the id exists and let bob enumerate them.
    assert_error(client.get(f"/api/bookmarks/{bookmark_id}", headers=bob), 404, "NOT_FOUND")
    assert_error(
        client.put(f"/api/bookmarks/{bookmark_id}", json=SAMPLE, headers=bob), 404, "NOT_FOUND"
    )
    assert_error(client.delete(f"/api/bookmarks/{bookmark_id}", headers=bob), 404, "NOT_FOUND")
    # And alice's bookmark survived all three attempts.
    assert client.get(f"/api/bookmarks/{bookmark_id}", headers=alice).status_code == 200


# --- input validation -------------------------------------------------------


def test_create_rejects_a_url_that_is_not_a_url(client):
    response = client.post(
        "/api/bookmarks", json={**SAMPLE, "url": "not-a-url"}, headers=register(client)
    )
    assert assert_error(response, 422, "VALIDATION_ERROR")["details"]["field"] == "url"


def test_create_rejects_an_empty_title(client):
    response = client.post(
        "/api/bookmarks", json={**SAMPLE, "title": ""}, headers=register(client)
    )
    assert assert_error(response, 422, "VALIDATION_ERROR")["details"]["field"] == "title"
