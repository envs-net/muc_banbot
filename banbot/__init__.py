"""BanBot package for XMPP multi-room ban management.

The heavy XMPP client class is imported lazily so pure helper modules can be
imported in tests and tooling without requiring runtime-only dependencies to be
installed first.
"""

def __getattr__(name: str):
    if name == "BanBot":
        from .bot import BanBot

        return BanBot
    raise AttributeError(name)
