from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db import SessionLocal, init_db
from models import AppSetting, GradeRecord, SchoolAsset, SchoolReport, Student, User
from settings import Settings


settings = Settings()
# Reduce argon2 rounds for faster login while staying secure
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto", argon2__time_cost=1, argon2__memory_cost=65536)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

CLASSES = ["FORM 1", "FORM 2", "FORM 3", "FORM 4"]
TERMS = ["First Term", "Second Term", "Third Term"]
FORM_1_2_CLASSES = {"FORM 1", "FORM 2"}


def calc_f34_aggregate(records: list, student_class: str) -> float:
    """
    Form 3 & 4 aggregate: English is compulsory.
    - If English is missing or failed (grade 9 / points 9), return a high penalty aggregate (99.0) = overall FAIL.
    - Otherwise: English points + best 5 from remaining subjects = best 6 total.
    """
    if not records:
        return 0.0

    english_rec = next(
        (r for r in records if r.subject.strip().lower() == "english"),
        None,
    )

    if english_rec is None:
        # English not entered yet — cannot compute a valid aggregate
        return 0.0

    eng_grade = calc_grade_backend(english_rec.score, student_class)
    if eng_grade["result"] == "FAIL":
        # Failed English → overall fail regardless of other subjects
        return 99.0

    eng_points = eng_grade["points"]

    other_points = sorted(
        calc_grade_backend(r.score, student_class)["points"]
        for r in records
        if r.subject.strip().lower() != "english"
    )
    best5 = other_points[:5]
    return float(eng_points + sum(best5))


def calc_grade_backend(score: int, student_class: str) -> dict:
    """Calculate grade based on score and class"""
    is_form1_or_2 = student_class in FORM_1_2_CLASSES

    if is_form1_or_2:
        if score >= 80:
            return {"grade": "A", "points": 1, "result": "PASS"}
        elif score >= 60:
            return {"grade": "B", "points": 1, "result": "PASS"}
        elif score >= 40:
            return {"grade": "C", "points": 1, "result": "PASS"}
        else:
            return {"grade": "F", "points": 0, "result": "FAIL"}
    else:
        if score >= 90:
            return {"grade": "1", "points": 1, "result": "DIST"}
        elif score >= 80:
            return {"grade": "2", "points": 2, "result": "DIST"}
        elif score >= 70:
            return {"grade": "3", "points": 3, "result": "STRONG CREDIT"}
        elif score >= 66:
            return {"grade": "4", "points": 4, "result": "CRED"}
        elif score >= 60:
            return {"grade": "5", "points": 5, "result": "CRED"}
        elif score >= 50:
            return {"grade": "6", "points": 6, "result": "CRED"}
        elif score >= 46:
            return {"grade": "7", "points": 7, "result": "PASS"}
        elif score >= 40:
            return {"grade": "8", "points": 8, "result": "PASS"}
        else:
            return {"grade": "9", "points": 9, "result": "FAIL"}


def sync_report(db: Session, student_id: str, term: str, academic_year: str) -> None:
    """Auto-create or update a school report from live grade_records."""
    student = db.scalar(select(Student).where(Student.student_id == student_id))
    if not student:
        return

    records = db.scalars(
        select(GradeRecord)
        .where(GradeRecord.student_id == student_id)
        .where(GradeRecord.term == term)
        .where(GradeRecord.academic_year == academic_year)
    ).all()

    scores = [r.score for r in records]
    avg_score = sum(scores) / len(scores) if scores else 0.0
    is_f12 = student.student_class in FORM_1_2_CLASSES

    # Compute grades once per record
    grades = [calc_grade_backend(r.score, student.student_class) for r in records]

    if is_f12:
        aggregate = avg_score
    else:
        aggregate = calc_f34_aggregate(records, student.student_class)

    app_settings = _read_settings_cached(db)
    report_data = {
        "school_name": app_settings.school_name,
        "student_id": student.student_id,
        "student_name": student.name,
        "student_class": student.student_class,
        "term": term,
        "academic_year": academic_year,
        "subjects": [
            {
                "subject": r.subject,
                "score": r.score,
                "grade": g['grade'],
                "result": g['result'],
                "comment": r.teacher_comment,
            }
            for r, g in zip(records, grades)
        ],
        "average_score": avg_score,
        "aggregate": aggregate,
        "is_form1_or_2": is_f12,
        "has_grades": len(records) > 0,
    }

    # Fetch ALL matching reports (there may be duplicates from old data)
    all_existing = db.scalars(
        select(SchoolReport)
        .where(SchoolReport.student_id == student_id)
        .where(SchoolReport.term == term)
        .where(SchoolReport.academic_year == academic_year)
        .order_by(SchoolReport.id.asc())
    ).all()

    if all_existing:
        # Keep the first, delete any extras
        existing = all_existing[0]
        for dup in all_existing[1:]:
            db.delete(dup)
        existing.report_data = json.dumps(report_data)
        existing.average_score = avg_score
        existing.aggregate_points = aggregate
        existing.total_subjects = len(records)
        existing.student_name = student.name
        existing.student_class = student.student_class
    else:
        db.add(SchoolReport(
            student_id=student_id,
            student_name=student.name,
            student_class=student.student_class,
            term=term,
            academic_year=academic_year,
            total_subjects=len(records),
            average_score=avg_score,
            aggregate_points=aggregate,
            report_data=json.dumps(report_data),
            position=None,
        ))

app = FastAPI(title="LIDOMA API", version="0.1.0")

# Configure CORS origins.
# - `ALLOWED_ORIGINS` can be set in the environment (comma-separated list).
# - For Vercel deployments we allow any vercel.app preview URL via regex.
allowed_origins = [o.strip() for o in settings.allowed_origins.split(",") if o.strip()]

# Keep common local defaults when no origins are explicitly configured.
if not allowed_origins:
    allowed_origins = ["http://127.0.0.1:8787", "http://localhost:8787"]

