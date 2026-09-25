from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# Checkpoint 3.7: BAZRA's first External Read Tool — grounded factual
# weather only (see weather/service.py's module docstring for the full
# scope boundary). Deliberately no "week"/"hourly" horizon: nothing in
# this checkpoint's evidence (the demonstrated example questions) needs
# either, and general hourly-forecast/weekly-forecast are explicit
# out-of-scope items, not oversights.
WeatherHorizon = Literal["now", "today", "tonight", "tomorrow"]
VALID_WEATHER_HORIZONS: frozenset[str] = frozenset({"now", "today", "tonight", "tomorrow"})

# The 8 condition buckets every Open-Meteo WMO weather_code is
# normalized into — see service.py's _WMO_CONDITION_BUCKETS for the
# full code->bucket mapping and its coverage of the documented WMO set.
WeatherCondition = Literal[
    "clear", "partly_cloudy", "cloudy", "fog", "drizzle", "rain", "snow", "thunderstorm"
]

# Highest to lowest priority — used only to pick the single most
# representative condition across a MULTI-HOUR "tonight" slice (a
# provider-side native single value already exists for today/tomorrow,
# so this ordering is never applied there). This is data normalization
# (never hiding a real rain hour behind a "mostly clear" average), not
# lifestyle/advice reasoning.
CONDITION_PRIORITY: tuple[WeatherCondition, ...] = (
    "thunderstorm", "snow", "rain", "drizzle", "fog", "cloudy", "partly_cloudy", "clear",
)


class GetWeatherArguments(BaseModel):
    """The model's own tool-call arguments, validated by THIS module —
    deliberately never added to actions_service._ACTION_ARGUMENT_SCHEMAS
    (that dict is write-only; get_weather never becomes a
    ProposedAction, so it has no business there). location is required
    (never optional/defaulted) — the model can only produce a call
    here when the user's own message named a place explicitly; see
    chat/service.py's _handle_get_weather and the system prompt's
    "never guess a location" instruction.
    """

    location: str = Field(min_length=1, max_length=100)
    horizon: WeatherHorizon


@dataclass
class ResolvedLocation:
    """What Open-Meteo's Geocoding API resolved the user's free-text
    location string to — display_name is built from name+country for
    the rendered reply; timezone is the location's OWN IANA zone,
    never the requester's — this is what makes "tonight in Cairo"
    correct regardless of where the requesting device actually is.
    """

    display_name: str
    latitude: float
    longitude: float
    timezone: str


@dataclass
class WeatherResult:
    """Checkpoint 3.7's normalized, provider-independent contract — the
    ONLY data the deterministic renderer in chat/service.py is allowed
    to read from. No advice/recommendation field exists here at all;
    that is the structural (not textual) proof that this capability
    cannot produce lifestyle advice — see chat/tests/test_weather.py's
    dataclass-field assertion.

    Field population is governed by "period vs instant", not by
    horizon name directly:
    - "now" is the one INSTANT: temperature/feels_like are populated,
      temperature_low/high and precipitation_probability are None (no
      probability concept, and no confirmed apparent_temperature
      aggregate, exist for a single instant beyond what the provider's
      own `current` block returns).
    - "today"/"tonight"/"tomorrow" are all PERIODS: temperature_low/high
      and precipitation_probability are populated; temperature/
      feels_like are None (a single number would misrepresent a
      multi-hour window).
    """

    resolved_location: str
    horizon: WeatherHorizon
    period_start: datetime
    period_end: datetime | None
    timezone: str
    temperature: float | None
    temperature_low: float | None
    temperature_high: float | None
    feels_like: float | None
    condition: WeatherCondition
    precipitation_probability: float | None
    units: Literal["C", "F"]


# Internal error classification — never shown to the user verbatim
# (chat/service.py collapses all of these except location_not_found
# into one truthful "can't reach the weather service" reply); used only
# for the structured, payload-free operational log line in service.py.
WeatherErrorClass = Literal[
    "location_not_found",
    "timeout",
    "connection_error",
    "provider_error_client",
    "provider_error_server",
    "rate_limited",
    "malformed_response",
]


class WeatherProviderError(Exception):
    def __init__(self, error_class: WeatherErrorClass):
        self.error_class: WeatherErrorClass = error_class
        super().__init__(error_class)
