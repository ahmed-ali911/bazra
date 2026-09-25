from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.modules.weather import service as weather_service
from app.modules.weather.schemas import ResolvedLocation, WeatherProviderError

_CAIRO = ResolvedLocation(display_name="Cairo, Egypt", latitude=30.06, longitude=31.25, timezone="Africa/Cairo")


class _FrozenDatetime(datetime):
    """Subclasses the real datetime so fromisoformat/combine/etc. behave
    identically — only .now() is overridden, to control what
    _parse_tonight sees as "the current instant" without adding a
    test-only clock parameter to production code."""

    _frozen_utc: datetime

    @classmethod
    def now(cls, tz=None):
        if tz is not None:
            return cls._frozen_utc.astimezone(tz)
        return cls._frozen_utc


def _freeze(monkeypatch: pytest.MonkeyPatch, utc_instant: datetime) -> None:
    frozen = type("_Frozen", (_FrozenDatetime,), {"_frozen_utc": utc_instant})
    monkeypatch.setattr(weather_service, "datetime", frozen)


def _fake_get(monkeypatch: pytest.MonkeyPatch, responses: list) -> list:
    """responses is a list of httpx.Response (or exception instances) to
    return/raise in order, one per call — geocode_location and
    fetch_weather each make exactly one HTTP call, so a 2-call flow
    passes a 2-item list."""
    calls: list = []

    def _get(url, params, timeout):
        calls.append((url, params, timeout))
        item = responses[len(calls) - 1]
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(weather_service.httpx, "get", _get)
    return calls


def _geocoding_response(name="Cairo", country="Egypt", lat=30.06, lon=31.25, tz="Africa/Cairo") -> httpx.Response:
    return httpx.Response(
        200,
        json={"results": [{"name": name, "country": country, "latitude": lat, "longitude": lon, "timezone": tz}]},
    )


# ---- geocoding ---------------------------------------------------------------


