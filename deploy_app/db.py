from typing import Generator

from sqlmodel import Session, SQLModel, create_engine, select

from deploy_app.config import DATABASE_URL, INIT_ADMIN_API_KEY, INIT_ADMIN_USERNAME
from deploy_app.models import User, UserRole
from deploy_app.security import hash_api_key

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


def create_db_and_seed(logger) -> None:
    SQLModel.metadata.create_all(engine)
    if not INIT_ADMIN_API_KEY:
        logger.warning("INIT_ADMIN_API_KEY не задан, первичный админ не создан")
        return

    with Session(engine) as session:
        existing_admin = session.exec(
            select(User).where(User.username == INIT_ADMIN_USERNAME)
        ).first()
        if existing_admin:
            return

        admin = User(
            username=INIT_ADMIN_USERNAME,
            role=UserRole.ADMIN,
            api_key_hash=hash_api_key(INIT_ADMIN_API_KEY),
            is_active=True,
        )
        session.add(admin)
        session.commit()
        logger.info("Первичный админ создан: %s", INIT_ADMIN_USERNAME)
