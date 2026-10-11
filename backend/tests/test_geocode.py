"""Geocoding: Nominatim first, Photon on failure, results cached, misses never retried."""

import pytest

from backend.core.resilience import Deadline, DeadlineExceeded, UpstreamError
from backend.services import nominatim
from backend.services.nominatim import NotFound, geocode, geocode_with_source

EIFFEL = [{"lat": "48.8584", "lon": "2.2945"}]
PHOTON_EIFFEL = {"features": [{"geometry": {"coordinates": [2.2950, 48.8580]}}]}  # GeoJSON: lon, lat


def test_nominatim_answer_is_parsed_to_floats(http):
    http.handlers["nominatim"] = http.json(EIFFEL)
    assert geocode_with_source("Eiffel Tower, Paris") == (48.8584, 2.2945, "nominatim")
    service, kwargs = http.requests[0]
    assert service == "nominatim"
    assert kwargs["params"]["q"] == "Eiffel Tower, Paris"
    assert "roam-app" in kwargs["headers"]["User-Agent"]  # Nominatim's usage policy requires one
    assert kwargs["timeout"] == nominatim.TIMEOUT_S


def test_repeat_lookup_is_served_from_cache(http):
    http.handlers["nominatim"] = http.json(EIFFEL)
    geocode_with_source("Eiffel Tower, Paris")
    # same place, different case and padding: no second call
    assert geocode_with_source("  eiffel tower, PARIS ") == (48.8584, 2.2945, "cache")
    assert http.count("nominatim") == 1


def test_nominatim_failure_is_retried_once_then_photon_answers(http):
    http.handlers["nominatim"] = http.status(503)
    http.handlers["photon"] = http.json(PHOTON_EIFFEL)
    # Photon returns [lon, lat]; the result must not have them swapped
    assert geocode_with_source("Eiffel Tower, Paris") == (48.8580, 2.2950, "photon")
    assert http.calls == ["nominatim", "nominatim", "photon"]


def test_photon_answer_is_cached_too(http):
    http.handlers["nominatim"] = http.unreachable()
    http.handlers["photon"] = http.json(PHOTON_EIFFEL)
    geocode_with_source("Eiffel Tower, Paris")
    assert geocode_with_source("Eiffel Tower, Paris")[2] == "cache"
    assert http.count("photon") == 1


def test_both_down_raises_upstream_error_and_caches_nothing(http):
    http.handlers["nominatim"] = http.status(500)
    http.handlers["photon"] = http.status(500)
    with pytest.raises(UpstreamError, match="Geocoding unavailable"):
        geocode_with_source("Eiffel Tower, Paris")
    assert http.calls == ["nominatim", "nominatim", "photon", "photon"]
    # the failure was not cached: once Nominatim is back the same query works
    http.handlers["nominatim"] = http.json(EIFFEL)
    assert geocode_with_source("Eiffel Tower, Paris")[2] == "nominatim"


def test_unknown_place_is_a_value_error_with_no_retry_and_no_failover(http):
    http.handlers["nominatim"] = http.json([])
    with pytest.raises(NotFound) as e:
        geocode_with_source("Zzyzx Qwerty")
    assert isinstance(e.value, ValueError) and not isinstance(e.value, UpstreamError)
    assert http.calls == ["nominatim"]


def test_unknown_place_does_not_count_against_the_breaker(http):
    # "No such place" is a healthy answer. Many of them in a row must leave the breaker closed.
    http.handlers["nominatim"] = http.json([])
    for i in range(5):
        with pytest.raises(NotFound):
            geocode_with_source(f"Nowhere {i}")
    assert nominatim._nominatim_breaker.state == "closed"
    assert http.count("photon") == 0


def test_photon_saying_unknown_place_is_also_a_value_error(http):
    # Nominatim is down and Photon answers that the place does not exist: 400 material, not 503.
    http.handlers["nominatim"] = http.status(503)
    http.handlers["photon"] = http.json({"features": []})
    with pytest.raises(NotFound):
        geocode_with_source("Zzyzx Qwerty")


def test_nominatim_breaker_opens_and_requests_go_straight_to_photon(http):
    # Two lookups x two attempts is four failures; the threshold is three. The third lookup
    # does not touch Nominatim at all.
    http.handlers["nominatim"] = http.status(503)
    http.handlers["photon"] = http.json(PHOTON_EIFFEL)
    geocode_with_source("Place one")
    geocode_with_source("Place two")
    assert nominatim._nominatim_breaker.state == "open"
    calls_before = http.count("nominatim")
    assert geocode_with_source("Place three")[2] == "photon"
    assert http.count("nominatim") == calls_before


def test_expired_deadline_stops_before_any_call(http):
    with pytest.raises(DeadlineExceeded):
        geocode_with_source("Eiffel Tower, Paris", Deadline(0))
    assert http.calls == []


def test_deadline_shortens_the_http_timeout(http):
    http.handlers["nominatim"] = http.json(EIFFEL)
    geocode_with_source("Eiffel Tower, Paris", Deadline(2.0))
    assert 0 < http.requests[0][1]["timeout"] <= 2.0


def test_geocode_wrapper_returns_lat_lon(http):
    http.handlers["nominatim"] = http.json(EIFFEL)
    assert geocode("Eiffel Tower, Paris") == (48.8584, 2.2945)
