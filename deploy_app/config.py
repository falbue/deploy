import os
from pathlib import Path

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./deploy.db")
DEPLOY_ROOT = Path(os.environ.get("DEPLOY_ROOT", "/apps"))
DB_ROOT = Path(os.environ.get("DB_ROOT", "/apps/databases"))
DB_NET_NAME = os.environ.get("DB_NET_NAME", "db-net")

# user_id=1 -> 2000-2999, user_id=2 -> 3000-3999 и т.д.
USER_PORT_BLOCK_START = int(os.environ.get("USER_PORT_BLOCK_START", "2"))

# Внутри пользовательского блока x000-x999
APP_PORT_OFFSET_START = int(os.environ.get("APP_PORT_OFFSET_START", "0"))
APP_PORT_OFFSET_END = int(os.environ.get("APP_PORT_OFFSET_END", "899"))

# Под БД резервируется x900-x999
DB_PORT_OFFSET_START = int(os.environ.get("DB_PORT_OFFSET_START", "900"))
DB_PORT_OFFSET_END = int(os.environ.get("DB_PORT_OFFSET_END", "999"))

INIT_ADMIN_USERNAME = os.environ.get("INIT_ADMIN_USERNAME", "admin")
INIT_ADMIN_API_KEY = os.environ.get("INIT_ADMIN_API_KEY", "")
