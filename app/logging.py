import logging

import structlog


def setup_logging() -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False),
            structlog.dev.ConsoleRenderer()
        ],
        wrapper_class = structlog.make_filtering_bound_logger(logging.NOTSET),
        context_class = dict,
        logger_factory = structlog.PrintLoggerFactory(),
        cache_logger_on_first_use = False
    )

def get_logger(name) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
