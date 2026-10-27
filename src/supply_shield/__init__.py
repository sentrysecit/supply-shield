"""Supply-Shield: firewall de dependencias para npm y PyPI."""

from importlib.metadata import version

__version__ = version("supply-shield")

__all__ = ["__version__"]
