"""Claves de caché estables, reutilizables por un backend Redis posterior."""

from supply_shield.core.package_ref import PackageRef


def _version_part(package: PackageRef) -> str:
    return package.version if package.version is not None else "*"


def metadata_cache_key(package: PackageRef) -> str:
    """Clave de los metadatos del registro para una versión de paquete."""
    return f"metadata:{package.identity}:{_version_part(package)}"


def findings_cache_key(package: PackageRef) -> str:
    """Clave de los hallazgos de los detectores para una versión de paquete."""
    return f"findings:{package.identity}:{_version_part(package)}"


def decision_cache_key(package: PackageRef, project: str) -> str:
    """Clave de la decisión de política de una versión de paquete en un proyecto."""
    return f"decision:{project}:{package.identity}:{_version_part(package)}"
