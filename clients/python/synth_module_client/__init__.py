"""Client library for the synth module daemon."""

from .client import DaemonInfo, Pong, SynthModuleClient, SynthModuleError, discover

__all__ = ["DaemonInfo", "Pong", "SynthModuleClient", "SynthModuleError", "discover"]
__version__ = "0.1.0"
