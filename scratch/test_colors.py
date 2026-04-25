import logging

from dam.logging.console import setup_colored_logging


def test_logs():
    setup_colored_logging()
    logger = logging.getLogger("test_logger")
    logger.debug("This is a debug message")
    logger.info("This is an info message")
    logger.warning("This is a warning message")
    logger.error("This is an error message")
    logger.critical("This is a critical message")


if __name__ == "__main__":
    test_logs()
