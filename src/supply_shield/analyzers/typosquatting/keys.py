"""Claves de comparación de nombres: separadores, letras parecidas, scope y afijos.

Todas las funciones reciben nombres ya normalizados (``normalize_package_name``),
así que trabajan sobre minúsculas.
"""

import re

_SEPARATORS = re.compile(r"[-_.@/]")

# Secuencias que se leen igual en la mayoría de las fuentes. Solo se usan las que
# aparecen en ataques reales y casi nunca en nombres legítimos parecidos entre sí;
# ``cl`` → ``d`` quedó fuera porque confunde pares legítimos como ``clone``/``done``.
_CONFUSABLE_SEQUENCES: tuple[tuple[str, str], ...] = (
    ("rn", "m"),
    ("vv", "w"),
)
_CONFUSABLE_CHARACTERS = str.maketrans({"1": "l", "i": "l", "0": "o"})

# Prefijos y sufijos que los atacantes agregan a un nombre popular para que parezca
# una variante oficial (``python3-dateutil``, ``lodash-js``, ``py-requests``).
ATTACK_PREFIXES: tuple[str, ...] = ("python3-", "python-", "py-", "py", "node-", "js-")
ATTACK_SUFFIXES: tuple[str, ...] = ("-python", "-py", "-js", ".js", "js", "-dev", "-lib")
_MIN_AFFIX_BASE = 3


def separator_key(name: str) -> str:
    """Quita separadores y signos de scope: ``@types/node`` y ``types-node`` dan ``typesnode``."""
    return _SEPARATORS.sub("", name)


def confusable_key(name: str) -> str:
    """Clave sin separadores y con las letras parecidas unificadas.

    ``jeilyfish`` y ``jellyfish`` comparten clave, igual que ``rnoment`` y ``moment``.
    """
    key = separator_key(name)
    for sequence, replacement in _CONFUSABLE_SEQUENCES:
        key = key.replace(sequence, replacement)
    return key.translate(_CONFUSABLE_CHARACTERS)


def split_scope(name: str) -> tuple[str | None, str]:
    """Separa ``@scope/nombre`` en ``("scope", "nombre")``; sin scope da ``(None, name)``."""
    if name.startswith("@") and "/" in name:
        scope, _, bare = name[1:].partition("/")
        return scope, bare
    return None, name


def strip_affixes(name: str) -> frozenset[str]:
    """Nombres base que quedan al quitar un prefijo o sufijo de ataque.

    Solo se quita uno a la vez y la base debe tener al menos tres caracteres.
    """
    bases: set[str] = set()
    for prefix in ATTACK_PREFIXES:
        if name.startswith(prefix):
            bases.add(name.removeprefix(prefix))
    for suffix in ATTACK_SUFFIXES:
        if name.endswith(suffix):
            bases.add(name.removesuffix(suffix))
    return frozenset(base for base in bases if len(base) >= _MIN_AFFIX_BASE)
