"""Registry Review MCP Server.

Automated registry review workflows for carbon credit project registration.
"""

__version__ = "2.6.0"
__author__ = "Shawn Anderson"

from .server import main

__all__ = ["main", "__version__"]
