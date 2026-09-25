"""BAZRA's first External Read Tool (Checkpoint 3.7) — grounded factual
weather only. Provider: Open-Meteo Forecast API + Open-Meteo Geocoding
API (https://open-meteo.com/), chosen for its free, keyless,
non-commercial-use tier (10,000 calls/day) — see the module-level
licensing note below.

Flow: geocode_location(free-text place name) -> ResolvedLocation ->
fetch_weather(resolved, horizon) -> WeatherResult. chat/service.py's
_handle_get_weather is the only caller; the deterministic renderer
there reads ONLY WeatherResult's own fields — this module never
produces, and WeatherResult never carries, any advice/recommendation
content (see schemas.py's docstring).

LICENSING (REQUIRED INVARIANT): the free endpoints used here require no
API key but are documented as NON-COMMERCIAL USE ONLY, and their data
is CC BY 4.0 licensed (attribution required — see chat/service.py's
_WEATHER_ATTRIBUTION_LINE, appended to every successful reply). If
BAZRA's deployment model ever becomes commercial, this provider
integration must be explicitly revisited (a paid Open-Meteo
subscription + API key + a different host, or a different provider
entirely) — never silently assumed to still be valid.
"""

import logging
import time
from datetime import date, datetime, time as dtime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from app.modules.weather.schemas import (
    CONDITION_PRIORITY,
    ResolvedLocation,
    WeatherCondition,
    WeatherErrorClass,
    WeatherHorizon,
    WeatherProviderError,
    WeatherResult,
)

logger = logging.getLogger(__name__)

# Fixed, code-owned hosts — never model-supplied, never constructed from
# a model-generated string. The ONLY model-controlled input is the
# `location` value, which is always passed through httpx's own
# query-parameter encoding (`params=`), never string-concatenated into
# a URL.
_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

_TIMEOUT_SECONDS = 5.0

# The complete, documented Open-Meteo/WMO weather_code set, normalized
# into the 8 fixed condition buckets. Unknown/unlisted codes fail safely
# (malformed_response) rather than inventing a condition — see
# _bucket_for_code.
_WMO_CONDITION_BUCKETS: dict[int, WeatherCondition] = {
    0: "clear",
    1: "clear",
    2: "partly_cloudy",
    3: "cloudy",
    45: "fog",
    48: "fog",
    51: "drizzle",
    53: "drizzle",
    55: "drizzle",
    56: "drizzle",
    57: "drizzle",
    61: "rain",
    63: "rain",
    65: "rain",
    66: "rain",
    67: "rain",
    71: "snow",
    73: "snow",
    75: "snow",
    77: "snow",
    80: "rain",
    81: "rain",
    82: "rain",
    85: "snow",
    86: "snow",
    95: "thunderstorm",
    96: "thunderstorm",
    99: "thunderstorm",
}

_CONDITION_PRIORITY_INDEX = {bucket: index for index, bucket in enumerate(CONDITION_PRIORITY)}


def _log(capability: str, status: str, latency_ms: int, error_class: str | None = None) -> None:
    """Structured, payload-free operational log — never location,
    coordinates, or any provider payload content. AiTrace stays
    LLM-call-only; this is the weather-equivalent observability,
    deliberately just a log line, not a new table (Checkpoint 3.6/3.7
    review: no current consumer for one)."""
    logger.info(
        "weather: provider=open-meteo capability=%s status=%s latency_ms=%d error_class=%s",
        capability, status, latency_ms, error_class,
    )


