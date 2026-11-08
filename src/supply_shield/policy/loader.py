"""Lectura y validación del archivo YAML de política."""

from pathlib import Path

import yaml
from pydantic import ValidationError

from supply_shield.policy.models import Policy


class PolicyLoadError(ValueError):
    """El archivo de política existe pero no es YAML válido o no cumple el esquema."""


def parse_policy(text: str, *, source: str = "<string>") -> Policy:
    """Valida una política escrita en YAML. Un documento vacío es la política por defecto."""
    try:
        data: object = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        msg = f"policy {source} is not valid YAML: {exc}"
        raise PolicyLoadError(msg) from exc
    try:
        return Policy.model_validate({} if data is None else data)
    except ValidationError as exc:
        msg = f"policy {source} is invalid: {exc}"
        raise PolicyLoadError(msg) from exc


def load_policy(path: Path) -> Policy:
    """Carga la política desde ``path``. ``FileNotFoundError`` se propaga a quien llama."""
    return parse_policy(path.read_text(encoding="utf-8"), source=str(path))
