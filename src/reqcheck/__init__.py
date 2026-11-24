"""Public API for future CLI, HTTP and runtime adapters."""
from .inputs import load_request
from .pipeline import check

__all__ = ['check', 'load_request']
__version__ = '1.0.0'
