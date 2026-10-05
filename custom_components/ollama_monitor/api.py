"""Tiny async client for the bits of the Ollama HTTP API we care about."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
import json
import re
from typing import Any

import aiohttp
from homeassistant.util import dt as dt_util
from yarl import URL

from .const import DEFAULT_PORT, LOAD_TIMEOUT, PINNED_THRESHOLD_DAYS, REQUEST_TIMEOUT


class OllamaError(Exception):
    """Base error talking to Ollama."""


class OllamaConnectionError(OllamaError):
    """Ollama could not be reached."""


class OllamaAuthError(OllamaError):
    """Ollama (or the proxy in front of it) rejected our credentials."""


class OllamaResponseError(OllamaError):
    """Ollama answered with an error."""

    def __init__(self, status: int, message: str) -> None:
        """Store status and message."""
        super().__init__(message)
        self.status = status
        self.message = message


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class LoadedModel:
    """A model currently resident in memory (/api/ps)."""

    name: str
    size: int
    size_vram: int
    context_length: int | None
    expires_at: datetime | None
    pinned: bool
    family: str | None
    parameter_size: str | None
    quantization: str | None

    @property
    def gpu_percent(self) -> int:
        """Share of the model that lives in VRAM."""
        if self.size <= 0:
            return 0
        return round(100 * min(self.size_vram, self.size) / self.size)

    @property
    def processor(self) -> str:
        """Same wording as `ollama ps`."""
        gpu = self.gpu_percent
        if gpu >= 100:
            return "100% GPU"
        if gpu <= 0:
            return "100% CPU"
        return f"{100 - gpu}%/{gpu}% CPU/GPU"

    def as_attr(self) -> dict[str, Any]:
        """Serialise for a state attribute (and the card)."""
        return {
            "name": self.name,
            "size": self.size,
            "size_vram": self.size_vram,
            "gpu_percent": self.gpu_percent,
            "processor": self.processor,
            "context_length": self.context_length,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "pinned": self.pinned,
            "family": self.family,
            "parameter_size": self.parameter_size,
            "quantization": self.quantization,
        }


@dataclass(slots=True)
class InstalledModel:
    """A model available on disk (/api/tags)."""

    name: str
    size: int
    modified_at: str | None
    family: str | None
    parameter_size: str | None
    quantization: str | None
    remote: bool

    def as_attr(self) -> dict[str, Any]:
        """Serialise for a state attribute (and the card)."""
        return asdict(self)


@dataclass(slots=True)
class OllamaStatus:
    """Everything one poll knows about a host."""

    online: bool
    version: str | None = None
    loaded: list[LoadedModel] = field(default_factory=list)
    installed: list[InstalledModel] = field(default_factory=list)

    @property
    def vram_used(self) -> int:
        """Total VRAM used by loaded models, in bytes."""
        return sum(m.size_vram for m in self.loaded)

    @property
    def memory_used(self) -> int:
        """Total memory (VRAM + RAM) used by loaded models, in bytes."""
        return sum(m.size for m in self.loaded)

    @property
    def disk_used(self) -> int:
        """Total size of installed models, in bytes."""
        return sum(m.size for m in self.installed)

    @property
    def primary_model(self) -> LoadedModel | None:
        """The most recently used model.

        Ollama pushes ``expires_at`` forward after every request, so the latest
        deadline is the model that answered last. Pinned models (keep_alive < 0)
        have no deadline; they only win when nothing else is loaded, so a pinned
        embedder doesn't hide the chat model you're actually using.
        """
        timed = [m for m in self.loaded if not m.pinned and m.expires_at]
        if timed:
            return max(timed, key=lambda m: m.expires_at)
        return self.loaded[0] if self.loaded else None

    @property
    def next_unload(self) -> datetime | None:
        """Soonest moment a (non-pinned) model will be evicted."""
        times = [m.expires_at for m in self.loaded if m.expires_at and not m.pinned]
        return min(times) if times else None


def _parse_time(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    parsed = dt_util.parse_datetime(raw)
    if parsed is None:
        # Go can emit nanoseconds; Python only does micro. Trim the extra digits.
        trimmed = re.sub(r"(\.\d{6})\d+", r"\1", raw)
        parsed = dt_util.parse_datetime(trimmed)
    if parsed is None or parsed.year < 1970:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.UTC)
    return parsed


def parse_loaded(raw: dict[str, Any], now: datetime | None = None) -> LoadedModel:
    """Turn one /api/ps entry into a LoadedModel."""
    now = now or dt_util.utcnow()
    details = raw.get("details") or {}
    expires_at = _parse_time(raw.get("expires_at"))
    pinned = bool(expires_at and expires_at - now > timedelta(days=PINNED_THRESHOLD_DAYS))
    return LoadedModel(
        name=str(raw.get("name") or raw.get("model") or "unknown"),
        size=int(raw.get("size") or 0),
        size_vram=int(raw.get("size_vram") or 0),
        context_length=raw.get("context_length"),
        expires_at=None if pinned else expires_at,
        pinned=pinned,
        family=details.get("family"),
        parameter_size=details.get("parameter_size"),
        quantization=details.get("quantization_level"),
    )


def parse_installed(raw: dict[str, Any]) -> InstalledModel:
    """Turn one /api/tags entry into an InstalledModel."""
    details = raw.get("details") or {}
    return InstalledModel(
        name=str(raw.get("name") or raw.get("model") or "unknown"),
        size=int(raw.get("size") or 0),
        modified_at=raw.get("modified_at"),
        family=details.get("family"),
        parameter_size=details.get("parameter_size"),
        quantization=details.get("quantization_level"),
        remote=bool(raw.get("remote_host") or raw.get("remote_model")),
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

_GO_DURATION = re.compile(r"^(\d+(\.\d+)?(ns|us|µs|ms|s|m|h))+$")
_FOREVER = {"forever", "inf", "infinite", "∞", "pin", "pinned", "never"}


def parse_keep_alive(value: Any) -> int | str | None:  # noqa: PLR0911
    """Normalise a keep-alive value into something Ollama accepts.

    Returns None for "use the server default", -1 for "keep forever",
    an int for seconds, or a Go duration string such as "10m" / "1h30m".
    Raises ValueError for anything else.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("keep_alive must be a duration, not a boolean")
    if isinstance(value, (int, float)):
        return -1 if value < 0 else int(value)
    text = str(value).strip().lower().replace(" ", "")
    if text in ("", "default"):
        return None
    if text in _FOREVER:
        return -1
    if re.fullmatch(r"-\d+(\.\d+)?[a-zµ]*", text):
        return -1
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return int(float(text))
    if _GO_DURATION.fullmatch(text):
        return text
    # Friendly extras: "30min", "2hours", "1day"
    friendly = re.fullmatch(
        r"(\d+(?:\.\d+)?)(sec|secs|second|seconds|min|mins|minute|minutes|hr|hrs|hour|hours|d|day|days)", text
    )
    if friendly:
        amount = float(friendly.group(1))
        unit = friendly.group(2)
        if unit.startswith("s"):
            return int(amount)
        if unit.startswith("m"):
            return int(amount * 60)
        if unit.startswith("h"):
            return int(amount * 3600)
        return int(amount * 86400)
    raise ValueError(f"Unrecognised keep_alive value: {value!r}")


