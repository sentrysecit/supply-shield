"""Ajustes de Pydantic compartidos por los contratos inmutables de este paquete."""

from pydantic import ConfigDict

FROZEN = ConfigDict(frozen=True, extra="forbid")
