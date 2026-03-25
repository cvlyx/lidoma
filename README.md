# 🎓 LIDOMA School Exam Report Analyzer

<div align="center">

![LIDOMA Banner](image.png)

**A full-stack academic management system for LIDOMA Private Secondary School**  
*Manage students, enter grades, generate reports, and empower parents — all in one place.*

[![FastAPI](https://img.shields.io/badge/FastAPI-0.100-009688?style=flat-square&logo=fastapi)](https://fastapi.tiangolo.com/)
[![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python)](https://python.org)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-supported-336791?style=flat-square&logo=postgresql)](https://postgresql.org)
[![Tailwind CSS](https://img.shields.io/badge/TailwindCSS-3.4-38BDF8?style=flat-square&logo=tailwindcss)](https://tailwindcss.com)
[![Deploy on Render](https://img.shields.io/badge/Backend-Render-46E3B7?style=flat-square&logo=render)](https://render.com)
[![Deploy on Vercel](https://img.shields.io/badge/Frontend-Vercel-000000?style=flat-square&logo=vercel)](https://vercel.com)

</div>

---

## ✨ Overview

LIDOMA is a modern, responsive school exam report management system built for secondary schools. It provides teachers and administrators with a streamlined interface to register students, enter subject scores, generate printable reports, and share results with parents — all backed by a secure REST API.

---

## 🚀 Features

### 👩‍🏫 Teacher / Admin Dashboard (`LIDOMA.HTML`)
- **Student Management** — Register, edit, and delete students with auto-generated IDs (`LID####`)
- **Grade Entry** — Enter subject scores per student, term, and academic year with teacher comments
- **Automatic Grading** — Scores are instantly converted to grades, points, and results (DISTINCTION → FAIL)
- **Printable Reports** — Generate A4-formatted individual student reports, exportable as PDF or Word
- **Analytics Tab** — Visual performance charts and class-wide statistics
- **Reports Archive** — Browse, filter, and bulk-download historical reports
- **Settings** — Customize school name, academic year, report title, and school logo
- **Parent Portal Link** — Direct link to the parent-facing results portal

### 👨‍👩‍👧 Parent Portal (`PARENT_PORTAL.HTML`)
- **Secure Lookup** — Parents access results using student name + ID (no login required)
- **Performance Dashboard** — Clean report card with subject scores, grades, and results
- **Academic Visualization** — Animated bar chart showing per-subject performance
- **Aggregate Points** — Best-6 aggregate calculation with distinction/credit/pass ranking
- **Printable View** — Print-optimized layout for physical copies

### 🔒 Backend API (`/backend`)
- **JWT Authentication** — Secure token-based auth for teachers and admins
- **Role-Based Access** — `admin` and `teacher` roles with protected endpoints
- **Auto Report Sync** — Reports are automatically created/updated when grades change
- **Position Ranking** — Assign class positions based on aggregate scores
- **PDF Generation** — Server-side PDF export via ReportLab
- **CORS Configured** — Supports Vercel preview URLs and custom origins

---

## 🏗️ Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Vanilla HTML/JS, Tailwind CSS 3.4, Lucide Icons |
| Backend | Python 3.11, FastAPI, SQLAlchemy 2.0 |
| Database | PostgreSQL (via psycopg2) |
| Auth | JWT (python-jose), Argon2 password hashing |
| PDF Export | ReportLab |
| Frontend Deploy | Vercel |
| Backend Deploy | Render |

---

## 📁 Project Structure

```
├── LIDOMA.HTML              # Teacher/Admin dashboard (frontend)
├── PARENT_PORTAL.HTML       # Parent results portal (frontend)
├── image.png                # School logo
├── vercel.json              # Vercel frontend deployment config
├── render.yaml              # Render backend deployment config
└── backend/
    ├── app.py               # FastAPI application & all API routes
    ├── models.py            # SQLAlchemy ORM models
    ├── db.py                # Database connection & session
    ├── settings.py          # Pydantic settings (env-based config)
    ├── create_tables.py     # DB table initialization script
    ├── requirements.txt     # Python dependencies
    └── .env.example         # Environment variable template
```

---

## ⚙️ Local Setup

### Prerequisites
- Python 3.11+
- PostgreSQL database

### 1. Clone the repository

```bash
git clone https://github.com/your-username/lidoma-school.git
cd lidoma-school
```

### 2. Set up the backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` with your values:

```env
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST/DBNAME?sslmode=require
JWT_SECRET=your-long-random-secret
ADMIN_USERNAME=admin
ADMIN_PASSWORD=your-secure-password
ALLOWED_ORIGINS=http://localhost:8787,http://127.0.0.1:8787
```

### 4. Initialize the database

```bash
python create_tables.py
```

### 5. Run the API server

```bash
uvicorn app:app --reload --port 8000
```

API docs available at: `http://localhost:8000/docs`

### 6. Open the frontend

Open `LIDOMA.HTML` directly in your browser or serve it with any static file server.

---

## 🌐 Deployment

### Frontend → Vercel

The `vercel.json` routes all requests to `LIDOMA.HTML`. Just connect your GitHub repo to Vercel and deploy.

### Backend → Render

The `render.yaml` is pre-configured for Render. Set the following environment variables in your Render service dashboard:

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string |
| `JWT_SECRET` | Long random secret for JWT signing |
| `ADMIN_USERNAME` | Admin login username |
| `ADMIN_PASSWORD` | Admin login password |
| `ALLOWED_ORIGINS` | Comma-separated list of allowed frontend URLs |

---

## 📊 Grading Scale

| Score Range | Grade | Points | Result |
|-------------|-------|--------|--------|
| 90 – 100 | 1 | 1 | DISTINCTION |
| 80 – 89 | 2 | 2 | DISTINCTION |
| 70 – 79 | 3 | 3 | STRONG CREDIT |
| 66 – 69 | 4 | 4 | CREDIT |
| 60 – 65 | 5 | 5 | CREDIT |
| 50 – 59 | 6 | 6 | CREDIT |
| 46 – 49 | 7 | 7 | PASS |
| 40 – 45 | 8 | 8 | PASS |
| 0 – 39 | 9 | 9 | FAIL |

> **Note:** FORM 1 & 2 use a simplified A/B/C/F grading scale. Aggregate is calculated from the best 6 subjects for FORM 3 & 4.

---

## 🔑 API Endpoints (Summary)

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `POST` | `/api/auth/login` | — | Get JWT token |
| `GET` | `/api/students` | Teacher | List all students |
| `POST` | `/api/students` | Teacher | Register a student |
| `GET` | `/api/records` | Teacher | List grade records |
| `POST` | `/api/records` | Teacher | Add a grade record |
| `GET` | `/api/reports` | Teacher | List all reports |
| `POST` | `/api/parent/lookup` | — | Parent result lookup |
| `POST` | `/api/admin/settings` | Admin | Update school settings |
| `POST` | `/api/admin/logo` | Admin | Upload school logo |

Full interactive docs: `http://your-api-url/docs`

---

## 🛡️ Security

- Passwords hashed with **Argon2** (industry-standard)
- JWT tokens expire after **12 hours**
- Parent portal uses **name + ID verification** (no account needed)
- CORS restricted to configured origins only

---

## 📄 License

This project is proprietary software developed for **LIDOMA Private Secondary School**.  
All rights reserved © 2026.

---

<div align="center">
  <sub>Built with ❤️ for LIDOMA Private Secondary School</sub>
</div>
