"""Índice en memoria de los paquetes populares de un ecosistema."""

from collections.abc import Callable

from supply_shield.analyzers.typosquatting.keys import confusable_key, separator_key
from supply_shield.analyzers.typosquatting.popular import PopularPackages
from supply_shield.core.enums import Ecosystem


class PopularIndex:
    """Nombres populares de un ecosistema listos para comparar.

    Se construye una vez al arrancar. Las búsquedas por clave (separadores y
    letras parecidas) son O(1); la comparación difusa recorre ``names``.
    """

    def __init__(self, popular: PopularPackages) -> None:
        self.ecosystem: Ecosystem = popular.ecosystem
        self.popular: PopularPackages = popular
        self.names: tuple[str, ...] = popular.packages
        self._rank: dict[str, int] = {name: i + 1 for i, name in enumerate(self.names)}
        self._by_separator_key = _group_by(self.names, separator_key)
        self._by_confusable_key = _group_by(self.names, confusable_key)

    def __contains__(self, name: object) -> bool:
        """Indica si ``name`` es uno de los paquetes populares."""
        return name in self._rank

    def __len__(self) -> int:
        """Cantidad de paquetes populares del índice."""
        return len(self.names)

    def rank(self, name: str) -> int:
        """Posición de ``name`` en el ranking, empezando en 1. ``KeyError`` si no es popular."""
        return self._rank[name]

    def same_separator_key(self, name: str) -> tuple[str, ...]:
        """Populares que solo difieren de ``name`` en separadores o en el ``@scope/``."""
        return self._by_separator_key.get(separator_key(name), ())

    def same_confusable_key(self, name: str) -> tuple[str, ...]:
        """Populares que se leen igual que ``name`` al unificar letras parecidas."""
        return self._by_confusable_key.get(confusable_key(name), ())


def _group_by(names: tuple[str, ...], key: Callable[[str], str]) -> dict[str, tuple[str, ...]]:
    groups: dict[str, list[str]] = {}
    for name in names:
        groups.setdefault(key(name), []).append(name)
    return {key_value: tuple(members) for key_value, members in groups.items()}
