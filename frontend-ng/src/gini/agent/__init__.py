"""Agent package.

`GiniAPI` is exposed lazily: importing it pulls in `app.context` -> PySide6, and headless consumers
(the Teaching Center server, CI, scripts) need the pure pieces of this package — `authoring`,
`teaching_center`, `lesson_resolver` — WITHOUT a GUI dependency. `from gini.agent import GiniAPI`
still works exactly as before; it just doesn't happen at package-import time.
"""


def __getattr__(name):                      # PEP 562 lazy attribute
    if name == "GiniAPI":
        from .api import GiniAPI
        return GiniAPI
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["GiniAPI"]
