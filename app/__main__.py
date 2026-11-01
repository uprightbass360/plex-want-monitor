import logging
import os

import uvicorn

from app.config import Config
from app.web.app import create_app

config = Config.from_env(os.environ)
logging.basicConfig(level=config.log_level)
# httpx logs full request URLs at INFO; feed and Plex URLs carry tokens.
for noisy in ("httpx", "httpcore"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
uvicorn.run(
    create_app(config),
    host="0.0.0.0",
    port=config.port,
    log_level=config.log_level.lower(),
)
