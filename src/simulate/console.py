from __future__ import annotations

from datetime import datetime


class ConsoleLogger:
    def __init__(self, *, enabled: bool = True, debug: bool = False):
        self.enabled = enabled
        self.debug_enabled = debug

    def emit(self, message: str, *, level: str = "INFO", force: bool = False) -> None:
        if not self.enabled and not force:
            return
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] [{level}] {message}", flush=True)

    def info(self, message: str) -> None:
        self.emit(message, level="INFO")

    def warn(self, message: str) -> None:
        self.emit(message, level="WARN")

    def error(self, message: str) -> None:
        self.emit(message, level="ERROR", force=True)

    def debug(self, message: str) -> None:
        if self.debug_enabled:
            self.emit(message, level="DEBUG")


def preview_text(text: str, *, limit: int = 120) -> str:
    normalized = " ".join((text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."
