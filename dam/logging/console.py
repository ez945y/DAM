import logging


class ColoredFormatter(logging.Formatter):
    """Logging Formatter to add colors"""

    grey = "\033[38;21m"
    cyan = "\033[36m"
    yellow = "\033[33m"
    red = "\033[31m"
    bold_red = "\033[1;31m"
    reset = "\033[0m"

    def __init__(self, fmt=None, datefmt=None):
        super().__init__(fmt, datefmt)
        self.fmt = fmt or "[%(asctime)s] [%(levelname)-7s] [%(name)-30s] %(message)s"
        self.datefmt = datefmt or "%H:%M:%S"

        self.FORMATS = {
            logging.DEBUG: self.grey + self.fmt + self.reset,
            logging.INFO: self.cyan + self.fmt + self.reset,
            logging.WARNING: self.yellow + self.fmt + self.reset,
            logging.ERROR: self.red + self.fmt + self.reset,
            logging.CRITICAL: self.bold_red + self.fmt + self.reset,
        }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno, self.fmt)
        formatter = logging.Formatter(log_fmt, datefmt=self.datefmt)
        return formatter.format(record)


def setup_colored_logging(level=logging.INFO, fmt=None, datefmt=None):
    """Sets up colored logging for the console."""
    handler = logging.StreamHandler()
    handler.setFormatter(ColoredFormatter(fmt=fmt, datefmt=datefmt))

    # Clear existing handlers if any
    root = logging.getLogger()
    if root.hasHandlers():
        root.handlers.clear()

    logging.basicConfig(level=level, handlers=[handler])
