"""Configuration — pydantic-settings singleton, env prefix ``RTWATERFLOW_``.

All fields are documented in ``.env.example``. Mirrors the blueprint's
``config.py``: one Settings class, ``.env`` support, ``extra="ignore"``,
module-level singleton accessor.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RTWATERFLOW_", env_file=".env", extra="ignore"
    )

    # --- paths ---
    data_dir: Path = Path("./data")
    # network loaded at startup (``<data_dir>/networks/<id>``); tests load
    # their fixture explicitly
    default_network: str = "tutorial_hillside"
    network_library: Path = Path("./data/network_library.json")
    user_networks_dir: Path = Path("./data/user_networks")
    scenarios_dir: Path = Path("./data/scenarios")
    recordings_dir: Path = Path("./data/recordings")

    # --- engine ---
    step_interval_seconds: float = 1.0  # wall-clock seconds per simulated step
    steps_per_day: int = 1440           # one-minute steps
    autostart: bool = True
    history_size: int = 1440

    # --- solver ---
    solver_iter: int = 100  # base iter for retry-ladder tier 1; tiers 2/3 use 3x

    # --- observability ---
    expose_ground_truth: bool = True  # false = strict mode (ground truth stripped)

    # --- server ---
    host: str = "127.0.0.1"
    # sibling port scheme: 8002 (UI dev 5175) so rtwaterflow can run next to
    # rtheatflow (8001/5174) and netzsim/rtpowerflow (8000/5173) on the same
    # machine
    port: int = 8002
    log_level: str = "info"
    cors_origins: str = "*"
    record: bool = False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton accessor (blueprint convention)."""
    return Settings()
