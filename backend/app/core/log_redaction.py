"""Keep bearer tokens that travel in URL paths out of the uvicorn access log.

Invitation and "was this you?" links carry their credential in the path
(/api/v1/invitations/validate/{token}, /api/v1/user-verification/{token}); the
frontend nginx redacts the same paths in its own log (nginx/access-log.conf.template).
"""
import logging
import re

_TOKEN_PATH = re.compile(r"^(/api/v1/(?:invitations/validate|user-verification)/)[^/?]+")


def redact_path(path: str) -> str:
    return _TOKEN_PATH.sub(r"\1[redacted]", path)


class TokenPathFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # uvicorn.access args: (client_addr, method, full_path, http_version, status_code)
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            record.args = args[:2] + (redact_path(args[2]),) + args[3:]
        return True


def install_access_log_redaction() -> None:
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, TokenPathFilter) for f in logger.filters):
        logger.addFilter(TokenPathFilter())
