import os


def env(name: str, default: str | None = None, required: bool = False) -> str | None:
    value = os.environ.get(name, default)
    if required and not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def enabled_modules() -> set[str] | None:
    """Comma separated list from ENABLED_MODULES; unset or '*' means all."""
    raw = os.environ.get("ENABLED_MODULES", "*").strip()
    if raw in ("", "*"):
        return None
    return {m.strip().lower() for m in raw.split(",") if m.strip()}
