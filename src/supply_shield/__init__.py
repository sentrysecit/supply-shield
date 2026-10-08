"""Supply-Shield: a dependency firewall for npm and PyPI."""

from importlib.metadata import version

__version__ = version("supply-shield")

__all__ = ["__version__"]
