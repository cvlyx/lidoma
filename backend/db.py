from sqlalchemy import create_engine
from sqlalchemy.pool import QueuePool
from sqlalchemy.orm import sessionmaker

from models import Base
from settings import Settings


settings = Settings()

if not settings.database_url:
    raise RuntimeError(
        "DATABASE_URL is missing. Create backend/.env (copy from backend/.env.example) and set DATABASE_URL."
    )

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    poolclass=QueuePool,
    pool_size=5,
    max_overflow=10,
    pool_timeout=30,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _apply_migrations()


def _apply_migrations() -> None:
    """Apply any missing constraints/indexes to existing tables."""
    from sqlalchemy import text
    with engine.connect() as conn:
        # Add unique constraint on grade_records if not exists
        conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'uq_grade_student_term_year_subject'
                ) THEN
                    -- Remove duplicates first (keep lowest id)
                    DELETE FROM grade_records a
                    USING grade_records b
                    WHERE a.id > b.id
                      AND a.student_id = b.student_id
                      AND a.term = b.term
                      AND a.academic_year = b.academic_year
                      AND lower(a.subject) = lower(b.subject);

                    ALTER TABLE grade_records
                    ADD CONSTRAINT uq_grade_student_term_year_subject
                    UNIQUE (student_id, term, academic_year, subject);
                END IF;
            END $$;
        """))
        # Add unique constraint on students name+class if not exists
        conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'uq_student_name_class'
                ) THEN
                    -- Remove duplicate students (keep oldest)
                    DELETE FROM students a
                    USING students b
                    WHERE a.created_at > b.created_at
                      AND a.name = b.name
                      AND a.student_class = b.student_class;

                    ALTER TABLE students
                    ADD CONSTRAINT uq_student_name_class
                    UNIQUE (name, student_class);
                END IF;
            END $$;
        """))
        conn.commit()

