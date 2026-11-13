import asyncio
import logging
import os
import signal
import sys

from . import __version__
from .app import App
from .config import ConfigError, Settings
from .i18n import TranslationError, load_texts
from .profiles import ProfileError, load_profile

log = logging.getLogger(__name__)


async def run(app: App) -> None:
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    for stop in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(stop, task.cancel)
    try:
        await app.run()
    except asyncio.CancelledError:
        log.info("stopped")


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    log.info("tuya-lds-vacuum-map %s", __version__)
    try:
        settings = Settings.from_env()
        profiles = {robot.profile: load_profile(robot.profile) for robot in settings.robots}
        texts = load_texts(settings.language)
    except (ConfigError, ProfileError, TranslationError) as error:
        sys.exit(str(error))
    for robot in settings.robots:
        log.info("robot %s with profile %s", robot.name or robot.device_id, profiles[robot.profile].name)
    asyncio.run(run(App(settings, profiles, texts)))


if __name__ == "__main__":
    main()
