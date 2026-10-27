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

    # Heuristic risk rules. Thresholds live here so they can be changed without
    # touching the rule implementations.
    heuristics_cooldown_days: int = Field(default=30, ge=1)
    heuristics_new_package_days: int = Field(default=90, ge=1)
    heuristics_new_package_max_versions: int = Field(default=5, ge=1)

    # Base de datos PostgreSQL y Redis.
    database_url: str = Field(
        default="postgresql+asyncpg://supply_shield:supply_shield@localhost:5432/supply_shield"
    )
    redis_url: str = Field(default="redis://localhost:6379/0")

    # Directorio para almacenamiento de artefactos direccionado por hash.
    artifacts_dir: str = Field(default=".artifacts")


@lru_cache
def get_settings() -> Settings:
    """Devuelve la instancia de ajustes de todo el proceso."""
    return Settings()
