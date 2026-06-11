from __future__ import annotations


def is_openspec_available() -> bool:
    try:
        import openspec  # noqa: F401
    except ImportError:
        return False
    return True
