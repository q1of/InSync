import os
from pathlib import Path
from dataclasses import dataclass
from dotenv import load_dotenv

# Грузим backend/.env по абсолютному пути — работает независимо от того,
# из какой папки запущен процесс (локально или через systemd на сервере).
load_dotenv(Path(__file__).with_name(".env"))


@dataclass
class Settings:
    bot_token: str = os.getenv("BOT_TOKEN", "")
    webapp_url: str = os.getenv("WEBAPP_URL", "")
    session_keys: str = os.getenv("SESSION_KEYS", "")
    mirea_proxy: str = os.getenv("MIREA_PROXY", "")
    friend_limit: int = int(os.getenv("FRIEND_LIMIT", "30"))
    database_path: str = os.getenv("DATABASE_PATH", "./data/insync.db")


settings = Settings()

# MIREA_PROXY читается напрямую из окружения модулями клиента (mirea_client,
# pulse_auth). Прокидываем его туда через переменную окружения, чтобы не
# тащить зависимость на этот модуль.
if settings.mirea_proxy:
    os.environ.setdefault("MIREA_PROXY", settings.mirea_proxy)
