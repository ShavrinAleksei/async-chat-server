import typing

import structlog


def setup_logging() -> None:
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ]
    )
    logger = get_logger("logging")
    logger.debug("Setup logging")


def get_logger(name: str = "root") -> typing.Any:
    return structlog.get_logger(name)
