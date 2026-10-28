"""Ajustes de la aplicación, cargados desde variables de entorno ``SUPPLY_SHIELD_*``."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración de ejecución: única fuente de cada valor ajustable."""

    model_config = SettingsConfigDict(
        env_prefix="SUPPLY_SHIELD_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["development", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # In-memory cache. Findings live longer than metadata or decisions because
    # advisory data changes on the order of hours, not minutes.
    cache_max_entries: int = Field(default=1024, ge=1)
    cache_metadata_ttl_seconds: int = Field(default=300, ge=1)
    cache_findings_ttl_seconds: int = Field(default=21_600, ge=1)
    cache_decisions_ttl_seconds: int = Field(default=300, ge=1)

    # Cliente del registro upstream (D1-1). La allowlist de hosts es la frontera SSRF:
    # toda petición saliente debe apuntar a uno de estos hosts. Se override por env
    # como un array JSON, p. ej. SUPPLY_SHIELD_UPSTREAM_HOSTS='["registry.npmjs.org"]'.
    upstream_hosts: tuple[str, ...] = (
        "registry.npmjs.org",
        "pypi.org",
        "files.pythonhosted.org",
    )
    upstream_max_response_bytes: int = Field(default=536_870_912, ge=1)  # 512 MiB
    upstream_connect_timeout_seconds: float = Field(default=10.0, gt=0)
    upstream_read_timeout_seconds: float = Field(default=30.0, gt=0)
    upstream_max_retries: int = Field(default=3, ge=0)
    upstream_backoff_factor: float = Field(default=0.5, gt=0)
    upstream_max_connections: int = Field(default=100, ge=1)


@lru_cache
def get_settings() -> Settings:
    """Devuelve la instancia de ajustes de todo el proceso."""
    return Settings()
