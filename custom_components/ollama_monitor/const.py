"""Constants for the Ollama Monitor integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "ollama_monitor"
NAME: Final = "Ollama Monitor"
VERSION: Final = "0.1.0"

DEFAULT_PORT: Final = 11434
DEFAULT_SCAN_INTERVAL: Final = 10  # seconds
MIN_SCAN_INTERVAL: Final = 2
MAX_SCAN_INTERVAL: Final = 3600

# How often the slower-moving bits (installed models, version) are re-fetched.
SLOW_REFRESH_SECONDS: Final = 60

REQUEST_TIMEOUT: Final = 10  # seconds, for status polling
LOAD_TIMEOUT: Final = 600  # seconds, loading a big model can take a while

CONF_API_KEY: Final = "api_key"
CONF_KEEP_ALIVE: Final = "keep_alive"
CONF_MODEL: Final = "model"
CONF_SCAN_INTERVAL: Final = "scan_interval"

ATTR_DEVICE_ID: Final = "device_id"
ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"

SERVICE_LOAD_MODEL: Final = "load_model"
SERVICE_UNLOAD_MODEL: Final = "unload_model"

# Anything expiring further out than this is treated as "pinned" (keep_alive < 0).
PINNED_THRESHOLD_DAYS: Final = 365 * 10

CARD_FILENAME: Final = "ollama-monitor-card.js"
CARD_URL_BASE: Final = f"/{DOMAIN}_static"
