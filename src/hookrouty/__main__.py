"""Production entry point: one Uvicorn worker without URL access logs."""

import logging
import sys

import uvicorn
from pydantic import ValidationError

from .config import Settings


def main():
    try:
        settings = Settings()
    except ValidationError:
        print("Invalid configuration. Check the documented environment settings.", file=sys.stderr)
        raise SystemExit(2) from None
    logging.basicConfig(
        level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    uvicorn.run(
        "hookrouty.app:create_app",
        factory=True,
        host="0.0.0.0",
        port=8080,
        workers=1,
        access_log=False,
        log_level=settings.log_level.lower(),
        timeout_graceful_shutdown=settings.shutdown_grace_seconds,
        proxy_headers=False,
    )


if __name__ == "__main__":
    main()
