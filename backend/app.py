from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db import SessionLocal, init_db
from models import AppSetting, GradeRecord, SchoolAsset, Student, User
from settings import Settings


settings = Settings()
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

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
    allow_origins=allowed_origins,
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
    # Ensure admin user exists
    db = SessionLocal()
    try:
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


@app.delete("/api/records/{record_id}", status_code=204)
def delete_record(
    record_id: int,
    _: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    res = db.execute(delete(GradeRecord).where(GradeRecord.id == record_id))
    if res.rowcount == 0:
        db.rollback()
        raise HTTPException(status_code=404, detail="Record not found")
    db.commit()
    return Response(status_code=204)


@app.delete("/api/records/clear", status_code=204)
def clear_records(
    _: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    db.execute(delete(GradeRecord))
    db.commit()
    return Response(status_code=204)


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

