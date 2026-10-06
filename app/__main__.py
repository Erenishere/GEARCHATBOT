"""Run the configured development server with `python -m app`."""

import uvicorn

from app.config import get_settings


if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run("app.main:app", host=settings.host, port=settings.port,
                reload=settings.app_env == "development")
