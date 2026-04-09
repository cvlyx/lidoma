from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
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
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

CLASSES = ["FORM 1", "FORM 2", "FORM 3", "FORM 4"]
TERMS = ["First Term", "Second Term", "Third Term"]
FORM_1_2_CLASSES = {"FORM 1", "FORM 2"}


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

    if is_f12:
        aggregate = avg_score
    else:
        if records:
            pts = sorted([calc_grade_backend(r.score, student.student_class)['points'] for r in records])
            aggregate = float(sum(pts[:6]))
        else:
            aggregate = 0.0

    app_settings = _read_settings(db)
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
                "grade": calc_grade_backend(r.score, student.student_class)['grade'],
                "result": calc_grade_backend(r.score, student.student_class)['result'],
                "comment": r.teacher_comment,
            }
            for r in records
        ],
        "average_score": avg_score,
        "aggregate": aggregate,
        "is_form1_or_2": is_f12,
        "has_grades": len(records) > 0,
    }

    existing = db.scalar(
        select(SchoolReport)
        .where(SchoolReport.student_id == student_id)
        .where(SchoolReport.term == term)
        .where(SchoolReport.academic_year == academic_year)
    )

    if existing:
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

        # Deduplicate school_reports — keep only the latest per student/term/year
        dupes = db.execute(
            select(
                SchoolReport.student_id,
                SchoolReport.term,
                SchoolReport.academic_year,
                func.count(SchoolReport.id).label("cnt")
            ).group_by(
                SchoolReport.student_id,
                SchoolReport.term,
                SchoolReport.academic_year
            ).having(func.count(SchoolReport.id) > 1)
        ).all()

        for row in dupes:
            reports = db.scalars(
                select(SchoolReport)
                .where(SchoolReport.student_id == row.student_id)
                .where(SchoolReport.term == row.term)
                .where(SchoolReport.academic_year == row.academic_year)
                .order_by(SchoolReport.id.desc())
            ).all()
            # Keep the first (latest), delete the rest
            for r in reports[1:]:
                db.delete(r)
        if dupes:
            db.commit()
    finally:
        db.close()


@app.get("/api/health")
def health():
    return {"ok": True}


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
        raise HTTPException(status_code=409, detail="Student already exists")
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

    q = (
        select(SchoolReport, grade_agg)
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

        rec_map: dict[tuple, list[tuple[int, str]]] = defaultdict(list)
        for rec in all_f34_records:
            rec_map[(rec.student_id, rec.term, rec.academic_year)].append((rec.score, rec.student_class))

        for row in f34_rows:
            r = row.SchoolReport
            key = (r.student_id, r.term, r.academic_year)
            entries = rec_map.get(key, [])
            if entries:
                pts = sorted([calc_grade_backend(score, cls)['points'] for score, cls in entries])
                agg_points_map[key] = float(sum(pts[:6]))
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
        if records:
            sorted_pts = sorted([calc_grade_backend(rec.score, r.student_class)['points'] for rec in records])
            aggregate = sum(sorted_pts[:6])
        else:
            aggregate = 0.0

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
            # For FORM 3&4: calculate best 6 points
            if records:
                grades = [calc_grade_backend(r.score, student_class) for r in records]
                sorted_points = sorted([g['points'] for g in grades])
                best6 = sorted_points[:6]
                aggregate = sum(best6)
                points = aggregate
            else:
                aggregate = 0.0
                points = 0
        
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
        
        # Check if report already exists
        existing = db.scalar(
            select(SchoolReport)
            .where(SchoolReport.student_id == student.student_id)
            .where(SchoolReport.term == term)
            .where(SchoolReport.academic_year == academic_year)
        )
        
        if existing:
            existing.report_data = json.dumps(report_data)
            existing.average_score = avg_score
            existing.aggregate_points = aggregate if not is_form1_or_2 else avg_score
            existing.total_subjects = len(records)
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

