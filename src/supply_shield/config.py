"""Ajustes de la aplicación, cargados desde variables de entorno ``SUPPLY_SHIELD_*``."""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator
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

    # Project authentication: YAML file with project names and hashed tokens.
    projects_file: Path = Field(default=Path("config/projects.yaml"))

    # Política de bloqueo (D4-3). Si el archivo no existe se usa la política de
    # fábrica; si existe pero es inválido, la aplicación no arranca.
    policy_file: Path = Field(default=Path("config/policy.yaml"))

    # Detector de typosquatting (D4-4). Sin las listas o con un archivo inválido, la
    # aplicación no arranca. Los umbrales traducen el puntaje 0-1 a una severidad.
    popular_packages_dir: Path = Field(default=Path("data/popular"))
    typosquatting_allowlist_file: Path = Field(default=Path("data/typosquatting/allowlist.yaml"))
    typosquatting_report_threshold: float = Field(default=0.60, ge=0, le=1)
    typosquatting_medium_threshold: float = Field(default=0.70, ge=0, le=1)
    typosquatting_high_threshold: float = Field(default=0.85, ge=0, le=1)
    typosquatting_critical_threshold: float = Field(default=0.95, ge=0, le=1)

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

    @model_validator(mode="after")
    def _typosquatting_thresholds_ascend(self) -> Self:
        thresholds = (
            self.typosquatting_report_threshold,
            self.typosquatting_medium_threshold,
            self.typosquatting_high_threshold,
            self.typosquatting_critical_threshold,
        )
        if list(thresholds) != sorted(thresholds):
            msg = "typosquatting thresholds must satisfy report <= medium <= high <= critical"
            raise ValueError(msg)
        return self
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
