"""Static audit of documentation claims against Python source."""
__version__ = "0.1.0"
from .audit import audit

__all__ = ["audit", "__version__"]