def _zone(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as exc:
        raise WeatherProviderError("malformed_response") from exc


def _classify_http_error(response: httpx.Response) -> WeatherErrorClass | None:
    if response.status_code == 429:
        return "rate_limited"
    if 400 <= response.status_code < 500:
        return "provider_error_client"
    if response.status_code >= 500:
        return "provider_error_server"
    return None


def _get(url: str, params: dict, capability: str) -> dict:
    """The one function that actually talks HTTP — both geocode_location
    and fetch_weather funnel through this for identical timeout/error-
    classification/logging behavior. Never raises anything but
    WeatherProviderError."""
    start = time.monotonic()
    try:
        response = httpx.get(url, params=params, timeout=_TIMEOUT_SECONDS)
    except httpx.TimeoutException as exc:
        latency_ms = int((time.monotonic() - start) * 1000)
        _log(capability, "error", latency_ms, "timeout")
        raise WeatherProviderError("timeout") from exc
    except httpx.HTTPError as exc:
        latency_ms = int((time.monotonic() - start) * 1000)
        _log(capability, "error", latency_ms, "connection_error")
        raise WeatherProviderError("connection_error") from exc

    latency_ms = int((time.monotonic() - start) * 1000)
    error_class = _classify_http_error(response)
    if error_class is not None:
        _log(capability, "error", latency_ms, error_class)
        raise WeatherProviderError(error_class)

    try:
        data = response.json()
    except ValueError as exc:
        _log(capability, "error", latency_ms, "malformed_response")
        raise WeatherProviderError("malformed_response") from exc

    _log(capability, "success", latency_ms)
    return data


def geocode_location(location: str) -> ResolvedLocation | None:
    """count=1: the top (best-ranked) match only — no disambiguation UI
    in this MVP (see Checkpoint 3.7 architecture review). Returns None
    only for a genuine zero-result lookup (the caller renders the
    location_not_found reply); any other failure raises
    WeatherProviderError."""
    data = _get(_GEOCODING_URL, {"name": location, "count": 1, "language": "en", "format": "json"}, "geocoding")

    results = data.get("results")
    if not results:
        return None

    top = results[0]
    try:
        name = top["name"]
        country = top.get("country") or ""
        latitude = float(top["latitude"])
        longitude = float(top["longitude"])
        tz_name = top["timezone"]
    except (KeyError, TypeError, ValueError) as exc:
        raise WeatherProviderError("malformed_response") from exc

    _zone(tz_name)  # validated eagerly so a bad zone fails here, not deep inside fetch_weather

    display_name = f"{name}, {country}" if country else name
    return ResolvedLocation(display_name=display_name, latitude=latitude, longitude=longitude, timezone=tz_name)


def _forecast_params(resolved: ResolvedLocation, horizon: WeatherHorizon) -> dict:
    """Minimum-necessary request shape per horizon (Checkpoint 3.7
    correction): never requests current+daily+hourly together, and
    never sends forecast_days without a block that actually consumes
    it — "now" has no forecast_days consumer at all (confirmed against
    the official docs: forecast_days is independent of `current` and
    defaults to 7 only when daily/hourly are requested without it)."""
    params: dict = {"latitude": resolved.latitude, "longitude": resolved.longitude, "timezone": resolved.timezone}
    if horizon == "now":
        params["current"] = "temperature_2m,apparent_temperature,weather_code"
    elif horizon in ("today", "tomorrow"):
        params["daily"] = "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code"
        params["forecast_days"] = 2
    else:  # tonight
        params["hourly"] = "temperature_2m,precipitation_probability,weather_code"
        params["forecast_days"] = 1
    return params


def _bucket_for_code(code: int) -> WeatherCondition:
    bucket = _WMO_CONDITION_BUCKETS.get(code)
    if bucket is None:
        raise WeatherProviderError("malformed_response")
    return bucket


def _aggregate_condition(codes: list[int]) -> WeatherCondition:
    """The highest-priority bucket present across a multi-hour slice —
    normalization, not advice: a single rainy hour inside an otherwise
    clear evening must never be averaged away into "clear". See
    schemas.py's CONDITION_PRIORITY for the fixed ordering."""
    buckets = {_bucket_for_code(code) for code in codes}
    return min(buckets, key=lambda bucket: _CONDITION_PRIORITY_INDEX[bucket])


def _parse_local(time_str: str, zone: ZoneInfo) -> datetime:
    """Open-Meteo returns daily/hourly/current timestamps as local
    wall-clock strings with NO UTC offset when an explicit `timezone`
    was requested (never "auto" here) — so this attaches the resolved
    location's own zone directly, it does not convert from UTC."""
    try:
        return datetime.fromisoformat(time_str).replace(tzinfo=zone)
    except ValueError as exc:
        raise WeatherProviderError("malformed_response") from exc


def _parse_now(data: dict, resolved: ResolvedLocation) -> WeatherResult:
    try:
        current = data["current"]
        temperature = float(current["temperature_2m"])
        feels_like = float(current["apparent_temperature"])
        code = int(current["weather_code"])
        time_str = current["time"]
    except (KeyError, TypeError, ValueError) as exc:
        raise WeatherProviderError("malformed_response") from exc

    zone = _zone(resolved.timezone)
    return WeatherResult(
        resolved_location=resolved.display_name,
        horizon="now",
        period_start=_parse_local(time_str, zone),
        period_end=None,
        timezone=resolved.timezone,
        temperature=temperature,
        temperature_low=None,
        temperature_high=None,
        feels_like=feels_like,
        condition=_bucket_for_code(code),
        precipitation_probability=None,
        units="C",
    )


def _parse_daily(data: dict, resolved: ResolvedLocation, horizon: WeatherHorizon) -> WeatherResult:
    index = 0 if horizon == "today" else 1
    try:
        daily = data["daily"]
        date_str = daily["time"][index]
        temp_max = float(daily["temperature_2m_max"][index])
        temp_min = float(daily["temperature_2m_min"][index])
        precip_prob = float(daily["precipitation_probability_max"][index])
        code = int(daily["weather_code"][index])
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise WeatherProviderError("malformed_response") from exc

    zone = _zone(resolved.timezone)
    try:
        day = date.fromisoformat(date_str)
    except ValueError as exc:
        raise WeatherProviderError("malformed_response") from exc

    return WeatherResult(
        resolved_location=resolved.display_name,
        horizon=horizon,
        period_start=datetime.combine(day, dtime(0, 0), tzinfo=zone),
        period_end=datetime.combine(day, dtime(23, 59, 59), tzinfo=zone),
        timezone=resolved.timezone,
        temperature=None,
        temperature_low=temp_min,
        temperature_high=temp_max,
        feels_like=None,
        condition=_bucket_for_code(code),
        precipitation_probability=precip_prob,
        units="C",
    )


def _tonight_window(now_local: datetime) -> tuple[datetime, datetime]:
    """Checkpoint 3.7 correction: never describes already-passed hours.
    Before 18:00 -> the full 18:00-23:59:59 window. At/after 18:00 ->
    starts at now_local itself; the hourly-slice filter below (t >=
    window_start) then naturally selects the first provider bucket AT
    OR AFTER the current moment, since bucket timestamps are discrete
    on-the-hour marks and any bucket strictly before now_local compares
    less than it."""
    zone = now_local.tzinfo
    today = now_local.date()
    eighteen = datetime.combine(today, dtime(18, 0), tzinfo=zone)
    window_end = datetime.combine(today, dtime(23, 59, 59), tzinfo=zone)
    window_start = max(eighteen, now_local)
    return window_start, window_end


def _parse_tonight(data: dict, resolved: ResolvedLocation) -> WeatherResult:
    try:
        hourly = data["hourly"]
        times = hourly["time"]
        temps = hourly["temperature_2m"]
        precip_probs = hourly["precipitation_probability"]
        codes = hourly["weather_code"]
    except (KeyError, TypeError) as exc:
        raise WeatherProviderError("malformed_response") from exc

    zone = _zone(resolved.timezone)
    now_local = datetime.now(timezone.utc).astimezone(zone)
    window_start, window_end = _tonight_window(now_local)

    sliced_indices = [
        i for i, t_str in enumerate(times) if window_start <= _parse_local(t_str, zone) <= window_end
    ]
    if not sliced_indices:
        # No hourly bucket falls inside the window at all (e.g. a
        # malformed/short provider response) — never fabricate a
        # tonight forecast from nothing.
        raise WeatherProviderError("malformed_response")

    try:
        sliced_temps = [float(temps[i]) for i in sliced_indices]
        sliced_probs = [float(precip_probs[i]) for i in sliced_indices]
        sliced_codes = [int(codes[i]) for i in sliced_indices]
    except (TypeError, ValueError, IndexError) as exc:
        raise WeatherProviderError("malformed_response") from exc

    return WeatherResult(
        resolved_location=resolved.display_name,
        horizon="tonight",
        period_start=window_start,
        period_end=window_end,
        timezone=resolved.timezone,
        temperature=None,
        temperature_low=min(sliced_temps),
        temperature_high=max(sliced_temps),
        feels_like=None,
        condition=_aggregate_condition(sliced_codes),
        precipitation_probability=max(sliced_probs),
        units="C",
    )


def fetch_weather(resolved: ResolvedLocation, horizon: WeatherHorizon) -> WeatherResult:
    data = _get(_FORECAST_URL, _forecast_params(resolved, horizon), "forecast")
    if horizon == "now":
        return _parse_now(data, resolved)
    if horizon in ("today", "tomorrow"):
        return _parse_daily(data, resolved, horizon)
    return _parse_tonight(data, resolved)