def test_geocode_location_success(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_get(monkeypatch, [_geocoding_response()])
    resolved = weather_service.geocode_location("Cairo")

    assert resolved == ResolvedLocation(
        display_name="Cairo, Egypt", latitude=30.06, longitude=31.25, timezone="Africa/Cairo"
    )
    assert len(calls) == 1
    assert calls[0][0] == weather_service._GEOCODING_URL
    assert calls[0][1]["name"] == "Cairo"
    assert calls[0][1]["count"] == 1


def test_geocode_location_no_result_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_get(monkeypatch, [httpx.Response(200, json={"results": []})])
    assert weather_service.geocode_location("Nowhereville") is None


def test_geocode_location_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_get(monkeypatch, [httpx.TimeoutException("timed out")])
    with pytest.raises(WeatherProviderError) as exc:
        weather_service.geocode_location("Cairo")
    assert exc.value.error_class == "timeout"


def test_geocode_location_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_get(monkeypatch, [httpx.ConnectError("refused")])
    with pytest.raises(WeatherProviderError) as exc:
        weather_service.geocode_location("Cairo")
    assert exc.value.error_class == "connection_error"


def test_geocode_location_4xx(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_get(monkeypatch, [httpx.Response(400, json={})])
    with pytest.raises(WeatherProviderError) as exc:
        weather_service.geocode_location("Cairo")
    assert exc.value.error_class == "provider_error_client"


def test_geocode_location_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_get(monkeypatch, [httpx.Response(503, json={})])
    with pytest.raises(WeatherProviderError) as exc:
        weather_service.geocode_location("Cairo")
    assert exc.value.error_class == "provider_error_server"


def test_geocode_location_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_get(monkeypatch, [httpx.Response(429, json={})])
    with pytest.raises(WeatherProviderError) as exc:
        weather_service.geocode_location("Cairo")
    assert exc.value.error_class == "rate_limited"


def test_geocode_location_malformed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_get(monkeypatch, [httpx.Response(200, content=b"not json")])
    with pytest.raises(WeatherProviderError) as exc:
        weather_service.geocode_location("Cairo")
    assert exc.value.error_class == "malformed_response"


def test_geocode_location_missing_fields_is_malformed(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_get(monkeypatch, [httpx.Response(200, json={"results": [{"name": "Cairo"}]})])
    with pytest.raises(WeatherProviderError) as exc:
        weather_service.geocode_location("Cairo")
    assert exc.value.error_class == "malformed_response"


# ---- fetch_weather: now / today / tomorrow normalization ---------------------


def test_fetch_weather_now_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_get(monkeypatch, [
        httpx.Response(200, json={
            "timezone": "Africa/Cairo",
            "current": {"time": "2026-09-25T15:00", "temperature_2m": 29.4, "apparent_temperature": 31.2, "weather_code": 0},
        }),
    ])
    result = weather_service.fetch_weather(_CAIRO, "now")

    assert result.horizon == "now"
    assert result.resolved_location == "Cairo, Egypt"
    assert result.temperature == 29.4
    assert result.feels_like == 31.2
    assert result.condition == "clear"
    assert result.temperature_low is None
    assert result.temperature_high is None
    assert result.precipitation_probability is None
    assert result.period_end is None
    assert result.timezone == "Africa/Cairo"
    assert result.units == "C"
    assert result.period_start == datetime(2026, 9, 25, 15, 0, tzinfo=ZoneInfo("Africa/Cairo"))


def test_fetch_weather_now_request_omits_forecast_days(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_get(monkeypatch, [
        httpx.Response(200, json={"current": {"time": "2026-09-25T15:00", "temperature_2m": 20, "apparent_temperature": 20, "weather_code": 0}}),
    ])
    weather_service.fetch_weather(_CAIRO, "now")
    assert "forecast_days" not in calls[0][1]
    assert "daily" not in calls[0][1]
    assert "hourly" not in calls[0][1]
    assert calls[0][1]["current"] == "temperature_2m,apparent_temperature,weather_code"


def test_fetch_weather_today_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_get(monkeypatch, [
        httpx.Response(200, json={
            "timezone": "Africa/Cairo",
            "daily": {
                "time": ["2026-09-25", "2026-09-26"],
                "temperature_2m_max": [34.0, 33.0],
                "temperature_2m_min": [24.0, 23.0],
                "precipitation_probability_max": [10.0, 60.0],
                "weather_code": [1, 61],
            },
        }),
    ])
    result = weather_service.fetch_weather(_CAIRO, "today")

    assert result.horizon == "today"
    assert result.temperature_low == 24.0
    assert result.temperature_high == 34.0
    assert result.precipitation_probability == 10.0
    assert result.condition == "clear"
    assert result.temperature is None
    assert result.feels_like is None
    assert result.period_start == datetime(2026, 9, 25, 0, 0, tzinfo=ZoneInfo("Africa/Cairo"))
    assert result.period_end == datetime(2026, 9, 25, 23, 59, 59, tzinfo=ZoneInfo("Africa/Cairo"))
    assert calls[0][1]["forecast_days"] == 2
    assert "current" not in calls[0][1]
    assert "hourly" not in calls[0][1]


def test_fetch_weather_tomorrow_normalization_uses_second_daily_index(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_get(monkeypatch, [
        httpx.Response(200, json={
            "timezone": "Africa/Cairo",
            "daily": {
                "time": ["2026-09-25", "2026-09-26"],
                "temperature_2m_max": [34.0, 33.0],
                "temperature_2m_min": [24.0, 23.0],
                "precipitation_probability_max": [10.0, 60.0],
                "weather_code": [1, 61],
            },
        }),
    ])
    result = weather_service.fetch_weather(_CAIRO, "tomorrow")

    assert result.horizon == "tomorrow"
    assert result.temperature_low == 23.0
    assert result.temperature_high == 33.0
    assert result.precipitation_probability == 60.0
    assert result.condition == "rain"
    assert result.period_start == datetime(2026, 9, 26, 0, 0, tzinfo=ZoneInfo("Africa/Cairo"))


# ---- tonight: window computation (pure function) ------------------------------


def test_tonight_window_before_18h_uses_full_window() -> None:
    now_local = datetime(2026, 9, 25, 14, 30, tzinfo=ZoneInfo("Africa/Cairo"))
    start, end = weather_service._tonight_window(now_local)
    assert start == datetime(2026, 9, 25, 18, 0, tzinfo=ZoneInfo("Africa/Cairo"))
    assert end == datetime(2026, 9, 25, 23, 59, 59, tzinfo=ZoneInfo("Africa/Cairo"))


def test_tonight_window_after_18h_starts_at_now() -> None:
    now_local = datetime(2026, 9, 25, 21, 7, tzinfo=ZoneInfo("Africa/Cairo"))
    start, end = weather_service._tonight_window(now_local)
    assert start == now_local
    assert end == datetime(2026, 9, 25, 23, 59, 59, tzinfo=ZoneInfo("Africa/Cairo"))


# ---- tonight: full normalization / aggregation / timezone correctness --------

_TONIGHT_HOURLY = {
    "timezone": "Africa/Cairo",
    "hourly": {
        "time": [f"2026-09-25T{h:02d}:00" for h in range(24)],
        "temperature_2m": [22.0] * 18 + [28.0, 26.0, 24.0, 23.0, 22.0, 21.0],  # indices 18-23
        "precipitation_probability": [0.0] * 18 + [10.0, 70.0, 20.0, 0.0, 0.0, 0.0],
        "weather_code": [0] * 18 + [1, 61, 2, 0, 0, 0],  # 19:00 = rain (61)
    },
}


def test_fetch_weather_tonight_before_18h_uses_full_window(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch, datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc))  # 15:00 Cairo (UTC+3 in Sept 2026)
    calls = _fake_get(monkeypatch, [httpx.Response(200, json=_TONIGHT_HOURLY)])
    result = weather_service.fetch_weather(_CAIRO, "tonight")

    assert result.horizon == "tonight"
    assert result.period_start == datetime(2026, 9, 25, 18, 0, tzinfo=ZoneInfo("Africa/Cairo"))
    assert result.period_end == datetime(2026, 9, 25, 23, 59, 59, tzinfo=ZoneInfo("Africa/Cairo"))
    # temps 18-23h: 28,26,24,23,22,21 -> min 21, max 28
    assert result.temperature_low == 21.0
    assert result.temperature_high == 28.0
    assert result.precipitation_probability == 70.0
    assert result.condition == "rain"  # rain (19:00) outranks partly_cloudy/clear elsewhere in the slice
    assert calls[0][1]["forecast_days"] == 1
    assert "daily" not in calls[0][1]
    assert "current" not in calls[0][1]


def test_fetch_weather_tonight_after_18h_excludes_already_passed_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    # 18:07 UTC = 21:07 Cairo local (Africa/Cairo is UTC+3 in Sept 2026).
    # window_start = max(18:00, 21:07) = 21:07 -> only the 22:00 and
    # 23:00 hourly buckets qualify (t >= 21:07); 18:00/19:00/20:00/21:00
    # are all strictly before 21:07 and must be excluded — in
    # particular the 19:00 RAIN hour, which would otherwise dominate
    # via priority-aggregation, must NOT leak into an already-past
    # "tonight" answer.
    _freeze(monkeypatch, datetime(2026, 9, 25, 18, 7, tzinfo=timezone.utc))
    _fake_get(monkeypatch, [httpx.Response(200, json=_TONIGHT_HOURLY)])
    result = weather_service.fetch_weather(_CAIRO, "tonight")

    assert result.period_start == datetime(2026, 9, 25, 21, 7, tzinfo=ZoneInfo("Africa/Cairo"))
    assert result.temperature_low == 21.0  # 23:00 reading
    assert result.temperature_high == 22.0  # 22:00 reading
    assert result.precipitation_probability == 0.0
    assert result.condition == "clear"  # the 19:00 rain hour is correctly excluded


def test_fetch_weather_tonight_timezone_correctness_not_server_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """The frozen instant is already well past 18:00 UTC — if the code
    mistakenly used a server/UTC clock instead of the resolved
    LOCATION's own timezone, it would compute a partial "now-onward"
    window here. Cairo (UTC+3) is still only 15:00 local at this
    instant, so the CORRECT behavior is the full 18:00-23:59 window —
    proving the resolved location's timezone governs, not UTC/server
    time."""
    _freeze(monkeypatch, datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc))  # 15:00 Cairo -> before 18:00
    _fake_get(monkeypatch, [httpx.Response(200, json=_TONIGHT_HOURLY)])
    result = weather_service.fetch_weather(_CAIRO, "tonight")
    assert result.period_start == datetime(2026, 9, 25, 18, 0, tzinfo=ZoneInfo("Africa/Cairo"))


def test_fetch_weather_tonight_empty_slice_fails_safely(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch, datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc))
    _fake_get(monkeypatch, [httpx.Response(200, json={
        "timezone": "Africa/Cairo",
        "hourly": {"time": ["2026-09-25T05:00"], "temperature_2m": [15.0], "precipitation_probability": [0.0], "weather_code": [0]},
    })])
    with pytest.raises(WeatherProviderError) as exc:
        weather_service.fetch_weather(_CAIRO, "tonight")
    assert exc.value.error_class == "malformed_response"


# ---- condition aggregation / WMO coverage -------------------------------------


def test_aggregate_condition_uses_highest_priority_present() -> None:
    # thunderstorm (95) must win even outnumbered by clear (0)
    assert weather_service._aggregate_condition([0, 0, 0, 95]) == "thunderstorm"
    assert weather_service._aggregate_condition([0, 2, 3]) == "cloudy"
    assert weather_service._aggregate_condition([0]) == "clear"


def test_wmo_mapping_covers_full_documented_code_set() -> None:
    expected_codes = {0, 1, 2, 3, 45, 48, 51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 71, 73, 75, 77, 80, 81, 82, 85, 86, 95, 96, 99}
    assert set(weather_service._WMO_CONDITION_BUCKETS.keys()) == expected_codes
    valid_buckets = {"clear", "partly_cloudy", "cloudy", "fog", "drizzle", "rain", "snow", "thunderstorm"}
    assert set(weather_service._WMO_CONDITION_BUCKETS.values()) <= valid_buckets


def test_unknown_wmo_code_fails_safely_rather_than_inventing_a_condition(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_get(monkeypatch, [httpx.Response(200, json={
        "timezone": "Africa/Cairo",
        "current": {"time": "2026-09-25T15:00", "temperature_2m": 20, "apparent_temperature": 20, "weather_code": 12345},
    })])
    with pytest.raises(WeatherProviderError) as exc:
        weather_service.fetch_weather(_CAIRO, "now")
    assert exc.value.error_class == "malformed_response"


# ---- forecast-level failures ---------------------------------------------------


def test_fetch_weather_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_get(monkeypatch, [httpx.TimeoutException("timed out")])
    with pytest.raises(WeatherProviderError) as exc:
        weather_service.fetch_weather(_CAIRO, "now")
    assert exc.value.error_class == "timeout"


def test_fetch_weather_malformed_missing_current_block(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_get(monkeypatch, [httpx.Response(200, json={"timezone": "Africa/Cairo"})])
    with pytest.raises(WeatherProviderError) as exc:
        weather_service.fetch_weather(_CAIRO, "now")
    assert exc.value.error_class == "malformed_response"
