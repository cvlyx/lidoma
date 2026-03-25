from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="teacher")  # admin|teacher
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Student(Base):
    __tablename__ = "students"

    student_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    student_class: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GradeRecord(Base):
    __tablename__ = "grade_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[str] = mapped_column(String(32), ForeignKey("students.student_id", ondelete="CASCADE"), index=True)
    student_name: Mapped[str] = mapped_column(String(120))
    student_class: Mapped[str] = mapped_column(String(32))
    term: Mapped[str] = mapped_column(String(32), index=True)
    academic_year: Mapped[str] = mapped_column(String(32), index=True)
    subject: Mapped[str] = mapped_column(String(64), index=True)
    score: Mapped[int] = mapped_column(Integer)
    teacher_comment: Mapped[str | None] = mapped_column(String(240), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class SchoolAsset(Base):
    __tablename__ = "school_assets"
    __table_args__ = (UniqueConstraint("key", name="uq_school_assets_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), index=True)
    data_url: Mapped[str] = mapped_column(Text())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class SchoolReport(Base):
    __tablename__ = "school_reports"
    __table_args__ = (UniqueConstraint("student_id", "term", "academic_year", name="uq_report_student_term_year"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[str] = mapped_column(String(32), ForeignKey("students.student_id", ondelete="CASCADE"), index=True)
    student_name: Mapped[str] = mapped_column(String(120), index=True)
    student_class: Mapped[str] = mapped_column(String(32), index=True)
    term: Mapped[str] = mapped_column(String(32), index=True)
    academic_year: Mapped[str] = mapped_column(String(32), index=True)
    total_subjects: Mapped[int] = mapped_column(Integer)
    average_score: Mapped[float] = mapped_column(Float)
    aggregate_points: Mapped[float] = mapped_column(Float)
    position: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Position in class (FORM 1&2 only)
    report_data: Mapped[str] = mapped_column(Text())  # JSON string with full report details
    pdf_data: Mapped[str | None] = mapped_column(Text(), nullable=True)  # Base64 encoded PDF
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

