"""Configuration.

Settings come from (highest priority first):
  1. environment variables  (``LABKEEPER_*``, nested with ``__``, e.g. ``LABKEEPER_MERAKI__API_KEY``)
  2. a YAML config file      (``labkeeper.yaml`` in the working dir, or ``$LABKEEPER_CONFIG``)
  3. defaults below

``MERAKI_DASHBOARD_API_KEY`` (the variable the official Meraki SDK uses) is honoured as a fallback for
the Meraki API key, and the Anthropic SDK reads ``ANTHROPIC_API_KEY`` / ``ant auth login`` itself.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


class MerakiRegion(BaseModel):
    """A Meraki cloud with its own API host and keys, e.g. Canada (api.meraki.ca), China (api.meraki.cn),
    India (api.meraki.in) or the US government cloud (api.gov-meraki.com)."""

    name: str
    base_url: str
    api_key: str | None = None
    api_key_env: str | None = None  # environment variable that holds the key
    org_ids: list[str] = Field(default_factory=list)  # poll only these (default: every org the key can see)


class MerakiSettings(BaseModel):
    enabled: bool = False
    api_key: str | None = None
    # Read the key from this environment variable instead (lets Labkeeper share an existing
    # credential file without copying the secret), e.g. LAB_MERAKI_API_KEY.
    api_key_env: str | None = None
    base_url: str = "https://api.meraki.com/api/v1"
    # Organizations to poll. When empty, org ids found in testbed files
    # (testbed.custom.dashboard_config.org_id) are used, then every org the key can see.
    org_ids: list[str] = Field(default_factory=list)
    # Other Meraki clouds. Testbed files can also declare one: testbed.custom.meraki_api_base_url with
    # api_key_env (and organization_id), or a custom.geoclouds map of {name: {base_url, api_key_env, organization_id}}.
    regions: list[MerakiRegion] = Field(default_factory=list)
    regions_from_testbeds: bool = True
    poll_interval: int = 60
    inventory_interval: int = 900
    timeout: float = 20.0
    port_cache_seconds: int = 20


class ReachabilitySettings(BaseModel):
    """Generic TCP reachability check for devices that have no richer provider."""

    enabled: bool = True
    poll_interval: int = 120
    timeout: float = 2.0
    ports: list[int] = Field(default_factory=lambda: [22, 443, 23, 830])
    concurrency: int = 64


class TestbedSettings(BaseModel):
    # Directories (or single files) holding pyATS-style testbed YAML.
    paths: list[str] = Field(default_factory=list)
    watch: bool = True
    # Device types that many testbeds share (jump hosts, servers, traffic generators).
    # They are listed in a testbed but never make two bookings conflict.
    shared_device_types: list[str] = Field(
        default_factory=lambda: ["rpi", "linux", "server", "ixia", "ixia-rest", "jump_host", "terminal_server"]
    )
    # How to read a rack + U position out of a device's ``location`` string (e.g. ``B1_C03_U20``).
    location_pattern: str = r"^(?P<rack>.+?)[_\-\s]U(?P<position>\d{1,3})$"
    default_rack_height: int = 42
    exclude: list[str] = Field(default_factory=list)
    # Track only these testbeds: names (testbed.name or file stem) or relative paths, globs allowed.
    # Empty = every testbed file. Devices come only from the testbeds that are tracked.
    include: list[str] = Field(default_factory=list)


class BookingSettings(BaseModel):
    default_duration_minutes: int = 120
    max_duration_days: int = 14
    # "anyone": courtesy model, anybody may release (or cancel) anybody's booking; it is logged under
    # their name. "owner": only the owner, or someone passing force=true when allow_force_release is on.
    release_policy: Literal["anyone", "owner"] = "anyone"
    allow_force_release: bool = True


class ConsoleSettings(BaseModel):
    timeout: float = 8.0
    # Send a carriage return after connecting so an idle console prints its prompt.
    send_newline: bool = True
    # Allow reaching console ports through a jump host (``proxy:`` in the testbed file).
    allow_proxy: bool = True
    verify_host_keys: bool = False


class AgentSettings(BaseModel):
    enabled: bool = False
    model: str = ""
    effort: str = "medium"
    max_tokens: int = 16000
    max_tool_rounds: int = 12
    # Server-side refusal fallbacks (Claude API only; switch off behind proxies that reject betas).
    use_fallbacks: bool = False


class AuthSettings(BaseModel):
    # Header carrying the user name. Put Labkeeper behind an SSO proxy and point this at the header
    # it sets (e.g. ``X-Forwarded-User``) for real identities.
    user_header: str = "X-Labkeeper-User"
    default_user: str = "anonymous"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LABKEEPER_", env_nested_delimiter="__", extra="ignore")

    data_dir: Path = Path("./data")
    database_url: str | None = None
    host: str = "127.0.0.1"
    port: int = 8000
    demo: bool = False
    cors_origins: list[str] = Field(default_factory=list)
    frontend_dist: Path | None = None
    log_level: str = "info"
    # Serve HTTPS directly (e.g. with an existing certificate) instead of behind a proxy.
    tls_certfile: Path | None = None
    tls_keyfile: Path | None = None

    meraki: MerakiSettings = Field(default_factory=MerakiSettings)
    reachability: ReachabilitySettings = Field(default_factory=ReachabilitySettings)
    testbeds: TestbedSettings = Field(default_factory=TestbedSettings)
    booking: BookingSettings = Field(default_factory=BookingSettings)
    console: ConsoleSettings = Field(default_factory=ConsoleSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Environment wins over the YAML file (passed as init kwargs).
        return (env_settings, dotenv_settings, init_settings, file_secret_settings)

    # ---- derived -------------------------------------------------------------------------------------
    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'labkeeper.db').resolve()}"

    @property
    def uploads_dir(self) -> Path:
        return (self.data_dir / "testbeds").resolve()

    @property
    def meraki_api_key(self) -> str | None:
        if self.meraki.api_key:
            return self.meraki.api_key
        if self.meraki.api_key_env and os.environ.get(self.meraki.api_key_env):
            return os.environ[self.meraki.api_key_env]
        return os.environ.get("MERAKI_DASHBOARD_API_KEY") or None

    def testbed_roots(self) -> list[Path]:
        roots = [Path(os.path.expanduser(p)).resolve() for p in self.testbeds.paths]
        if self.demo:
            roots.insert(0, demo_data_dir())
        roots.append(self.uploads_dir)
        seen: list[Path] = []
        for r in roots:
            if r not in seen:
                seen.append(r)
        return seen


def demo_data_dir() -> Path:
    return (Path(__file__).parent / "demo_data").resolve()


def load_settings(config_path: str | os.PathLike[str] | None = None, **overrides: Any) -> Settings:
    path = Path(config_path or os.environ.get("LABKEEPER_CONFIG") or "labkeeper.yaml")
    file_values: dict[str, Any] = {}
    if path.is_file():
        file_values = yaml.safe_load(path.read_text()) or {}
        if not isinstance(file_values, dict):
            raise ValueError(f"{path}: config must be a mapping")
        # Relative paths in the config file are relative to the file itself.
        base = path.resolve().parent
        for key in ("data_dir", "frontend_dist", "tls_certfile", "tls_keyfile"):
            if file_values.get(key):
                file_values[key] = str((base / os.path.expanduser(str(file_values[key]))).resolve())
        tb = file_values.get("testbeds") or {}
        if isinstance(tb, dict) and tb.get("paths"):
            tb["paths"] = [str((base / os.path.expanduser(p)).resolve()) for p in tb["paths"]]
    file_values.update({k: v for k, v in overrides.items() if v is not None})
    return Settings(**file_values)


_settings: Settings | None = None


def configure(settings: Settings) -> Settings:
    global _settings
    _settings = settings
    get_settings.cache_clear()
    return settings


@lru_cache
def get_settings() -> Settings:
    return _settings or load_settings()
