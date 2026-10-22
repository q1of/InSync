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
    friend_limit: int = int(os.getenv("FRIEND_LIMIT", "30"))
    database_path: str = os.getenv("DATABASE_PATH", "./data/insync.db")


settings = Settings()
