"""End-to-end API tests: auth routes, bookmark CRUD, querying, stats, and the spec.

These go through the real ASGI app against an in-memory database (see conftest.py),
so routing, dependencies, the error handlers, and serialization are all covered.
"""

from datetime import datetime, timedelta, timezone

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


def create(client, headers, **overrides) -> dict:
    """Store one bookmark, SAMPLE with the given fields replaced."""
    response = client.post("/api/bookmarks", json={**SAMPLE, **overrides}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def listed(client, headers, **params) -> dict:
    response = client.get("/api/bookmarks", params=params, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


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


# --- search and filter ------------------------------------------------------


def test_filter_by_tag_returns_only_the_matching_bookmarks(client):
    headers = register(client)
    create(client, headers, title="Rust one", tags=["rust"])
    create(client, headers, title="Python one", tags=["python"])

    body = listed(client, headers, tag="rust")
    assert [b["title"] for b in body["items"]] == ["Rust one"]
    assert body["total"] == 1


def test_filter_by_tag_normalises_the_query_the_way_the_write_path_does(client):
    headers = register(client)
    create(client, headers, tags=["Python"])
    # Stored as "python", so an unnormalised query would match nothing it just created.
    assert listed(client, headers, tag=" PYTHON ")["total"] == 1


def test_search_matches_the_title_or_the_description_ignoring_case(client):
    headers = register(client)
    create(client, headers, title="Learning SQL", description="join notes")
    create(client, headers, title="Unrelated", description="Deep sql internals")
    create(client, headers, title="Neither", description=None)  # NULL fails its half of the OR

    assert listed(client, headers, q="sql")["total"] == 2


def test_search_treats_a_wildcard_as_a_literal_character(client):
    headers = register(client)
    create(client, headers, title="Battery at 100%")
    create(client, headers, title="Battery at 1000 mAh")

    # Unescaped, "100%" is the LIKE pattern "100 followed by anything" and matches both.
    assert [b["title"] for b in listed(client, headers, q="100%")["items"]] == ["Battery at 100%"]


def test_the_date_range_covers_the_whole_of_the_end_day(client):
    headers = register(client)
    create(client, headers)
    # created_at is UTC, so the boundaries are UTC days regardless of the host's zone.
    today = datetime.now(timezone.utc).date()

    # to=today keeps a bookmark created at 14:32 today, not only one created at midnight.
    assert listed(client, headers, **{"from": str(today), "to": str(today)})["total"] == 1
    assert listed(client, headers, **{"from": str(today + timedelta(days=1))})["total"] == 0
    assert listed(client, headers, **{"to": str(today - timedelta(days=1))})["total"] == 0


def test_filters_stay_scoped_to_the_caller(client):
    alice, bob = register(client, "alice"), register(client, "bob")
    create(client, alice, title="Alice on rust", tags=["rust"])
    create(client, bob, title="Bob on rust", tags=["rust"])

    body = listed(client, bob, tag="rust", q="rust")
    assert [b["title"] for b in body["items"]] == ["Bob on rust"]
    assert body["total"] == 1


# --- pagination -------------------------------------------------------------


def test_pagination_reports_the_full_total_and_does_not_repeat_rows(client):
    headers = register(client)
    for n in range(5):
        create(client, headers, title=f"Post {n}")

    pages = [listed(client, headers, per_page=2, page=p) for p in (1, 2, 3)]
    assert [len(page["items"]) for page in pages] == [2, 2, 1]
    # total is the size of the whole result set, not of the page that was returned.
    assert [page["total"] for page in pages] == [5, 5, 5]
    assert pages[0]["page"] == 1 and pages[0]["per_page"] == 2

    titles = [b["title"] for page in pages for b in page["items"]]
    assert len(set(titles)) == 5  # the offsets do not overlap or skip


def test_pagination_bounds_are_enforced(client):
    headers = register(client)
    over_cap = assert_error(
        client.get("/api/bookmarks", params={"per_page": 1000}, headers=headers),
        422,
        "VALIDATION_ERROR",
    )
    assert over_cap["details"]["field"] == "per_page"
    assert over_cap["details"]["limit"] == 100  # a client cannot ask for an unbounded page

    under_floor = client.get("/api/bookmarks", params={"page": 0}, headers=headers)
    assert assert_error(under_floor, 422, "VALIDATION_ERROR")["details"]["field"] == "page"


# --- stats ------------------------------------------------------------------


def test_stats_is_not_parsed_as_a_bookmark_id(client):
    # /stats is declared before /{bookmark_id}; the other order makes this a 422.
    response = client.get("/api/bookmarks/stats", headers=register(client))
    assert response.status_code == 200, response.text


def test_stats_requires_a_token(client):
    assert_error(client.get("/api/bookmarks/stats"), 401, "UNAUTHENTICATED")


def test_stats_counts_only_the_callers_bookmarks_and_tags(client):
    alice, bob = register(client, "alice"), register(client, "bob")
    create(client, alice, tags=["python", "web"])
    create(client, alice, tags=["python"])
    create(client, bob, tags=["python", "rust", "go"])

    body = client.get("/api/bookmarks/stats", headers=alice).json()
    assert body["total_bookmarks"] == 2
    # 4 tag rows exist globally; 2 of them are on alice's bookmarks. Counting the
    # tags table instead would report bob's rust and go as well.
    assert body["total_tags"] == 2
    assert body["top_tags"] == [{"name": "python", "count": 2}, {"name": "web", "count": 1}]
    assert body["bookmarks_per_month"] == [
        {"month": datetime.now(timezone.utc).strftime("%Y-%m"), "count": 2}
    ]


def test_stats_is_all_zeroes_for_a_user_with_no_bookmarks(client):
    body = client.get("/api/bookmarks/stats", headers=register(client)).json()
    assert body == {
        "total_bookmarks": 0,
        "total_tags": 0,
        "top_tags": [],
        "bookmarks_per_month": [],
    }


# --- OpenAPI documentation --------------------------------------------------


def test_the_docs_and_the_spec_are_served(client):
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_the_spec_requires_a_bearer_token_on_protected_endpoints_only(client):
    spec = client.get("/openapi.json").json()
    scheme = spec["components"]["securitySchemes"]["HTTPBearer"]
    assert scheme["type"] == "http" and scheme["scheme"] == "bearer"

    paths = spec["paths"]
    assert paths["/api/bookmarks"]["get"]["security"]
    assert paths["/api/bookmarks/stats"]["get"]["security"]
    assert paths["/api/bookmarks/{bookmark_id}"]["delete"]["security"]
    # The credential endpoints issue the token, so requiring one would be circular.
    assert "security" not in paths["/api/auth/login"]["post"]
    assert "security" not in paths["/health"]["get"]


def test_the_spec_names_the_response_model_of_every_endpoint(client):
    spec = client.get("/openapi.json").json()

    def ref(path: str, method: str, status: str) -> str:
        content = spec["paths"][path][method]["responses"][status]["content"]
        return content["application/json"]["schema"]["$ref"]

    assert ref("/api/auth/register", "post", "201").endswith("/AuthOut")
    assert ref("/api/bookmarks", "post", "201").endswith("/BookmarkOut")
    assert ref("/api/bookmarks", "get", "200").endswith("/BookmarkPage")
    assert ref("/api/bookmarks/stats", "get", "200").endswith("/Stats")
    # The documented failure shape is the envelope errors.py actually emits.
    assert ref("/api/bookmarks", "get", "default").endswith("/ErrorOut")
    # 204 means no body, so it must not advertise one.
    delete = spec["paths"]["/api/bookmarks/{bookmark_id}"]["delete"]
    assert "content" not in delete["responses"]["204"]


def test_the_spec_documents_the_list_query_parameters(client):
    params = {
        p["name"]: p
        for p in client.get("/openapi.json").json()["paths"]["/api/bookmarks"]["get"]["parameters"]
    }
    assert set(params) == {"tag", "q", "from", "to", "page", "per_page"}
    # Documented under their wire names: `from` is a Python keyword, so the handler
    # takes date_from and the alias is what appears here and in the URL.
    assert all(p["in"] == "query" and not p.get("required") for p in params.values())
    assert params["per_page"]["schema"]["maximum"] == 100


# --- OpenAPI contract conformance -------------------------------------------
#
# The tests above assert the *spec* is shaped right; these assert the live responses
# actually match it. Together they close the loop: the spec cannot drift from the code
# (it is generated from the models), and the code cannot drift from the spec (these
# validate against the served /openapi.json). `conforms` lives in conftest.py.


def test_the_register_response_conforms_to_its_schema(client, conforms):
    response = client.post(
        "/api/auth/register",
        json={"username": "alice", "email": "alice@example.com", "password": "hunter2-long"},
    )
    conforms(response, "/api/auth/register", "post", 201)  # AuthOut, with a nested UserOut


def test_the_bookmark_crud_responses_conform_to_their_schemas(client, conforms):
    headers = register(client)
    created = client.post("/api/bookmarks", json=SAMPLE, headers=headers)
    conforms(created, "/api/bookmarks", "post", 201)  # BookmarkOut

    bookmark_id = created.json()["id"]
    conforms(client.get(f"/api/bookmarks/{bookmark_id}", headers=headers),
             "/api/bookmarks/{bookmark_id}", "get", 200)
    conforms(client.get("/api/bookmarks", headers=headers),
             "/api/bookmarks", "get", 200)  # BookmarkPage, items are BookmarkOut
    # 204 promises no body; the fixture asserts the response honours that.
    conforms(client.delete(f"/api/bookmarks/{bookmark_id}", headers=headers),
             "/api/bookmarks/{bookmark_id}", "delete", 204)


def test_the_stats_response_conforms_to_its_schema(client, conforms):
    conforms(client.get("/api/bookmarks/stats", headers=register(client)),
             "/api/bookmarks/stats", "get", 200)  # Stats, with nested TagCount/MonthCount


def test_the_error_envelope_conforms_to_the_documented_shape(client, conforms):
    # Any failure resolves to the `default` response, whose schema is ErrorOut. The
    # body here is a 401, validated against the shape every endpoint documents for errors.
    conforms(client.get("/api/bookmarks"), "/api/bookmarks", "get", "default")
