import os

import uvicorn

from app.config import Config
from app.web.app import create_app

config = Config.from_env(os.environ)
uvicorn.run(
    create_app(config),
    host="0.0.0.0",
    port=config.port,
    log_level=config.log_level.lower(),
)
