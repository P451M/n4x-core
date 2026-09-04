"""N4X backend package.

Host+adapter and graph-owned System share the ``n4x`` namespace. Worker
``PYTHONPATH`` prepends the materialized System tree; ``extend_path`` keeps
installed adapter packages (host, graph, secrets) importable.
"""

from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)

__all__ = ["__version__"]

__version__ = "0.1.0"