# Allow Vercel preview URLs (e.g. https://<project>-<hash>.vercel.app)
allow_origin_regex = r"^https?://([a-z0-9-]+\.)*vercel\.app$"

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins + ["null", "file://"],
    allow_origin_regex=allow_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _hash_password(p: str) -> str:
    return pwd_context.hash(p)


def _verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def _create_access_token(sub: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=settings.jwt_minutes)
    payload = {"sub": sub, "role": role, "iat": int(now.timestamp()), "exp": int(exp.timestamp())}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


class TokenOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"


class StudentIn(BaseModel):
    student_id: str = Field(..., min_length=3, max_length=32)
    name: str = Field(..., min_length=1, max_length=120)
    student_class: str = Field(..., min_length=1, max_length=32)


class StudentUpdateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    student_class: str = Field(..., min_length=1, max_length=32)


class StudentOut(StudentIn):
    created_at: datetime


class RecordIn(BaseModel):
    student_id: str = Field(..., min_length=3, max_length=32)
    student_name: str = Field(..., min_length=1, max_length=120)
    student_class: str = Field(..., min_length=1, max_length=32)
    term: str = Field(..., min_length=1, max_length=32)
    academic_year: str = Field(..., min_length=4, max_length=32)
    subject: str = Field(..., min_length=1, max_length=64)
    score: int = Field(..., ge=0, le=100)
    teacher_comment: str | None = Field(default=None, max_length=240)


class RecordUpdateIn(BaseModel):
    term: str | None = Field(default=None, min_length=1, max_length=32)
    academic_year: str | None = Field(default=None, min_length=4, max_length=32)
    subject: str | None = Field(default=None, min_length=1, max_length=64)
    score: int | None = Field(default=None, ge=0, le=100)
    teacher_comment: str | None = Field(default=None, max_length=240)


class RecordOut(RecordIn):
    id: int
    created_at: datetime


class SettingsIn(BaseModel):
    school_name: str | None = Field(default=None, max_length=200)
    academic_year: str | None = Field(default=None, max_length=32)
    report_title: str | None = Field(default=None, max_length=200)


class SettingsOut(BaseModel):
    school_name: str | None = None
    academic_year: str | None = None
    report_title: str | None = None


class ReportGenerateIn(BaseModel):
    student_class: str = Field(..., min_length=1, max_length=32)
    term: str = Field(..., min_length=1, max_length=32)
    academic_year: str = Field(..., min_length=4, max_length=32)
    assign_positions: bool = Field(default=False)


class ReportAssignPositionsIn(BaseModel):
    student_class: str = Field(..., min_length=1, max_length=32)
    term: str = Field(..., min_length=1, max_length=32)
    academic_year: str = Field(..., min_length=4, max_length=32)


class ReportDownloadBatchIn(BaseModel):
    report_ids: list[int] = Field(default_factory=list)


class ReportDownloadClassIn(BaseModel):
    student_class: str = Field(..., min_length=1, max_length=32)
    term: str = Field(..., min_length=1, max_length=32)
    academic_year: str = Field(..., min_length=4, max_length=32)


class LogoIn(BaseModel):
    data_url: str = Field(..., min_length=20)


class ParentLookupIn(BaseModel):
    student_id: str = Field(..., min_length=3, max_length=32)
    student_name: str = Field(..., min_length=1, max_length=120)


class ParentLookupOut(BaseModel):
    student: StudentOut
    records: list[RecordOut]
    settings: SettingsOut
    logo_data_url: str | None = None


class ReportSummary(BaseModel):
    id: int
    student_id: str
    student_name: str
    student_class: str
    term: str
    academic_year: str
    total_subjects: int
    average_score: float
    aggregate_points: float
    position: int | None = None
    created_at: datetime


class ReportFull(ReportSummary):
    report_data: dict
    pdf_data: str | None = None


def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)], db: Annotated[Session, Depends(get_db)]
) -> User:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        sub = payload.get("sub")
        if not sub:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    except JWTError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from e

    user = db.scalar(select(User).where(User.username == sub))
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user")
    return user


def require_admin(user: Annotated[User, Depends(get_current_user)]) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")
    return user


@app.on_event("startup")
def _startup():
    init_db()
    db = SessionLocal()
    try:
        # Ensure admin user exists
        existing = db.scalar(select(User).where(User.username == settings.admin_username))
        if not existing:
            db.add(
                User(
                    username=settings.admin_username,
                    password_hash=_hash_password(settings.admin_password),
                    role="admin",
                    is_active=True,
                )
            )
            db.commit()

        # Resync only reports that show 0 subjects — these are stale/mismatched reports
        empty_reports = db.scalars(
            select(SchoolReport).where(SchoolReport.total_subjects == 0)
        ).all()
        for r in empty_reports:
            sync_report(db, r.student_id, r.term, r.academic_year)
        if empty_reports:
            db.commit()

    finally:
        db.close()


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/init")
def init_data(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    student_id: str | None = None,
):
    """Single endpoint to load all startup data in one round-trip.
    Pass ?student_id=X to load records for a specific student only.
    Without student_id, returns all students but only the 300 most recent records.
    """
    students = db.scalars(select(Student).order_by(Student.created_at.desc())).all()

    q = select(GradeRecord).order_by(GradeRecord.created_at.desc())
    if student_id:
        q = q.where(GradeRecord.student_id == student_id)
    records = db.scalars(q).all()

    cfg = _read_settings_cached(db)
    logo = db.scalar(select(SchoolAsset).where(SchoolAsset.key == "logo"))
    return {
        "students": [
            {"student_id": s.student_id, "name": s.name, "student_class": s.student_class, "created_at": s.created_at.isoformat()}
            for s in students
        ],
        "records": [
            {
                "id": r.id, "student_id": r.student_id, "student_name": r.student_name,
                "student_class": r.student_class, "term": r.term, "academic_year": r.academic_year,
                "subject": r.subject, "score": r.score, "teacher_comment": r.teacher_comment,
                "created_at": r.created_at.isoformat()
            }
            for r in records
        ],
        "settings": {"school_name": cfg.school_name, "academic_year": cfg.academic_year, "report_title": cfg.report_title},
        "logo_data_url": logo.data_url if logo else None,
    }


