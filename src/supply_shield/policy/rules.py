"""Identificadores de las reglas que la política sabe configurar."""

from enum import StrEnum


class Rule(StrEnum):
    """Regla que reporta un detector en ``Finding.rule``.

    La política solo acepta estas claves: un nombre mal escrito en el YAML hace
    fallar la carga en vez de caer en silencio en la regla por defecto. Un
    detector nuevo agrega aquí su id y su valor por defecto en ``DEFAULT_RULES``.
    """

    VULNERABILITY = "vulnerability"
    KNOWN_MALWARE = "known_malware"
    TYPOSQUATTING = "typosquatting"
    DEPENDENCY_CONFUSION = "dependency_confusion"
    COOLDOWN = "cooldown"
    INSTALL_SCRIPTS = "install_scripts"
    NEW_PACKAGE = "new_package"


def parse_rule(name: str) -> Rule | None:
    """Devuelve la regla conocida con ese id, o ``None`` si no existe."""
    try:
        return Rule(name)
    except ValueError:
        return None
