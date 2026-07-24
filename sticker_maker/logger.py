"""Color-aware logging that coexists with tqdm progress bars."""

from __future__ import annotations

import logging
import sys

from colorama import Fore, Style, just_fix_windows_console
from tqdm import tqdm


COLORS = {
    logging.DEBUG: Fore.BLUE,
    logging.INFO: Fore.CYAN,
    logging.WARNING: Fore.YELLOW,
    logging.ERROR: Fore.RED,
    logging.CRITICAL: Fore.RED + Style.BRIGHT,
}


class TqdmHandler(logging.Handler):
    """Send log records through tqdm.write to avoid damaged bars."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            color = COLORS.get(record.levelno, "") if sys.stderr.isatty() else ""
            reset = Style.RESET_ALL if color else ""
            tqdm.write(f"{color}{self.format(record)}{reset}", file=sys.stderr)
        except Exception:
            self.handleError(record)


def configure_logging(verbose: bool = False) -> logging.Logger:
    """Create the package logger exactly once."""

    just_fix_windows_console()
    logger = logging.getLogger("sticker_maker")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.handlers.clear()
    handler = TqdmHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s  %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger
