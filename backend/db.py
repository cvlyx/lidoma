from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base
from settings import Settings


settings = Settings()

if not settings.database_url:
    raise RuntimeError(
        "DATABASE_URL is missing. Create backend/.env (copy from backend/.env.example) and set DATABASE_URL."
    )

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)

