# TSR Enterprises — Fleet & Finance Management

Premium yellow/gold business dashboard for managing company vehicles, drivers/employees,
expenses (vehicle/driver/house/office), fuel entries, billing & receivables, EMI & loans,
GST, purse/cash tracking (TSR & TRR), Mithi tracking, documents, access control and a
driver self-service portal.

## Tech Stack
- **Backend:** FastAPI (Python)
- **Frontend:** Jinja2 templates + Bootstrap 5 + Bootstrap Icons + ApexCharts
- **Design:** Yellow/gold glassmorphism theme with animations (`static/css/style.css`)
- **Database:** PostgreSQL (tables auto-created on startup)
- **Auth:** Google OAuth (admin/viewer) + mobile OTP for drivers (Fast2SMS)
- **Documents:** Google Drive uploads via service account
- **Deploy:** Docker / docker-compose / Render / Procfile

## Quick Start (local)
```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env        # fill in DATABASE_URL, Google keys, ALLOWED_EMAILS
.venv\Scripts\python main.py  # http://localhost:8000
```

Or with Docker (brings up PostgreSQL too):
```bash
docker compose up --build
```

## Configuration (.env)
| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string |
| `GOOGLE_CLIENT_ID/SECRET/REDIRECT_URI` | Google OAuth login |
| `SECRET_KEY` | Session signing key |
| `ALLOWED_EMAILS` | Comma list of emails bootstrapped as admin |
| `GOOGLE_DRIVE_FOLDER_ID` | Root Drive folder for document uploads |
| `GOOGLE_CREDENTIALS_JSON` / `SERVICE_ACCOUNT_FILE` | Drive service account |
| `FAST2SMS_API_KEY` | Driver OTP SMS (dev mode shows OTP when empty) |
| `SESSION_MAX_AGE` | Session lifetime in seconds |

## Roles
- **admin** — full add/edit/delete/upload/export + user management
- **viewer** — read-only everywhere (writes blocked by middleware, buttons hidden)
- **driver** — Driver Portal only: own salary/advance/meals/diesel + add diesel entry

## Project Layout
```
main.py            app entry: middleware, routers, startup init
config.py          environment settings
routes/            auth, dashboard, vehicles, drivers, expenses, fuel,
                   billing, purse, emi, gst, access, driver_portal, mithi
services/          db.py, sheets_service.py (tables/CRUD/audit),
                   drive_service.py, auth_service.py
utils/             templates.py, permissions.py, duplicate_check.py
templates/         base + 17 page templates (glassmorphism UI)
static/            css/style.css (theme), js/app.js (shared helpers)
```

## Notes
- Mithi is **not** EMI: persons pay `main_amount × rate ÷ 100` monthly until the
  main amount itself is returned; then they are Closed.
- Expenses paid by TSR/TRR automatically appear in the Purse module.
- Billing auto-calculates SGST/CGST/TDS; receivables can be split across an
  invoice's bills and bill status updates to Pending/Partial/Paid.
- Sales GST is read from Billing; GST payable = output GST − input GST.
