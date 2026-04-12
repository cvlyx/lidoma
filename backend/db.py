from sqlalchemy import create_engine, text
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
    pool_size=2,
    max_overflow=3,
    pool_timeout=30,
    connect_args={"connect_timeout": 10},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _apply_migrations()


def _apply_migrations() -> None:
    """
    Clean up duplicates and apply unique constraints.
    Each step is wrapped individually so one failure doesn't block others.
    """
    with engine.connect() as conn:

        # 1. Remove duplicate school_reports — keep lowest id per student/term/year
        try:
            conn.execute(text("""
                DELETE FROM school_reports
                WHERE id NOT IN (
                    SELECT MIN(id)
                    FROM school_reports
                    GROUP BY student_id, term, academic_year
                )
            """))
            conn.commit()
            print("Migration: duplicate school_reports cleaned.")
        except Exception as e:
            conn.rollback()
            print(f"Migration warning (school_reports dedup): {e}")

        # 2. Remove duplicate grade_records — keep lowest id per student/term/year/subject
        try:
            conn.execute(text("""
                DELETE FROM grade_records
                WHERE id NOT IN (
                    SELECT MIN(id)
                    FROM grade_records
                    GROUP BY student_id, term, academic_year, lower(subject)
                )
            """))
            conn.commit()
            print("Migration: duplicate grade_records cleaned.")
        except Exception as e:
            conn.rollback()
            print(f"Migration warning (grade_records dedup): {e}")

        # 3. Remove duplicate students — keep oldest per name/class
        try:
            conn.execute(text("""
                DELETE FROM students
                WHERE student_id NOT IN (
                    SELECT DISTINCT ON (name, student_class) student_id
                    FROM students
                    ORDER BY name, student_class, created_at ASC
                )
            """))
            conn.commit()
            print("Migration: duplicate students cleaned.")
        except Exception as e:
            conn.rollback()
            print(f"Migration warning (students dedup): {e}")

        # 4. Add unique constraint on school_reports if missing
        try:
            conn.execute(text("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'uq_report_student_term_year'
                    ) THEN
                        ALTER TABLE school_reports
                        ADD CONSTRAINT uq_report_student_term_year
                        UNIQUE (student_id, term, academic_year);
                    END IF;
                END $$;
            """))
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"Migration warning (school_reports constraint): {e}")

        # 5. Add unique constraint on grade_records if missing
        try:
            conn.execute(text("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'uq_grade_student_term_year_subject'
                    ) THEN
                        ALTER TABLE grade_records
                        ADD CONSTRAINT uq_grade_student_term_year_subject
                        UNIQUE (student_id, term, academic_year, subject);
                    END IF;
                END $$;
            """))
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"Migration warning (grade_records constraint): {e}")

        # 6. Add unique constraint on students name+class if missing
        try:
            conn.execute(text("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'uq_student_name_class'
                    ) THEN
                        ALTER TABLE students
                        ADD CONSTRAINT uq_student_name_class
                        UNIQUE (name, student_class);
                    END IF;
                END $$;
            """))
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"Migration warning (students constraint): {e}")

        # 7. Normalise all existing records to 'Second Term'
        # First delete any existing 'Second Term' record that would conflict with a Term 1/3 record
        # for the same student/year/subject (keep the non-Second-Term one, update it to Second Term)
        try:
            conn.execute(text("""
                DELETE FROM grade_records
                WHERE term = 'Second Term'
                AND (student_id, academic_year, lower(subject)) IN (
                    SELECT student_id, academic_year, lower(subject)
                    FROM grade_records
                    WHERE term != 'Second Term'
                )
            """))
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"Migration warning (grade_records conflict clear): {e}")

        try:
            result = conn.execute(text(
                "UPDATE grade_records SET term = 'Second Term' WHERE term != 'Second Term'"
            ))
            conn.commit()
            if result.rowcount:
                print(f"Migration: updated {result.rowcount} grade_records to 'Second Term'.")
        except Exception as e:
            conn.rollback()
            print(f"Migration warning (grade_records term normalise): {e}")

        # Same for school_reports
        try:
            conn.execute(text("""
                DELETE FROM school_reports
                WHERE term = 'Second Term'
                AND (student_id, academic_year) IN (
                    SELECT student_id, academic_year
                    FROM school_reports
                    WHERE term != 'Second Term'
                )
            """))
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"Migration warning (school_reports conflict clear): {e}")

        try:
            result = conn.execute(text(
                "UPDATE school_reports SET term = 'Second Term' WHERE term != 'Second Term'"
            ))
            conn.commit()
            if result.rowcount:
                print(f"Migration: updated {result.rowcount} school_reports to 'Second Term'.")
        except Exception as e:
            conn.rollback()
            print(f"Migration warning (school_reports term normalise): {e}")