@app.post("/api/auth/login", response_model=TokenOut)
def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()], db: Annotated[Session, Depends(get_db)]
):
    user = db.scalar(select(User).where(User.username == form.username))
    if not user or not user.is_active or not _verify_password(form.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect username or password")
    return TokenOut(access_token=_create_access_token(sub=user.username, role=user.role))


# ---------- Public (Parent Portal) ----------


@app.post("/api/parent/lookup", response_model=ParentLookupOut)
def parent_lookup(payload: ParentLookupIn, db: Annotated[Session, Depends(get_db)]):
    student = db.scalar(select(Student).where(Student.student_id == payload.student_id))
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    # Keep it strict: require name match (case-insensitive exact) to reduce accidental disclosure.
    if student.name.strip().lower() != payload.student_name.strip().lower():
        raise HTTPException(status_code=404, detail="Student not found")

    recs = db.scalars(
        select(GradeRecord)
        .where(GradeRecord.student_id == student.student_id)
        .order_by(GradeRecord.created_at.asc())
    ).all()

    st = _read_settings(db)
    logo = db.scalar(select(SchoolAsset).where(SchoolAsset.key == "logo"))
    return ParentLookupOut(
        student=StudentOut(
            student_id=student.student_id,
            name=student.name,
            student_class=student.student_class,
            created_at=student.created_at,
        ),
        records=[
            RecordOut(
                id=r.id,
                student_id=r.student_id,
                student_name=r.student_name,
                student_class=r.student_class,
                term=r.term,
                academic_year=r.academic_year,
                subject=r.subject,
                score=r.score,
                teacher_comment=r.teacher_comment,
                created_at=r.created_at,
            )
            for r in recs
        ],
        settings=st,
        logo_data_url=(logo.data_url if logo else None),
    )


@app.get("/api/public/settings", response_model=SettingsOut)
def public_settings(db: Annotated[Session, Depends(get_db)]):
    return _read_settings(db)


@app.get("/api/public/logo")
def public_logo(db: Annotated[Session, Depends(get_db)]):
    logo = db.scalar(select(SchoolAsset).where(SchoolAsset.key == "logo"))
    if not logo:
        return Response(status_code=204)
    return {"data_url": logo.data_url}


def _read_settings(db: Session) -> SettingsOut:
    rows = db.scalars(select(AppSetting)).all()
    d = {r.key: r.value for r in rows}
    return SettingsOut(
        school_name=d.get("school_name"),
        academic_year=d.get("academic_year"),
        report_title=d.get("report_title"),
    )

# In-memory settings cache (invalidated on save)
_settings_cache: SettingsOut | None = None

def _read_settings_cached(db: Session) -> SettingsOut:
    global _settings_cache
    if _settings_cache is None:
        _settings_cache = _read_settings(db)
    return _settings_cache

def _invalidate_settings_cache() -> None:
    global _settings_cache
    _settings_cache = None


# ---------- Teacher/Admin ----------


@app.get("/api/students", response_model=list[StudentOut])
def list_students(
    _: Annotated[User, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]
):
    rows = db.scalars(select(Student).order_by(Student.created_at.desc())).all()
    return [
        StudentOut(
            student_id=s.student_id, name=s.name, student_class=s.student_class, created_at=s.created_at
        )
        for s in rows
    ]


@app.post("/api/students", response_model=StudentOut, status_code=201)
def create_student(
    payload: StudentIn,
    _: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    s = Student(student_id=payload.student_id, name=payload.name, student_class=payload.student_class)
    db.add(s)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="A student with this ID or the same name in this class already exists")
    return StudentOut(student_id=s.student_id, name=s.name, student_class=s.student_class, created_at=s.created_at)


@app.delete("/api/students/{student_id}", status_code=204)
def delete_student(
    student_id: str,
    _: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    db.execute(delete(GradeRecord).where(GradeRecord.student_id == student_id))
    res = db.execute(delete(Student).where(Student.student_id == student_id))
    if res.rowcount == 0:
        db.rollback()
        raise HTTPException(status_code=404, detail="Student not found")
    db.commit()
    return Response(status_code=204)


@app.put("/api/students/{student_id}", response_model=StudentOut)
def update_student(
    payload: StudentUpdateIn,
    student_id: str,
    _: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    s = db.scalar(select(Student).where(Student.student_id == student_id))
    if not s:
        raise HTTPException(status_code=404, detail="Student not found")

    # Check name+class uniqueness (excluding self)
    existing = db.scalar(
        select(Student)
        .where(Student.name == payload.name)
        .where(Student.student_class == payload.student_class)
        .where(Student.student_id != student_id)
    )
    if existing:
        raise HTTPException(status_code=409, detail="A student with this name already exists in this class")

    s.name = payload.name
    s.student_class = payload.student_class
    db.commit()
    db.refresh(s)
    return StudentOut(student_id=s.student_id, name=s.name, student_class=s.student_class, created_at=s.created_at)


@app.get("/api/records", response_model=list[RecordOut])
def list_records(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    student_id: str | None = None,
    term: str | None = None,
    academic_year: str | None = None,
):
    q = select(GradeRecord).order_by(GradeRecord.created_at.desc())
    if student_id:
        q = q.where(GradeRecord.student_id == student_id)
    if term:
        q = q.where(GradeRecord.term == term)
    if academic_year:
        q = q.where(GradeRecord.academic_year == academic_year)
    rows = db.scalars(q).all()
    return [
        RecordOut(
            id=r.id,
            student_id=r.student_id,
            student_name=r.student_name,
            student_class=r.student_class,
            term=r.term,
            academic_year=r.academic_year,
            subject=r.subject,
            score=r.score,
            teacher_comment=r.teacher_comment,
            created_at=r.created_at,
        )
        for r in rows
    ]


@app.post("/api/records", response_model=RecordOut, status_code=201)
def create_record(
    payload: RecordIn,
    _: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    # Always enforce Second Term regardless of what was submitted
    payload.term = "Second Term"
    # Check for duplicate subject for same student/term/year
    existing = db.scalar(
        select(GradeRecord)
        .where(GradeRecord.student_id == payload.student_id)
        .where(GradeRecord.term == payload.term)
        .where(GradeRecord.academic_year == payload.academic_year)
        .where(func.lower(GradeRecord.subject) == func.lower(payload.subject))
    )
    if existing:
        raise HTTPException(status_code=409, detail=f"{payload.subject} already entered for this student in this term")

    r = GradeRecord(
        student_id=payload.student_id,
        student_name=payload.student_name,
        student_class=payload.student_class,
        term=payload.term,
        academic_year=payload.academic_year,
        subject=payload.subject,
        score=payload.score,
        teacher_comment=payload.teacher_comment,
    )
    db.add(r)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"{payload.subject} already entered for this student in this term")
    db.refresh(r)
    sync_report(db, r.student_id, r.term, r.academic_year)
    db.commit()
    return RecordOut(
        id=r.id,
        student_id=r.student_id,
        student_name=r.student_name,
        student_class=r.student_class,
        term=r.term,
        academic_year=r.academic_year,
        subject=r.subject,
        score=r.score,
        teacher_comment=r.teacher_comment,
        created_at=r.created_at,
    )


@app.delete("/api/records/clear", status_code=204)
def clear_records(
    _: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    db.execute(delete(GradeRecord))
    db.commit()
    return Response(status_code=204)


@app.delete("/api/records/{record_id}", status_code=204)
def delete_record(
    record_id: int,
    _: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    record = db.scalar(select(GradeRecord).where(GradeRecord.id == record_id))
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")
    student_id, term, academic_year = record.student_id, record.term, record.academic_year
    db.delete(record)
    db.commit()
    sync_report(db, student_id, term, academic_year)
    db.commit()
    return Response(status_code=204)


@app.put("/api/records/{record_id}", response_model=RecordOut)
def update_record(
    payload: RecordUpdateIn,
    record_id: int,
    _: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    r = db.scalar(select(GradeRecord).where(GradeRecord.id == record_id))
    if not r:
        raise HTTPException(status_code=404, detail="Record not found")
    
    if payload.term is not None:
        r.term = payload.term
    if payload.academic_year is not None:
        r.academic_year = payload.academic_year
    if payload.subject is not None:
        r.subject = payload.subject
    if payload.score is not None:
        r.score = payload.score
    if payload.teacher_comment is not None:
        r.teacher_comment = payload.teacher_comment
    
    db.commit()
    db.refresh(r)
    sync_report(db, r.student_id, r.term, r.academic_year)
    db.commit()
    return RecordOut(
        id=r.id,
        student_id=r.student_id,
        student_name=r.student_name,
        student_class=r.student_class,
        term=r.term,
        academic_year=r.academic_year,
        subject=r.subject,
        score=r.score,
        teacher_comment=r.teacher_comment,
        created_at=r.created_at,
    )


@app.post("/api/admin/settings", response_model=SettingsOut)
def upsert_settings(
    payload: SettingsIn,
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    updates: dict[str, str] = {}
    if payload.school_name is not None:
        updates["school_name"] = payload.school_name
    if payload.academic_year is not None:
        updates["academic_year"] = payload.academic_year
    if payload.report_title is not None:
        updates["report_title"] = payload.report_title

    for k, v in updates.items():
        row = db.scalar(select(AppSetting).where(AppSetting.key == k))
        if row:
            row.value = v
        else:
            db.add(AppSetting(key=k, value=v))
    db.commit()
    _invalidate_settings_cache()
    return _read_settings(db)


@app.post("/api/admin/logo")
def set_logo(
    payload: LogoIn,
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    row = db.scalar(select(SchoolAsset).where(SchoolAsset.key == "logo"))
    if row:
        row.data_url = payload.data_url
    else:
        db.add(SchoolAsset(key="logo", data_url=payload.data_url))
    db.commit()
    return {"ok": True}


@app.post("/api/admin/users", status_code=201)
def create_user(
    username: str,
    password: str,
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    role: Literal["admin", "teacher"] = "teacher",
):
    u = User(username=username, password_hash=_hash_password(password), role=role, is_active=True)
    db.add(u)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="User already exists")
    return {"ok": True}


# ========== REPORT MANAGEMENT API ==========

from io import BytesIO
try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False


@app.get("/api/reports", response_model=list[ReportSummary])
def list_reports(
    _: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    student_class: str | None = None,
    term: str | None = None,
    academic_year: str | None = None,
    search: str | None = None,
):
    """List all reports with live stats via a single aggregated query"""
    from sqlalchemy import Float, cast

    # Subquery: aggregate grade_records per student/term/year
    grade_agg = (
        select(
            GradeRecord.student_id.label("sid"),
            GradeRecord.term.label("term"),
            GradeRecord.academic_year.label("year"),
            func.count(GradeRecord.id).label("total_subjects"),
            func.avg(cast(GradeRecord.score, Float)).label("avg_score"),
        )
        .group_by(GradeRecord.student_id, GradeRecord.term, GradeRecord.academic_year)
        .subquery()
    )

    # When no term/year filter: show only one report per student (highest id = most recent)
    # When term/year filter applied: show one report per student/term/year (lowest id = canonical)
    if not term and not academic_year:
        canonical_ids = (
            select(func.max(SchoolReport.id).label("id"))
            .group_by(SchoolReport.student_id)
            .subquery()
        )
    else:
        canonical_ids = (
            select(func.min(SchoolReport.id).label("id"))
            .group_by(SchoolReport.student_id, SchoolReport.term, SchoolReport.academic_year)
            .subquery()
        )

    q = (
        select(SchoolReport, grade_agg)
        .where(SchoolReport.id.in_(select(canonical_ids.c.id)))
        .outerjoin(
            grade_agg,
            (SchoolReport.student_id == grade_agg.c.sid)
            & (SchoolReport.term == grade_agg.c.term)
            & (SchoolReport.academic_year == grade_agg.c.year),
        )
        .order_by(SchoolReport.student_class.asc(), SchoolReport.average_score.desc())
    )

    if student_class:
        q = q.where(SchoolReport.student_class == student_class)
    if term:
        q = q.where(SchoolReport.term == term)
    if academic_year:
        q = q.where(SchoolReport.academic_year == academic_year)
    if search:
        s = f"%{search}%"
        q = q.where(
            SchoolReport.student_name.ilike(s) | SchoolReport.student_id.ilike(s)
        )

    rows = db.execute(q).all()

    # For FORM 3/4: fetch all relevant grade records in one query (filtered by term+year too)
    f34_rows = [r for r in rows if r.SchoolReport.student_class not in FORM_1_2_CLASSES and (r.total_subjects or 0) > 0]
    agg_points_map: dict[tuple, float] = {}

    if f34_rows:
        from collections import defaultdict
        student_ids = list({r.SchoolReport.student_id for r in f34_rows})
        terms = list({r.SchoolReport.term for r in f34_rows})
        years = list({r.SchoolReport.academic_year for r in f34_rows})

        all_f34_records = db.scalars(
            select(GradeRecord)
            .where(GradeRecord.student_id.in_(student_ids))
            .where(GradeRecord.term.in_(terms))
            .where(GradeRecord.academic_year.in_(years))
        ).all()

        rec_map: dict[tuple, list[tuple[int, str, str]]] = defaultdict(list)
        for rec in all_f34_records:
            rec_map[(rec.student_id, rec.term, rec.academic_year)].append((rec.score, rec.subject, rec.student_class))

        for row in f34_rows:
            r = row.SchoolReport
            key = (r.student_id, r.term, r.academic_year)
            entries = rec_map.get(key, [])
            if entries:
                # entries = list of (score, subject, student_class)
                english_entry = next((e for e in entries if e[1].strip().lower() == "english"), None)
                if english_entry is None:
                    agg_points_map[key] = 0.0
                else:
                    eng_grade = calc_grade_backend(english_entry[0], english_entry[2])
                    if eng_grade["result"] == "FAIL":
                        agg_points_map[key] = 99.0
                    else:
                        eng_pts = eng_grade["points"]
                        other_pts = sorted(
                            calc_grade_backend(score, cls)["points"]
                            for score, subj, cls in entries
                            if subj.strip().lower() != "english"
                        )
                        agg_points_map[key] = float(eng_pts + sum(other_pts[:5]))
            else:
                agg_points_map[key] = 0.0

    result = []
    for row in rows:
        r = row.SchoolReport
        total = row.total_subjects or 0
        avg = float(row.avg_score) if row.avg_score is not None else 0.0
        key = (r.student_id, r.term, r.academic_year)
        is_f12 = r.student_class in FORM_1_2_CLASSES
        agg = avg if is_f12 else agg_points_map.get(key, 0.0)

        result.append(ReportSummary(
            id=r.id,
            student_id=r.student_id,
            student_name=r.student_name,
            student_class=r.student_class,
            term=r.term,
            academic_year=r.academic_year,
            total_subjects=total,
            average_score=avg,
            aggregate_points=agg,
            position=r.position,
            created_at=r.created_at,
        ))
    return result


@app.get("/api/reports/{report_id}", response_model=ReportFull)
def get_report(
    report_id: int,
    _: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Get full report details with fresh grades from grade_records"""
    r = db.scalar(select(SchoolReport).where(SchoolReport.id == report_id))
    if not r:
        raise HTTPException(status_code=404, detail="Report not found")

    # Always fetch fresh grades from grade_records
    records = db.scalars(
        select(GradeRecord)
        .where(GradeRecord.student_id == r.student_id)
        .where(GradeRecord.term == r.term)
        .where(GradeRecord.academic_year == r.academic_year)
        .order_by(GradeRecord.subject.asc())
    ).all()

    is_form1_or_2 = r.student_class in FORM_1_2_CLASSES
    scores = [rec.score for rec in records]
    avg_score = sum(scores) / len(scores) if scores else 0.0

    if is_form1_or_2:
        aggregate = avg_score
    else:
        aggregate = calc_f34_aggregate(records, r.student_class)

    settings = _read_settings(db)
    stored = json.loads(r.report_data)

    fresh_report_data = {
        "school_name": stored.get("school_name") or settings.school_name,
        "student_id": r.student_id,
        "student_name": r.student_name,
        "student_class": r.student_class,
        "term": r.term,
        "academic_year": r.academic_year,
        "subjects": [
            {
                "subject": rec.subject,
                "score": rec.score,
                "grade": calc_grade_backend(rec.score, r.student_class)['grade'],
                "result": calc_grade_backend(rec.score, r.student_class)['result'],
                "comment": rec.teacher_comment,
            }
            for rec in records
        ],
        "average_score": avg_score,
        "aggregate": aggregate,
        "is_form1_or_2": is_form1_or_2,
        "has_grades": len(records) > 0,
    }

    return ReportFull(
        id=r.id,
        student_id=r.student_id,
        student_name=r.student_name,
        student_class=r.student_class,
        term=r.term,
        academic_year=r.academic_year,
        total_subjects=len(records),
        average_score=avg_score,
        aggregate_points=aggregate,
        position=r.position,
        created_at=r.created_at,
        report_data=fresh_report_data,
        pdf_data=r.pdf_data,
    )


@app.delete("/api/reports/{report_id}", status_code=204)
def delete_report(
    report_id: int,
    _: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Delete a report"""
    res = db.execute(delete(SchoolReport).where(SchoolReport.id == report_id))
    if res.rowcount == 0:
        db.rollback()
        raise HTTPException(status_code=404, detail="Report not found")
    db.commit()
    return Response(status_code=204)


class ReportEditIn(BaseModel):
    student_name: str | None = Field(default=None, max_length=120)
    term: str | None = Field(default=None, max_length=32)
    academic_year: str | None = Field(default=None, max_length=32)
    subjects: list[dict] | None = None  # [{subject, score, teacher_comment}]


@app.put("/api/reports/{report_id}", response_model=ReportFull)
def edit_report(
    report_id: int,
    payload: ReportEditIn,
    _: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Edit a report's subjects/scores directly via grade_records"""
    r = db.scalar(select(SchoolReport).where(SchoolReport.id == report_id))
    if not r:
        raise HTTPException(status_code=404, detail="Report not found")

    if payload.subjects is not None:
        for subj_data in payload.subjects:
            subject = subj_data.get("subject", "").strip()
            score = subj_data.get("score")
            comment = subj_data.get("teacher_comment", None)
            if not subject or score is None:
                continue
            score = max(0, min(100, int(score)))
            rec = db.scalar(
                select(GradeRecord)
                .where(GradeRecord.student_id == r.student_id)
                .where(GradeRecord.term == r.term)
                .where(GradeRecord.academic_year == r.academic_year)
                .where(func.lower(GradeRecord.subject) == subject.lower())
            )
            if rec:
                rec.score = score
                if comment is not None:
                    rec.teacher_comment = comment
            else:
                db.add(GradeRecord(
                    student_id=r.student_id,
                    student_name=r.student_name,
                    student_class=r.student_class,
                    term=r.term,
                    academic_year=r.academic_year,
                    subject=subject,
                    score=score,
                    teacher_comment=comment,
                ))
        db.commit()

    sync_report(db, r.student_id, r.term, r.academic_year)
    db.commit()
    db.refresh(r)

    records = db.scalars(
        select(GradeRecord)
        .where(GradeRecord.student_id == r.student_id)
        .where(GradeRecord.term == r.term)
        .where(GradeRecord.academic_year == r.academic_year)
        .order_by(GradeRecord.subject.asc())
    ).all()
    scores = [rec.score for rec in records]
    avg_score = sum(scores) / len(scores) if scores else 0.0
    is_f12 = r.student_class in FORM_1_2_CLASSES
    if is_f12:
        aggregate = avg_score
    else:
        aggregate = calc_f34_aggregate(records, r.student_class)

    fresh_data = json.loads(r.report_data)
    return ReportFull(
        id=r.id, student_id=r.student_id, student_name=r.student_name,
        student_class=r.student_class, term=r.term, academic_year=r.academic_year,
        total_subjects=len(records), average_score=avg_score, aggregate_points=aggregate,
        position=r.position, created_at=r.created_at, report_data=fresh_data,
    )


@app.post("/api/reports/cleanup", status_code=200)
def cleanup_duplicate_reports(
    _: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Full cleanup:
    1. Delete duplicate school_reports (same student/term/year) keeping lowest id
    2. Delete extra reports for same student keeping only the one with most subjects
    3. Re-sync every remaining report from live grade_records
    4. Delete reports that have zero subjects after sync
    """
    cleaned = 0

    # Step 1: remove exact duplicates (same student/term/year), keep lowest id
    dupes = db.execute(
        select(
            SchoolReport.student_id,
            SchoolReport.term,
            SchoolReport.academic_year,
            func.count(SchoolReport.id).label("cnt")
        ).group_by(SchoolReport.student_id, SchoolReport.term, SchoolReport.academic_year)
        .having(func.count(SchoolReport.id) > 1)
    ).all()

    for row in dupes:
        reports = db.scalars(
            select(SchoolReport)
            .where(SchoolReport.student_id == row.student_id)
            .where(SchoolReport.term == row.term)
            .where(SchoolReport.academic_year == row.academic_year)
            .order_by(SchoolReport.id.asc())
        ).all()
        for r in reports[1:]:
            db.delete(r)
            cleaned += 1
    db.commit()

    # Step 2: for students with reports across multiple terms/years,
    # keep only the report with the most subjects (most recent data)
    multi = db.execute(
        select(
            SchoolReport.student_id,
            func.count(SchoolReport.id).label("cnt")
        ).group_by(SchoolReport.student_id)
        .having(func.count(SchoolReport.id) > 1)
    ).all()

    for row in multi:
        reports = db.scalars(
            select(SchoolReport)
            .where(SchoolReport.student_id == row.student_id)
            .order_by(SchoolReport.total_subjects.desc(), SchoolReport.id.desc())
        ).all()
        for r in reports[1:]:
            db.delete(r)
            cleaned += 1
    db.commit()

    # Step 3: re-sync ALL remaining reports from live grade_records
    all_reports = db.scalars(select(SchoolReport)).all()
    for r in all_reports:
        sync_report(db, r.student_id, r.term, r.academic_year)
    db.commit()

    # Step 4: remove reports with no grade records at all
    empty = db.scalars(
        select(SchoolReport).where(SchoolReport.total_subjects == 0)
    ).all()
    empty_removed = len(empty)
    for r in empty:
        db.delete(r)
    db.commit()

    return {
        "ok": True,
        "duplicates_removed": cleaned,
        "empty_reports_removed": empty_removed,
        "total_resynced": len(all_reports),
    }


@app.get("/api/reports/debug-counts")
def debug_report_counts(
    _: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Show report counts vs student counts per class, and list all duplicate student_ids."""
    from sqlalchemy import distinct

    # Count students per class
    student_counts = db.execute(
        select(Student.student_class, func.count(Student.student_id).label("cnt"))
        .group_by(Student.student_class)
    ).all()

    # Count ALL reports per class (raw, no dedup)
    report_counts_raw = db.execute(
        select(SchoolReport.student_class, func.count(SchoolReport.id).label("cnt"))
        .group_by(SchoolReport.student_class)
    ).all()

    # Count distinct students in reports per class
    report_distinct = db.execute(
        select(SchoolReport.student_class, func.count(distinct(SchoolReport.student_id)).label("cnt"))
        .group_by(SchoolReport.student_class)
    ).all()

    # Find students with more than one report row (any term/year combo)
    multi_reports = db.execute(
        select(
            SchoolReport.student_id,
            SchoolReport.student_name,
            SchoolReport.student_class,
            func.count(SchoolReport.id).label("cnt"),
            func.array_agg(SchoolReport.term).label("terms"),
            func.array_agg(SchoolReport.academic_year).label("years"),
        )
        .group_by(SchoolReport.student_id, SchoolReport.student_name, SchoolReport.student_class)
        .having(func.count(SchoolReport.id) > 1)
        .order_by(SchoolReport.student_class, SchoolReport.student_name)
    ).all()

    return {
        "student_counts": {r.student_class: r.cnt for r in student_counts},
        "report_counts_raw": {r.student_class: r.cnt for r in report_counts_raw},
        "report_distinct_students": {r.student_class: r.cnt for r in report_distinct},
        "students_with_multiple_reports": [
            {
                "student_id": r.student_id,
                "name": r.student_name,
                "class": r.student_class,
                "report_count": r.cnt,
                "terms": r.terms,
                "years": r.years,
            }
            for r in multi_reports
        ],
    }


@app.post("/api/reports/generate")
def generate_reports(
    payload: ReportGenerateIn,
    _: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Generate and save reports for students in a class"""
    student_class = payload.student_class
    term = payload.term
    academic_year = payload.academic_year
    assign_positions = payload.assign_positions
    
    # Get all students in the class
    students = db.scalars(select(Student).where(Student.student_class == student_class)).all()
    if not students:
        raise HTTPException(status_code=404, detail="No students found in this class")
    
    # Get settings
    settings = _read_settings(db)
    logo = db.scalar(select(SchoolAsset).where(SchoolAsset.key == "logo"))
    
    generated_count = 0
    
    for student in students:
        # Get all grade records for this student
        records = db.scalars(
            select(GradeRecord)
            .where(GradeRecord.student_id == student.student_id)
            .where(GradeRecord.term == term)
            .where(GradeRecord.academic_year == academic_year)
        ).all()
        
        # Calculate statistics - include students even with no grades
        if records:
            scores = [r.score for r in records]
            avg_score = sum(scores) / len(scores)
        else:
            # No grades yet - use 0 as average
            avg_score = 0.0
            scores = []
        
        is_form1_or_2 = student_class in FORM_1_2_CLASSES
        
        if is_form1_or_2:
            # For FORM 1&2: use average as aggregate
            aggregate = avg_score
            points = None
        else:
            aggregate = calc_f34_aggregate(records, student_class)
            points = aggregate
        
        # Prepare report data
        report_data = {
            "school_name": settings.school_name,
            "student_id": student.student_id,
            "student_name": student.name,
            "student_class": student_class,
            "term": term,
            "academic_year": academic_year,
            "subjects": [
                {
                    "subject": r.subject,
                    "score": r.score,
                    "grade": calc_grade_backend(r.score, student_class)['grade'],
                    "result": calc_grade_backend(r.score, student_class)['result'],
                    "comment": r.teacher_comment
                }
                for r in records
            ] if records else [],
            "average_score": avg_score,
            "aggregate": aggregate,
            "is_form1_or_2": is_form1_or_2,
            "has_grades": len(records) > 0
        }
        
        # Check if report already exists — handle duplicates
        all_existing = db.scalars(
            select(SchoolReport)
            .where(SchoolReport.student_id == student.student_id)
            .where(SchoolReport.term == term)
            .where(SchoolReport.academic_year == academic_year)
            .order_by(SchoolReport.id.asc())
        ).all()

        if all_existing:
            existing = all_existing[0]
            for dup in all_existing[1:]:
                db.delete(dup)
            existing.report_data = json.dumps(report_data)
            existing.average_score = avg_score
            existing.aggregate_points = aggregate if not is_form1_or_2 else avg_score
            existing.total_subjects = len(records)
            existing.student_name = student.name
            existing.student_class = student_class
        else:
            new_report = SchoolReport(
                student_id=student.student_id,
                student_name=student.name,
                student_class=student_class,
                term=term,
                academic_year=academic_year,
                total_subjects=len(records),
                average_score=avg_score,
                aggregate_points=aggregate if not is_form1_or_2 else avg_score,
                report_data=json.dumps(report_data),
                position=None
            )
            db.add(new_report)
        
        generated_count += 1
    
    if assign_positions and student_class in FORM_1_2_CLASSES:
        assign_positions_in_class(db, student_class, term, academic_year)
    
    db.commit()
    
    return {"ok": True, "generated": generated_count, "class": student_class}


@app.post("/api/reports/assign-positions")
def assign_positions_endpoint(
    payload: ReportAssignPositionsIn,
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """Assign positions to FORM 1&2 students based on average scores"""
    student_class = payload.student_class
    term = payload.term
    academic_year = payload.academic_year
    
    if not student_class or student_class not in FORM_1_2_CLASSES:
        raise HTTPException(status_code=400, detail="Only FORM 1 and FORM 2 can have positions assigned")
    
    count = assign_positions_in_class(db, student_class, term, academic_year)
    
    return {"ok": True, "assigned": count}


def assign_positions_in_class(db: Session, student_class: str, term: str, academic_year: str) -> int:
    """Helper function to assign positions based on average scores"""
    reports = db.scalars(
        select(SchoolReport)
        .where(SchoolReport.student_class == student_class)
        .where(SchoolReport.term == term)
        .where(SchoolReport.academic_year == academic_year)
        .order_by(SchoolReport.average_score.desc())
    ).all()
    
    if not reports:
        return 0
    
    # Assign positions with ties
    current_pos = 1
    prev_score = None
    
    for i, report in enumerate(reports):
        if prev_score is not None and report.average_score < prev_score:
            current_pos = i + 1
        
        report.position = current_pos
        prev_score = report.average_score
    
    db.commit()
    return len(reports)


def _build_report_pdf_elements(report: SchoolReport, report_data: dict) -> list:
    """Build PDF elements for a school report - used by all PDF generation endpoints."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    
    styles = getSampleStyleSheet()
    elements = []
    
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=18,
        textColor=colors.HexColor('#1e1b4b'),
        fontName='Helvetica-Bold',
        spaceAfter=12
    )
    
    elements.append(Paragraph(f"{report_data.get('school_name', 'SCHOOL')}", title_style))
    elements.append(Paragraph("PROGRESS REPORT CARD", styles['Heading2']))
    elements.append(Spacer(1, 0.2*inch))
    
    info_data = [
        ["Student Name:", report.student_name],
        ["Student ID:", report.student_id],
        ["Class:", report.student_class],
        ["Term:", report.term],
        ["Academic Year:", report.academic_year],
    ]
    if report.position:
        suffix = 'st' if report.position == 1 else 'nd' if report.position == 2 else 'rd' if report.position == 3 else 'th'
        info_data.append(["Position:", f"{report.position}{suffix}"])
    
    info_table = Table(info_data, colWidths=[2*inch, 3*inch])
    info_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f1f5f9')),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 0.3*inch))
    
    subjects_data = [['Subject', 'Score', 'Grade', 'Result', 'Comment']]
    for subj in report_data.get('subjects', []):
        subjects_data.append([
            subj.get('subject', ''),
            str(subj.get('score', 0)),
            subj.get('grade', ''),
            subj.get('result', ''),
            subj.get('comment', '') or ''
        ])
    
    subjects_table = Table(subjects_data, colWidths=[2*inch, 0.8*inch, 0.8*inch, 1.2*inch, 2.2*inch])
    subjects_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e293b')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.white),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    elements.append(subjects_table)
    elements.append(Spacer(1, 0.3*inch))
    
    summary_data = [
        ['Total Subjects:', str(report.total_subjects)],
        ['Average Score:', f"{report.average_score:.1f}%"],
    ]
    if report.aggregate_points and report.student_class not in FORM_1_2_CLASSES:
        summary_data.append(['Aggregate Points:', f"{report.aggregate_points:.1f}"])
    
    summary_table = Table(summary_data, colWidths=[2.5*inch, 2.5*inch])
    summary_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f1f5f9')),
    ]))
    elements.append(summary_table)
    
    return elements


@app.get("/api/reports/download/{report_id}")
def download_report_pdf(
    report_id: int,
    _: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Download individual report as PDF"""
    if not REPORTLAB_AVAILABLE:
        raise HTTPException(status_code=500, detail="PDF generation library not available")
    
    report = db.scalar(select(SchoolReport).where(SchoolReport.id == report_id))
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    
    report_data = json.loads(report.report_data)
    
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    elements = _build_report_pdf_elements(report, report_data)
    doc.build(elements)
    buffer.seek(0)
    
    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=\"{report.student_name}_{report.term}_{report.academic_year}_Report.pdf\""}
    )


@app.post("/api/reports/download-batch")
def download_batch_reports(
    payload: ReportDownloadBatchIn,
    _: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Download multiple reports as ZIP"""
    if not REPORTLAB_AVAILABLE:
        raise HTTPException(status_code=500, detail="PDF generation library not available")
    
    import zipfile
    from fastapi.responses import StreamingResponse
    
    report_ids = payload.report_ids
    if not report_ids:
        raise HTTPException(status_code=400, detail="No report IDs provided")
    
    reports = db.scalars(select(SchoolReport).where(SchoolReport.id.in_(report_ids))).all()
    
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for report in reports:
            report_data = json.loads(report.report_data)
            pdf_buffer = BytesIO()
            doc = SimpleDocTemplate(pdf_buffer, pagesize=A4)
            elements = _build_report_pdf_elements(report, report_data)
            doc.build(elements)
            pdf_buffer.seek(0)
            
            pos_prefix = f"{report.position}_" if report.position else ""
            pdf_filename = f"{pos_prefix}{report.student_name}_{report.term}_{report.academic_year}_Report.pdf"
            zip_file.writestr(pdf_filename, pdf_buffer.getvalue())
    
    zip_buffer.seek(0)
    
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=\"reports_batch.zip\""}
    )


@app.post("/api/reports/download-class")
def download_class_reports(
    payload: ReportDownloadClassIn,
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """Download all reports for a class as ZIP"""
    if not REPORTLAB_AVAILABLE:
        raise HTTPException(status_code=500, detail="PDF generation library not available")
    
    import zipfile
    from fastapi.responses import StreamingResponse
    
    student_class = payload.student_class
    term = payload.term
    academic_year = payload.academic_year
    
    reports = db.scalars(
        select(SchoolReport)
        .where(SchoolReport.student_class == student_class)
        .where(SchoolReport.term == term)
        .where(SchoolReport.academic_year == academic_year)
        .order_by(SchoolReport.average_score.desc())
    ).all()
    
    if not reports:
        raise HTTPException(status_code=404, detail="No reports found for this class")
    
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for report in reports:
            report_data = json.loads(report.report_data)
            pdf_buffer = BytesIO()
            doc = SimpleDocTemplate(pdf_buffer, pagesize=A4)
            elements = _build_report_pdf_elements(report, report_data)
            doc.build(elements)
            pdf_buffer.seek(0)
            
            pos_prefix = f"{report.position}_" if report.position else ""
            pdf_filename = f"{pos_prefix}{report.student_name}_{report.term}_{report.academic_year}_Report.pdf"
            zip_file.writestr(pdf_filename, pdf_buffer.getvalue())
    
    zip_buffer.seek(0)
    
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=\"{student_class}_{term}_{academic_year}_Reports.zip\""}
    )

