"""Alert log: one line per alert in a rotating file (default /var/log/idraulik/alerts.log).

Line format: 2026-10-15T14:42:00 CRIT [files] message
"""
from __future__ import annotations

import logging
import logging.handlers
from collections import deque
from pathlib import Path

LEVELS = ("INFO", "WARN", "CRIT")


class Alerter:
    def __init__(self, path: str, fallback_dir: str):
        self.path = Path(path)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(self.path, maxBytes=5_000_000, backupCount=5)
        except OSError:
            self.path = Path(fallback_dir) / "alerts.log"
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(self.path, maxBytes=5_000_000, backupCount=5)
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%dT%H:%M:%S"))
        self.logger = logging.getLogger(f"idraulik.alerts.{self.path}")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        self.logger.handlers = [handler]
        self.main = logging.getLogger("idraulik")

    def alert(self, level: str, source: str, message: str) -> None:
        level = level if level in LEVELS else "WARN"
        message = " ".join(message.split())  # keep one alert per line
        self.logger.info("%s [%s] %s", level, source, message)
        self.main.warning("ALERT %s [%s] %s", level, source, message)

    def recent(self, n: int = 20, min_level: str = "INFO") -> list[str]:
        try:
            with open(self.path, encoding="utf-8", errors="replace") as f:
                lines = list(deque(f, maxlen=2000))
        except OSError:
            return []
        ok = LEVELS[LEVELS.index(min_level):]
        return [l.rstrip() for l in lines if len(l.split()) > 1 and l.split()[1] in ok][-n:]
