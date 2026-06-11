from __future__ import annotations


def is_fastapi_available() -> bool:
    try:
        import fastapi  # noqa: F401
    except ImportError:
        return False
    return True


def require_fastapi() -> None:
    if not is_fastapi_available():
        raise ImportError(
            "FastAPI is required for the web UI. Install with: pip install openloom[ui]"
        )
