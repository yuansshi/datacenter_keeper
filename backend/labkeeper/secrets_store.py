"""In-memory store for credentials found in testbed files.

Passwords in testbed YAML are needed to open a console through a jump host, but they never touch
the database, the API, the UI or the LLM. They are re-read from the files on every import
(which also happens at start-up), so nothing sensitive is persisted by Labkeeper itself.
"""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass, field
from typing import Any

SENSITIVE_KEY_RE = re.compile(
    r"(pass(word|wd|phrase)?|secret|token|api[_-]?key|private[_-]?key|community|auth[_-]?key|priv[_-]?key)",
    re.IGNORECASE,
)
REDACTED = "••••••"
ENV_RE = re.compile(r"%ENV\{([A-Za-z_][A-Za-z0-9_]*)\}")


def is_sensitive_key(key: str) -> bool:
    return bool(SENSITIVE_KEY_RE.search(str(key)))


ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]{1,127}$")


def _names_a_variable(key: Any, value: Any) -> bool:
    k = str(key).lower()
    return (k.endswith("_env") or k.endswith("_env_var")) and isinstance(value, str) and bool(ENV_NAME_RE.match(value))


def redact(value: Any) -> Any:
    """Deep copy with sensitive values replaced. Keys are kept so the UI can show they exist."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if _names_a_variable(k, v):
                out[k] = v  # e.g. api_key_env: CANADA_MERAKI_API_KEY names where the secret is, it is not one
            elif is_sensitive_key(k) and not isinstance(v, dict | list):
                out[k] = REDACTED if v not in (None, "") else v
            elif str(k).lower() == "enable" and isinstance(v, str):
                out[k] = REDACTED
            else:
                out[k] = redact(v)
        return out
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def resolve_secret(value: Any) -> str | None:
    """Resolve pyATS ``%ENV{VAR}`` markup. ``%ENC{...}`` (pyATS-encrypted) is not decodable here."""
    if value is None:
        return None
    text = str(value)
    if text.startswith("%ENC{"):
        return None
    return ENV_RE.sub(lambda m: os.environ.get(m.group(1), ""), text)


@dataclass
class ProxyPlan:
    name: str
    host: str
    port: int = 22
    username: str | None = None
    password: str | None = None
    password_encrypted: bool = False


@dataclass
class ConnectionPlan:
    """Everything needed to open one connection of one device."""

    name: str
    protocol: str
    host: str | None
    port: int | None
    proxy: ProxyPlan | None = None
    proxy_error: str | None = None
    is_console: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


class SecretStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._plans: dict[str, dict[str, ConnectionPlan]] = {}

    def replace_all(self, plans: dict[str, dict[str, ConnectionPlan]]) -> None:
        with self._lock:
            self._plans = dict(plans)

    def update(self, device_key: str, plans: dict[str, ConnectionPlan]) -> None:
        with self._lock:
            self._plans[device_key] = plans

    def plans_for(self, device_key: str) -> dict[str, ConnectionPlan]:
        with self._lock:
            return dict(self._plans.get(device_key, {}))


secret_store = SecretStore()