def normalize_url(raw: str) -> str:
    """Clean up what the user typed into something we can request.

    No scheme → assume http and, if no port was given, the default Ollama port.
    With a scheme we take the URL as-is, so reverse-proxied https URLs without a
    port keep working. A trailing /api or /v1 is dropped.
    """
    text = raw.strip()
    if not text:
        raise ValueError("empty url")
    if "://" not in text:
        parsed = URL(f"http://{text}")
        if parsed.explicit_port is None:
            parsed = parsed.with_port(DEFAULT_PORT)
    else:
        parsed = URL(text)
    if parsed.scheme not in ("http", "https") or not parsed.host:
        raise ValueError(f"not an http(s) url: {raw!r}")
    path = parsed.path.rstrip("/")
    for suffix in ("/api", "/v1"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
    parsed = parsed.with_path(path or "/").with_query(None).with_fragment(None)
    return str(parsed).rstrip("/")


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #


class OllamaClient:
    """Minimal Ollama API client using Home Assistant's aiohttp session."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str,
        api_key: str | None = None,
    ) -> None:
        """Set up the client."""
        self._session = session
        self.url = url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key.strip()}"} if api_key and api_key.strip() else {}

    async def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        request_timeout: float = REQUEST_TIMEOUT,
    ) -> Any:
        try:
            async with self._session.request(
                method,
                f"{self.url}{path}",
                json=payload,
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=request_timeout),
            ) as resp:
                if resp.status in (401, 403):
                    raise OllamaAuthError(f"HTTP {resp.status}")
                text = await resp.text()
                if resp.status >= 400:
                    message = text
                    try:
                        body = _json(text)
                        if isinstance(body, dict) and body.get("error"):
                            message = body["error"]
                    except ValueError:
                        pass
                    raise OllamaResponseError(resp.status, str(message).strip() or f"HTTP {resp.status}")
                try:
                    return _json(text)
                except ValueError as err:
                    raise OllamaResponseError(
                        resp.status, "Response was not JSON — is this really Ollama?"
                    ) from err
        except TimeoutError as err:
            raise OllamaConnectionError(f"Timed out talking to {self.url}") from err
        except aiohttp.ClientError as err:
            raise OllamaConnectionError(str(err) or err.__class__.__name__) from err

    async def version(self) -> str:
        """Return the server version string."""
        data = await self._request("GET", "/api/version")
        if not isinstance(data, dict) or "version" not in data:
            raise OllamaResponseError(200, "Unexpected /api/version response — is this really Ollama?")
        return str(data["version"])

    async def ps(self) -> list[LoadedModel]:
        """Return models currently in memory."""
        data = await self._request("GET", "/api/ps")
        now = dt_util.utcnow()
        return [parse_loaded(m, now) for m in (data or {}).get("models") or []]

    async def tags(self) -> list[InstalledModel]:
        """Return installed models, biggest first."""
        data = await self._request("GET", "/api/tags")
        models = [parse_installed(m) for m in (data or {}).get("models") or []]
        return sorted(models, key=lambda m: m.name.lower())

    async def load(self, model: str, keep_alive: int | str | None) -> None:
        """Load a model into memory (optionally pinning it for a while)."""
        payload: dict[str, Any] = {"model": model, "stream": False}
        if keep_alive is not None:
            payload["keep_alive"] = keep_alive
        try:
            await self._request("POST", "/api/generate", payload, request_timeout=LOAD_TIMEOUT)
        except OllamaResponseError as err:
            # Embedding-only models refuse /api/generate; warm them via /api/embed.
            if err.status == 400 and "does not support" in err.message.lower():
                embed: dict[str, Any] = {"model": model, "input": "."}
                if keep_alive is not None:
                    embed["keep_alive"] = keep_alive
                await self._request("POST", "/api/embed", embed, request_timeout=LOAD_TIMEOUT)
                return
            raise

    async def unload(self, model: str) -> None:
        """Evict a model from memory right now."""
        await self._request(
            "POST",
            "/api/generate",
            {"model": model, "keep_alive": 0, "stream": False},
            request_timeout=60,
        )


def _json(text: str) -> Any:
    try:
        return json.loads(text) if text else {}
    except json.JSONDecodeError as err:
        raise ValueError("not json") from err
