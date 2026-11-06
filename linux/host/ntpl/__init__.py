"""ntpl -- native host for the NosTale Packet Logger.

The injected DLL is 32-bit Windows code because it lives inside the game. This
package is everything outside the game: the TCP client, the packet/state model,
the command line tool, and the GTK4 GUI. It is pure standard-library Python
except for the optional GUI.
"""

from .protocol import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    Kind,
    Message,
    Packet,
    ProtocolError,
    default_port,
)

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "Kind",
    "Message",
    "Packet",
    "ProtocolError",
    "default_port",
    "__version__",
]
