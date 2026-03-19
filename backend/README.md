# LIDOMA backend (FastAPI + Neon Postgres)

## Setup

1) Create a `.env` next to this README (copy from `.env.example`).

2) Install **Python 3.13.x** (recommended). Python 3.14 can cause dependency builds to hang (e.g. `pydantic-core`).

2) Install deps:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

3) Run:

```bash
uvicorn app:app --reload --port 8787
```

The API will be at `http://127.0.0.1:8787`.

## Notes

- Do **not** commit your `.env` (it contains secrets).
- On first start, the server will auto-create tables and ensure an admin user exists using `ADMIN_USERNAME` / `ADMIN_PASSWORD`.
