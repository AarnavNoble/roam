"""Accounts, bearer tokens, saved trips and share links, through the HTTP API."""

from datetime import datetime, timedelta, timezone

import jwt
import pytest

from backend import config
from backend.api.trips import save_trip
from backend.db.models import Trip, User
from backend.db.session import session_scope

PASSWORD = "correct horse battery"
SECRET_NOTE = "allergic to shellfish, staying at Hotel Particulier"


def register(client, email: str = "ada@example.com", password: str = PASSWORD) -> dict:
    """Register and return {"token", "user", "headers"}."""
    r = client.post("/api/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return r.json() | {"headers": {"Authorization": f"Bearer {r.json()['token']}"}}


def make_trip(user_id: str | None = None, city: str = "Paris") -> str:
    """Store a trip the way the itinerary endpoint does, without running the pipeline."""
    request = {"city": city, "start_location": "Montmartre", "goals": ["food"], "trip_date": "2026-06-03",
               "notes": SECRET_NOTE}
    route = [{"day": 1, "pois": [{"id": 1, "name": "Cafe", "lat": 48.88, "lon": 2.34}], "travel_times_sec": []}]
    itinerary = {"overview": "A short day.", "degraded": [], "timings_ms": {"llm": 12.0},
                 "ranking_explanations": {"Cafe": {}}, "trip_id": "stale",
                 "days": [{"day": 1, "theme": "Coffee", "summary": "One stop.", "stops": [
                     {"id": 1, "name": "Cafe", "lat": 48.88, "lon": 2.34, "arrival_time": "09:10",
                      "description": "Good coffee."}]}]}
    with session_scope() as db:
        return save_trip(db, user_id=user_id, request=request, route=route, itinerary=itinerary, degraded=[]).id


# ── Register and login ───────────────────────────────────────────────────────

def test_register_returns_a_token_that_identifies_the_user(client):
    account = register(client, email="  Ada@Example.COM ")
    assert account["user"]["email"] == "ada@example.com"  # trimmed and lower-cased
    me = client.get("/api/auth/me", headers=account["headers"])
    assert me.status_code == 200
    assert me.json() == {"id": account["user"]["id"], "email": "ada@example.com"}


def test_password_is_stored_as_a_bcrypt_hash(client):
    account = register(client)
    with session_scope() as db:
        stored = db.get(User, account["user"]["id"]).password_hash
    assert stored.startswith("$2") and PASSWORD not in stored


def test_duplicate_email_is_409_even_with_different_case(client):
    register(client, email="ada@example.com")
    r = client.post("/api/auth/register", json={"email": "ADA@example.com", "password": PASSWORD})
    assert r.status_code == 409
    with session_scope() as db:
        assert db.query(User).count() == 1


@pytest.mark.parametrize("password", ["short", "1234567", "x" * 73, "é" * 37])
def test_weak_or_oversized_password_is_422(client, password):
    # Under 8 bytes is too weak; over 72 bytes would be silently truncated by bcrypt.
    r = client.post("/api/auth/register", json={"email": "ada@example.com", "password": password})
    assert r.status_code == 422


@pytest.mark.parametrize("email", ["not-an-email", "a@b", "two words@example.com", "", "a@@example.com"])
def test_invalid_email_is_422(client, email):
    r = client.post("/api/auth/register", json={"email": email, "password": PASSWORD})
    assert r.status_code == 422


def test_login_returns_a_working_token(client):
    account = register(client)
    r = client.post("/api/auth/login", json={"email": "ADA@example.com", "password": PASSWORD})
    assert r.status_code == 200
    assert r.json()["user"] == account["user"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {r.json()['token']}"})
    assert me.status_code == 200


def test_wrong_password_and_unknown_email_get_the_same_401(client):
    # Same status and same message, so the response does not reveal which emails have accounts.
    register(client)
    wrong = client.post("/api/auth/login", json={"email": "ada@example.com", "password": "wrong password"})
    unknown = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": PASSWORD})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


# ── Tokens ───────────────────────────────────────────────────────────────────

def forged(sub: str, secret: str = None, expires_in_hours: float = 1, algorithm: str = "HS256") -> dict:
    now = datetime.now(timezone.utc)
    token = jwt.encode({"sub": sub, "iat": now, "exp": now + timedelta(hours=expires_in_hours)},
                       secret or config.JWT_SECRET, algorithm=algorithm)
    return {"Authorization": f"Bearer {token}"}


def test_missing_token_is_401_on_protected_routes(client):
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/trips").status_code == 401


def test_garbage_token_is_401(client):
    r = client.get("/api/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid or expired token"


def test_expired_token_is_401(client):
    account = register(client)
    r = client.get("/api/auth/me", headers=forged(account["user"]["id"], expires_in_hours=-1))
    assert r.status_code == 401


def test_token_signed_with_another_secret_is_401(client):
    account = register(client)
    r = client.get("/api/auth/me", headers=forged(account["user"]["id"], secret="some-other-secret-of-32-bytes-len"))
    assert r.status_code == 401


def test_unsigned_token_is_401(client):
    # alg=none must not be accepted: only HS256 is allowed when decoding.
    account = register(client)
    now = datetime.now(timezone.utc)
    token = jwt.encode({"sub": account["user"]["id"], "exp": now + timedelta(hours=1)}, None, algorithm="none")
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_valid_token_for_a_deleted_user_is_401(client):
    account = register(client)
    with session_scope() as db:
        db.delete(db.get(User, account["user"]["id"]))
    assert client.get("/api/auth/me", headers=account["headers"]).status_code == 401


def test_bad_token_is_401_even_on_routes_that_allow_anonymous(client):
    # An optional-auth route does not quietly downgrade a bad token to anonymous.
    trip_id = make_trip()
    r = client.get(f"/api/trips/{trip_id}", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


# ── Trip privacy ─────────────────────────────────────────────────────────────

def test_private_trip_is_404_to_other_users_and_anonymous_callers(client):
    owner, other = register(client, "owner@example.com"), register(client, "other@example.com")
    trip_id = make_trip(owner["user"]["id"])
    assert client.get(f"/api/trips/{trip_id}", headers=owner["headers"]).status_code == 200
    # 404, not 403: a caller cannot tell someone else's trip from one that does not exist
    assert client.get(f"/api/trips/{trip_id}", headers=other["headers"]).status_code == 404
    assert client.get(f"/api/trips/{trip_id}").status_code == 404
    assert client.get("/api/trips/doesnotexist").status_code == 404


def test_other_users_cannot_share_unshare_or_delete_a_private_trip(client):
    owner, other = register(client, "owner@example.com"), register(client, "other@example.com")
    trip_id = make_trip(owner["user"]["id"])
    assert client.post(f"/api/trips/{trip_id}/share", headers=other["headers"]).status_code == 404
    assert client.post(f"/api/trips/{trip_id}/share").status_code == 404
    assert client.delete(f"/api/trips/{trip_id}/share", headers=other["headers"]).status_code == 404
    assert client.delete(f"/api/trips/{trip_id}", headers=other["headers"]).status_code == 404
    assert client.delete(f"/api/trips/{trip_id}").status_code == 401
    assert client.get(f"/api/trips/{trip_id}", headers=owner["headers"]).status_code == 200  # still there


def test_anonymous_trip_is_readable_by_anyone_holding_its_id(client):
    trip_id = make_trip(user_id=None)
    r = client.get(f"/api/trips/{trip_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == trip_id and body["city"] == "Paris" and body["stops"] == 1
    assert body["itinerary"]["trip_id"] == trip_id
    assert body["itinerary"]["days"][0]["stops"][0]["name"] == "Cafe"
    # a signed-in user holding the id can read it too
    assert client.get(f"/api/trips/{trip_id}", headers=register(client)["headers"]).status_code == 200


def test_stored_itinerary_drops_per_response_fields(client):
    # Explanations and timings describe one response; they are not part of the saved trip.
    body = client.get(f"/api/trips/{make_trip()}").json()
    assert "timings_ms" not in body["itinerary"] and "ranking_explanations" not in body["itinerary"]


def test_list_returns_only_the_callers_trips_newest_first(client):
    owner, other = register(client, "owner@example.com"), register(client, "other@example.com")
    first = make_trip(owner["user"]["id"], city="Paris")
    second = make_trip(owner["user"]["id"], city="Rome")
    make_trip(other["user"]["id"], city="Tokyo")
    make_trip(user_id=None, city="Lima")
    # created_at has to differ for the order to be defined
    with session_scope() as db:
        db.get(Trip, first).created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        db.get(Trip, second).created_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
    trips = client.get("/api/trips", headers=owner["headers"]).json()["trips"]
    assert [(t["id"], t["city"]) for t in trips] == [(second, "Rome"), (first, "Paris")]
    assert "request" not in trips[0]  # the list is a summary
    page = client.get("/api/trips?limit=1&offset=1", headers=owner["headers"]).json()["trips"]
    assert [t["id"] for t in page] == [first]


# ── Sharing ──────────────────────────────────────────────────────────────────

def test_share_creates_a_slug_and_is_idempotent(client):
    owner = register(client)
    trip_id = make_trip(owner["user"]["id"])
    r = client.post(f"/api/trips/{trip_id}/share", headers=owner["headers"])
    assert r.status_code == 200
    slug = r.json()["share_slug"]
    assert 8 <= len(slug) <= 16 and slug != trip_id
    assert r.json()["path"] == f"/t/{slug}"
    again = client.post(f"/api/trips/{trip_id}/share", headers=owner["headers"])
    assert again.json()["share_slug"] == slug
    assert client.get(f"/api/trips/{trip_id}", headers=owner["headers"]).json()["share_slug"] == slug


def test_shared_view_is_public_and_omits_the_request(client):
    owner = register(client)
    trip_id = make_trip(owner["user"]["id"])
    slug = client.post(f"/api/trips/{trip_id}/share", headers=owner["headers"]).json()["share_slug"]
    r = client.get(f"/api/shared/{slug}")  # no token
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"city", "goals", "trip_date", "itinerary"}
    assert body["itinerary"]["days"][0]["stops"][0]["name"] == "Cafe"
    # the traveller's notes, the trip id and the owner never appear in the public view
    assert SECRET_NOTE not in r.text and "shellfish" not in r.text
    assert trip_id not in r.text and owner["user"]["id"] not in r.text
    # and the slug is not a way into the private endpoint
    assert client.get(f"/api/trips/{slug}").status_code == 404


def test_unknown_slug_is_404(client):
    assert client.get("/api/shared/nosuchslug").status_code == 404


def test_unshare_revokes_the_link_and_a_new_share_gets_a_new_slug(client):
    owner = register(client)
    trip_id = make_trip(owner["user"]["id"])
    slug = client.post(f"/api/trips/{trip_id}/share", headers=owner["headers"]).json()["share_slug"]
    assert client.delete(f"/api/trips/{trip_id}/share", headers=owner["headers"]).status_code == 204
    assert client.get(f"/api/shared/{slug}").status_code == 404
    assert client.get(f"/api/trips/{trip_id}", headers=owner["headers"]).status_code == 200  # trip kept
    new_slug = client.post(f"/api/trips/{trip_id}/share", headers=owner["headers"]).json()["share_slug"]
    assert new_slug != slug


def test_sharing_an_anonymous_trip_while_signed_in_claims_it(client):
    # The first signed-in writer becomes the owner, after which the trip is private.
    account = register(client)
    trip_id = make_trip(user_id=None)
    assert client.post(f"/api/trips/{trip_id}/share", headers=account["headers"]).status_code == 200
    assert [t["id"] for t in client.get("/api/trips", headers=account["headers"]).json()["trips"]] == [trip_id]
    assert client.get(f"/api/trips/{trip_id}").status_code == 404


def test_anonymous_caller_can_share_an_anonymous_trip(client):
    trip_id = make_trip(user_id=None)
    r = client.post(f"/api/trips/{trip_id}/share")
    assert r.status_code == 200
    assert client.get(f"/api/shared/{r.json()['share_slug']}").status_code == 200


# ── Delete ───────────────────────────────────────────────────────────────────

def test_owner_deletes_trip_and_its_share_link_dies_with_it(client):
    owner = register(client)
    trip_id = make_trip(owner["user"]["id"])
    slug = client.post(f"/api/trips/{trip_id}/share", headers=owner["headers"]).json()["share_slug"]
    assert client.delete(f"/api/trips/{trip_id}", headers=owner["headers"]).status_code == 204
    assert client.get(f"/api/trips/{trip_id}", headers=owner["headers"]).status_code == 404
    assert client.get(f"/api/shared/{slug}").status_code == 404
    assert client.get("/api/trips", headers=owner["headers"]).json()["trips"] == []
    assert client.delete(f"/api/trips/{trip_id}", headers=owner["headers"]).status_code == 404


def test_anonymous_trip_cannot_be_deleted_by_a_signed_in_stranger(client):
    # Holding the id of an unowned trip lets you read it, not destroy it.
    trip_id = make_trip(user_id=None)
    assert client.delete(f"/api/trips/{trip_id}", headers=register(client)["headers"]).status_code == 404
    assert client.get(f"/api/trips/{trip_id}").status_code == 200


def test_deleting_a_parent_trip_keeps_its_edited_child(client):
    # Edits point at their parent with ON DELETE SET NULL, so history survives a delete.
    owner = register(client)
    parent = make_trip(owner["user"]["id"])
    child = make_trip(owner["user"]["id"])
    with session_scope() as db:
        db.get(Trip, child).parent_id = parent
    assert client.get(f"/api/trips/{child}", headers=owner["headers"]).json()["parent_id"] == parent
    assert client.delete(f"/api/trips/{parent}", headers=owner["headers"]).status_code == 204
    r = client.get(f"/api/trips/{child}", headers=owner["headers"])
    assert r.status_code == 200 and r.json()["parent_id"] is None


# ── The itinerary endpoint and accounts together ─────────────────────────────

def test_itinerary_made_while_signed_in_is_saved_to_the_account(client, upstreams):
    from backend.tests.conftest import trip_request
    owner = register(client)
    body = client.post("/api/itinerary", json=trip_request(notes=SECRET_NOTE), headers=owner["headers"]).json()
    trips = client.get("/api/trips", headers=owner["headers"]).json()["trips"]
    assert [t["id"] for t in trips] == [body["trip_id"]]
    assert trips[0]["stops"] == 6 and trips[0]["goals"] == ["food", "culture"]
    assert client.get(f"/api/trips/{body['trip_id']}").status_code == 404  # private to the account
    detail = client.get(f"/api/trips/{body['trip_id']}", headers=owner["headers"]).json()
    assert detail["request"]["notes"] == SECRET_NOTE
    assert detail["itinerary"]["days"] == body["days"]


def test_itinerary_made_anonymously_is_saved_unowned(client, upstreams):
    from backend.tests.conftest import trip_request
    body = client.post("/api/itinerary", json=trip_request()).json()
    assert client.get(f"/api/trips/{body['trip_id']}").status_code == 200
    with session_scope() as db:
        assert db.get(Trip, body["trip_id"]).user_id is None


def test_itinerary_with_a_bad_token_is_401_and_runs_nothing(client, upstreams):
    from backend.tests.conftest import trip_request
    r = client.post("/api/itinerary", json=trip_request(), headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401
    assert upstreams.calls == []
