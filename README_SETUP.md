# StudyVerse LMS Secure Starter

## Local setup on macOS
1. Install Python 3.11+ and PostgreSQL.
2. Open this folder in VS Code. In Terminal:
   `python3 -m venv .venv`
   `source .venv/bin/activate`
   `pip install -r requirements.txt`
   `cp .env.example .env`
3. Create database: `createdb studyverse_lms`. Edit `DATABASE_URL` in `.env` to match your local PostgreSQL username/password.
4. Generate a secret with `python -c "import secrets; print(secrets.token_hex(32))"` and put it in `FLASK_SECRET_KEY`.
5. Gmail: enable Google 2-Step Verification, then create an App Password in Google Account → Security → App passwords. Put the generated app password in `GMAIL_APP_PASSWORD`. Never use your regular Gmail password.
6. Run `python app.py` and open `http://127.0.0.1:5000`. Tables and sample courses are initialized on first start.
7. Register at `/signup`. Password recovery flow: `/forgot-password` → email OTP → `/verify-otp` → `/reset-password`.

## Worldwide deployment on Render
1. Push this folder to GitHub. `.gitignore` prevents `.env` from being uploaded; check before pushing.
2. On Render, choose New → Blueprint and connect the repository. Render reads `render.yaml` and provisions a web service plus PostgreSQL database.
3. Enter `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD` in the Render environment settings. The file configures HTTPS cookies and a generated Flask secret.
4. Deploy, then open the public HTTPS URL Render provides. Test signup/login/password reset on a phone using mobile data.
5. Do not use localhost in Render's `DATABASE_URL`; use the hosted database connection string.

## Security notes
Passwords and OTPs are hashed in PostgreSQL. OTPs expire after 10 minutes, allow 5 attempts, are single-use, and have a 60-second resend cooldown. Reset responses do not reveal whether an email is registered. Before a real public launch, add request rate limiting, email verification for new accounts, monitoring/backups, and a privacy notice. This starter is not a security audit.
