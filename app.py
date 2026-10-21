
import os
import hmac
import hashlib
import json
import re
import secrets
import smtplib
import threading
import time
from datetime import date
from email.message import EmailMessage
from functools import wraps
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from flask import (
    Flask, abort, make_response, render_template, jsonify, request,
    redirect, url_for, session, flash
)
from dotenv import load_dotenv
import psycopg
from db import DATABASE_URL, get_connection

load_dotenv()

app = Flask(__name__)

IS_PRODUCTION = bool(os.getenv("RAILWAY_ENVIRONMENT")) or os.getenv("FLASK_ENV") == "production"
SECRET_KEY = os.getenv("FLASK_SECRET_KEY")
if IS_PRODUCTION and not SECRET_KEY:
    raise RuntimeError("FLASK_SECRET_KEY must be configured for production")
app.secret_key = SECRET_KEY or "dev-only-change-this-key"
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = IS_PRODUCTION

ACCOUNTS = {
    "student": {
        "username": os.getenv("LMS_USERNAME", "" if IS_PRODUCTION else "student"),
        "password": os.getenv("LMS_PASSWORD", "" if IS_PRODUCTION else "StudyVerse@123"),
        "email": os.getenv("LMS_EMAIL", "").strip().lower(),
        "display_name": "Student",
    },
    "instructor": {
        "username": os.getenv("LMS_INSTRUCTOR_USERNAME", ""),
        "password": os.getenv("LMS_INSTRUCTOR_PASSWORD", ""),
        "email": os.getenv("LMS_INSTRUCTOR_EMAIL", "").strip().lower(),
        "display_name": "Instructor",
    },
}
OTP_EXPIRY_SECONDS = 600
OTP_MAX_ATTEMPTS = 5
AI_DAILY_REQUEST_LIMIT = 30
ALLOWED_UPLOAD_EXTENSIONS = {
    "pdf", "ppt", "pptx", "doc", "docx", "txt", "csv", "xlsx", "xls",
    "png", "jpg", "jpeg", "zip", "md", "rtf",
}
DATABASE_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS users (
        id BIGSERIAL PRIMARY KEY,
        username VARCHAR(32) NOT NULL,
        email VARCHAR(254) NOT NULL,
        display_name VARCHAR(80) NOT NULL,
        password_hash TEXT NOT NULL,
        role VARCHAR(20) NOT NULL DEFAULT 'student'
            CHECK (role IN ('student', 'instructor')),
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS users_username_lower_idx ON users (LOWER(username))",
    "CREATE UNIQUE INDEX IF NOT EXISTS users_email_lower_idx ON users (LOWER(email))",
    """
    CREATE TABLE IF NOT EXISTS student_assignments (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title VARCHAR(200) NOT NULL,
        due_date DATE NOT NULL,
        completed BOOLEAN NOT NULL DEFAULT FALSE,
        filename TEXT,
        content_type TEXT,
        file_data BYTEA,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS student_assignments_user_due_idx ON student_assignments (user_id, due_date)",
    """
    CREATE TABLE IF NOT EXISTS ai_chat_usage (
        user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        usage_date DATE NOT NULL DEFAULT CURRENT_DATE,
        request_count INTEGER NOT NULL DEFAULT 0 CHECK (request_count >= 0)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS classrooms (
        id BIGSERIAL PRIMARY KEY,
        instructor_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name VARCHAR(120) NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        join_code VARCHAR(8) NOT NULL UNIQUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS classroom_members (
        classroom_id BIGINT NOT NULL REFERENCES classrooms(id) ON DELETE CASCADE,
        student_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        joined_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (classroom_id, student_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS classroom_assignments (
        id BIGSERIAL PRIMARY KEY,
        classroom_id BIGINT NOT NULL REFERENCES classrooms(id) ON DELETE CASCADE,
        instructor_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title VARCHAR(200) NOT NULL,
        instructions TEXT NOT NULL DEFAULT '',
        due_date DATE NOT NULL,
        attachment_name TEXT,
        attachment_type TEXT,
        attachment_data BYTEA,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS classroom_submissions (
        id BIGSERIAL PRIMARY KEY,
        assignment_id BIGINT NOT NULL REFERENCES classroom_assignments(id) ON DELETE CASCADE,
        student_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        filename TEXT NOT NULL,
        content_type TEXT NOT NULL,
        file_data BYTEA NOT NULL,
        submitted_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (assignment_id, student_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS classroom_assignments_class_due_idx ON classroom_assignments (classroom_id, due_date)",
)
_database_initialized = False
_database_initialization_lock = threading.Lock()


def initialize_database():
    global _database_initialized
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL must be configured to use accounts and assignments")
    if _database_initialized:
        return
    with _database_initialization_lock:
        if _database_initialized:
            return
        with get_connection() as connection:
            with connection.cursor() as cursor:
                for statement in DATABASE_SCHEMA:
                    cursor.execute(statement)
        _database_initialized = True


def find_user_by_login(identifier):
    initialize_database()
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, username, email, display_name, password_hash, role
                FROM users
                WHERE LOWER(username) = LOWER(%s) OR LOWER(email) = LOWER(%s)
                LIMIT 1
                """,
                (identifier, identifier),
            )
            return cursor.fetchone()


def find_user_by_email(email):
    initialize_database()
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, username, email, display_name, password_hash, role
                FROM users WHERE LOWER(email) = LOWER(%s) LIMIT 1
                """,
                (email,),
            )
            return cursor.fetchone()


def provision_legacy_user(role):
    account = ACCOUNTS.get(role)
    if not account or not account["username"] or not account["password"]:
        return None
    initialize_database()
    email = account["email"] or f"{account['username']}@studyverse.invalid"
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO users (username, email, display_name, password_hash, role)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    account["username"],
                    email,
                    account["display_name"],
                    generate_password_hash(account["password"]),
                    role,
                ),
            )
            cursor.execute(
                """
                SELECT id, username, email, display_name, password_hash, role
                FROM users WHERE LOWER(username) = LOWER(%s) LIMIT 1
                """,
                (account["username"],),
            )
            return cursor.fetchone()


def set_user_session(user):
    session.clear()
    session["logged_in"] = True
    session["user_id"] = user[0]
    session["username"] = user[1]
    session["display_name"] = user[3]
    session["role"] = user[5]


def require_role(role):
    if session.get("role") != role:
        return jsonify({"error": f"This action is available to {role}s only."}), 403
    return None


def upload_data(upload):
    if not upload or not upload.filename:
        return None, None, None, None
    filename = secure_filename(upload.filename)
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if not filename or extension not in ALLOWED_UPLOAD_EXTENSIONS:
        return None, None, None, (jsonify({
            "error": "Unsupported file type. Upload a PDF, presentation, document, spreadsheet, image, text file, or ZIP."
        }), 400)
    data = upload.read(15 * 1024 * 1024 + 1)
    if len(data) > 15 * 1024 * 1024:
        return None, None, None, (jsonify({"error": "Files must be 15 MB or smaller."}), 413)
    return filename, upload.mimetype or "application/octet-stream", data, None


def csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


@app.context_processor
def inject_csrf_token():
    return {"csrf_token": csrf_token}


@app.before_request
def protect_post_requests():
    if request.method != "POST":
        return None
    expected = session.get("_csrf_token", "")
    submitted = request.form.get("csrf_token", "") or request.headers.get("X-CSRF-Token", "")
    if not expected or not submitted or not hmac.compare_digest(expected, submitted):
        if request.path.startswith("/api/"):
            return jsonify({"error": "Your session expired. Refresh the page and try again."}), 400
        abort(400)
    return None


def send_otp_email(email, otp):
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    if not smtp_user or not smtp_password:
        raise RuntimeError("SMTP_USER and SMTP_PASSWORD must be configured")

    message = EmailMessage()
    message["Subject"] = "Your StudyVerse sign-in code"
    message["From"] = os.getenv("SMTP_FROM", smtp_user)
    message["To"] = email
    message.set_content(
        f"Your StudyVerse sign-in code is {otp}. "
        "It expires in 10 minutes. If you did not request this code, "
        "you can ignore this email."
    )

    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "465"))
    with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=10) as server:
        server.login(smtp_user, smtp_password)
        server.send_message(message)


def otp_digest(otp):
    return hmac.new(
        app.secret_key.encode("utf-8"), otp.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def clear_pending_otp():
    for key in ("otp_hash", "otp_expires_at", "otp_attempts", "otp_role", "otp_email"):
        session.pop(key, None)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


@app.before_request
def require_login():
    public_endpoints = {"login", "register", "forgot_password", "static", "health"}

    if request.endpoint not in public_endpoints:
        if not session.get("logged_in"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Please sign in to continue."}), 401
            return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("logged_in"):
        return redirect(url_for("home"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role", "student")
        account = ACCOUNTS.get(role)

        try:
            user = find_user_by_login(username) if DATABASE_URL else None
        except (psycopg.Error, RuntimeError):
            app.logger.exception("Could not look up StudyVerse account")
            flash("Sign-in is temporarily unavailable. Please try again shortly.")
            return render_template("login.html", page="login"), 503

        if user and user[5] == role and check_password_hash(user[4], password):
            set_user_session(user)
            return redirect(url_for("home"))

        valid_username = bool(account and account["username"]) and (
            hmac.compare_digest(username.casefold(), account["username"].casefold())
            or bool(account["email"]) and hmac.compare_digest(username.casefold(), account["email"])
        )
        valid_password = bool(account and account["password"]) and hmac.compare_digest(
            password, account["password"]
        )

        if DATABASE_URL and user is None and valid_username and valid_password:
            try:
                user = provision_legacy_user(role)
            except (psycopg.Error, RuntimeError):
                app.logger.exception("Could not migrate the configured StudyVerse account")
                flash("Sign-in is temporarily unavailable. Please try again shortly.")
                return render_template("login.html", page="login"), 503
            if user and user[5] == role and check_password_hash(user[4], password):
                set_user_session(user)
                return redirect(url_for("home"))
        elif not DATABASE_URL and valid_username and valid_password:
            session.clear()
            session["logged_in"] = True
            session["username"] = username
            session["role"] = role
            session["display_name"] = account["display_name"]
            return redirect(url_for("home"))

        flash("Incorrect username or password. Please try again.")

    return render_template("login.html", page="login")


@app.route("/register", methods=["GET", "POST"])
def register():
    if session.get("logged_in"):
        return redirect(url_for("home"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        display_name = request.form.get("display_name", "").strip()
        role = request.form.get("role", "student")
        password = request.form.get("password", "")
        password_confirmation = request.form.get("password_confirmation", "")

        if role not in {"student", "instructor"}:
            flash("Choose a valid account type.")
        elif not DATABASE_URL:
            flash("New accounts are temporarily unavailable. Please try again later.")
            return render_template("login.html", page="register"), 503
        elif not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
            flash("Choose a username with 3–32 letters, numbers, dots, dashes, or underscores.")
        elif not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(email) > 254:
            flash("Enter a valid email address.")
        elif not display_name or len(display_name) > 80:
            flash("Enter a name up to 80 characters long.")
        elif len(password) < 10 or len(password) > 128:
            flash("Your password must be between 10 and 128 characters.")
        elif password != password_confirmation:
            flash("The passwords do not match.")
        else:
            try:
                initialize_database()
                with get_connection() as connection:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            """
                            INSERT INTO users (username, email, display_name, password_hash, role)
                            VALUES (%s, %s, %s, %s, %s)
                            RETURNING id, username, email, display_name, password_hash, role
                            """,
                            (username, email, display_name, generate_password_hash(password), role),
                        )
                        user = cursor.fetchone()
            except psycopg.errors.UniqueViolation:
                flash("That username or email is already registered. Try signing in instead.")
            except (psycopg.Error, RuntimeError):
                app.logger.exception("Could not create StudyVerse account")
                flash("Account creation is temporarily unavailable. Please try again shortly.")
                return render_template("login.html", page="register"), 503
            else:
                set_user_session(user)
                return redirect(url_for("home"))

    return render_template(
        "login.html",
        page="register",
        selected_role=request.form.get("role", "student"),
    )


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if session.get("logged_in"):
        return redirect(url_for("home"))

    if request.method == "POST":
        if request.form.get("action") == "request":
            clear_pending_otp()
            role = request.form.get("role", "")
            email = request.form.get("email", "").strip().lower()
            account = ACCOUNTS.get(role)
            try:
                user = find_user_by_email(email) if DATABASE_URL else None
            except (psycopg.Error, RuntimeError):
                app.logger.exception("Could not look up email sign-in account")
                flash("Email sign-in is temporarily unavailable. Please try again shortly.")
                return render_template(
                    "login.html",
                    page="forgot",
                    otp_pending=False,
                    otp_email="",
                    selected_role=role or "student",
                ), 503

            registered_email = bool(user and user[5] == role)
            configured_email = bool(account and account["email"]) and hmac.compare_digest(
                email, account["email"]
            )
            if registered_email or configured_email:
                otp = f"{secrets.randbelow(1_000_000):06d}"
                try:
                    send_otp_email(email, otp)
                except (OSError, RuntimeError, smtplib.SMTPException, ValueError):
                    app.logger.exception("Could not send sign-in OTP email")
                    flash("Unable to send an email code right now. Please try again later.")
                else:
                    session["otp_hash"] = otp_digest(otp)
                    session["otp_expires_at"] = int(time.time()) + OTP_EXPIRY_SECONDS
                    session["otp_attempts"] = 0
                    session["otp_role"] = user[5] if registered_email else role
                    session["otp_email"] = email
                    flash("If the account matches, a sign-in code has been emailed.")
                    return redirect(url_for("forgot_password"))
            else:
                flash("If the account matches, a sign-in code has been emailed.")

        elif request.form.get("action") == "verify":
            otp = request.form.get("otp", "").strip()
            expires_at = session.get("otp_expires_at", 0)
            attempts = session.get("otp_attempts", 0)

            if not session.get("otp_hash") or time.time() >= expires_at:
                clear_pending_otp()
                flash("That sign-in code has expired. Request a new code.")
            elif attempts >= OTP_MAX_ATTEMPTS:
                clear_pending_otp()
                flash("Too many incorrect attempts. Request a new code.")
            elif len(otp) == 6 and otp.isdigit() and hmac.compare_digest(
                otp_digest(otp), session["otp_hash"]
            ):
                role = session.get("otp_role")
                email = session.get("otp_email")
                account = ACCOUNTS.get(role)
                user = find_user_by_email(email) if DATABASE_URL else None
                if user and user[5] == role:
                    set_user_session(user)
                elif account and account["email"] and hmac.compare_digest(
                    email, account["email"]
                ):
                    session.clear()
                    session["logged_in"] = True
                    session["username"] = email
                    session["role"] = role
                    session["display_name"] = account["display_name"]
                else:
                    clear_pending_otp()
                    flash("That account is no longer available. Please sign in again.")
                    return redirect(url_for("login"))
                return redirect(url_for("home"))
            else:
                session["otp_attempts"] = attempts + 1
                flash("Incorrect sign-in code. Please check it and try again.")

    expires_at = session.get("otp_expires_at", 0)
    otp_pending = bool(session.get("otp_hash")) and time.time() < expires_at
    if not otp_pending and session.get("otp_hash"):
        clear_pending_otp()
    return render_template(
        "login.html",
        page="forgot",
        otp_pending=otp_pending,
        otp_email=session.get("otp_email", ""),
        selected_role=request.form.get("role", "student"),
    )


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def home():
    if DATABASE_URL and not session.get("user_id"):
        role = session.get("role", "")
        account = ACCOUNTS.get(role)
        identifier = session.get("username", "").casefold()
        is_legacy_account = bool(account and account["username"]) and (
            hmac.compare_digest(identifier, account["username"].casefold())
            or bool(account["email"]) and hmac.compare_digest(identifier, account["email"])
        )
        if is_legacy_account:
            try:
                user = provision_legacy_user(role)
            except (psycopg.Error, RuntimeError):
                app.logger.exception("Could not migrate the active StudyVerse account")
                return "Account storage is temporarily unavailable.", 503
            if user and user[5] == role and check_password_hash(
                user[4], account["password"]
            ):
                set_user_session(user)
            else:
                session.clear()
                flash("Please sign in again to continue.")
                return redirect(url_for("login"))
    return render_template(
        "index.html",
        display_name=session.get("display_name")
        or ACCOUNTS.get(session.get("role"), {}).get("display_name", "Student"),
        role=session.get("role", "student"),
    )


@app.route("/api/health")
def health():
    return jsonify({
        "project": "StudyVerse LMS",
        "status": "running"
    })


@app.route("/api/courses")
@login_required
def get_courses():
    courses = [
        {"id": 1, "name": "Python Programming", "progress": 75},
        {"id": 2, "name": "Database Management", "progress": 50},
        {"id": 3, "name": "Web Development", "progress": 90},
        {"id": 4, "name": "Artificial Intelligence", "progress": 35}
    ]
    return jsonify(courses)


def assignment_payload(row):
    return {
        "id": row[0],
        "title": row[1],
        "due_date": row[2].isoformat(),
        "completed": row[3],
        "filename": row[4],
    }


def classroom_access(cursor, classroom_id, user_id, role):
    if role == "instructor":
        cursor.execute(
            "SELECT 1 FROM classrooms WHERE id = %s AND instructor_id = %s",
            (classroom_id, user_id),
        )
    else:
        cursor.execute(
            """
            SELECT 1 FROM classroom_members
            WHERE classroom_id = %s AND student_id = %s
            """,
            (classroom_id, user_id),
        )
    return cursor.fetchone() is not None


@app.route("/api/classrooms", methods=["GET", "POST"])
@login_required
def classrooms_api():
    user_id, error = require_database_user()
    if error:
        return error
    role = session.get("role")

    if request.method == "GET":
        initialize_database()
        with get_connection() as connection:
            with connection.cursor() as cursor:
                if role == "instructor":
                    cursor.execute(
                        """
                        SELECT c.id, c.name, c.description, c.join_code,
                               COUNT(DISTINCT m.student_id)
                        FROM classrooms c
                        LEFT JOIN classroom_members m ON m.classroom_id = c.id
                        WHERE c.instructor_id = %s
                        GROUP BY c.id ORDER BY c.created_at DESC
                        """,
                        (user_id,),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT c.id, c.name, c.description, NULL::text,
                               COUNT(DISTINCT m.student_id)
                        FROM classrooms c
                        JOIN classroom_members mine ON mine.classroom_id = c.id
                            AND mine.student_id = %s
                        LEFT JOIN classroom_members m ON m.classroom_id = c.id
                        GROUP BY c.id ORDER BY c.created_at DESC
                        """,
                        (user_id,),
                    )
                rows = cursor.fetchall()
        return jsonify({
            "classrooms": [
                {
                    "id": row[0],
                    "name": row[1],
                    "description": row[2],
                    "join_code": row[3],
                    "student_count": row[4],
                }
                for row in rows
            ],
            "role": role,
        })

    role_error = require_role("instructor")
    if role_error:
        return role_error
    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip() if isinstance(data.get("name"), str) else ""
    description = data.get("description", "").strip() if isinstance(data.get("description"), str) else ""
    if not name or len(name) > 120 or len(description) > 1000:
        return jsonify({"error": "Enter a class name (up to 120 characters) and description (up to 1000 characters)."}), 400
    initialize_database()
    with get_connection() as connection:
        with connection.cursor() as cursor:
            for _ in range(5):
                join_code = secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:8].upper()
                cursor.execute(
                    """
                    INSERT INTO classrooms (instructor_id, name, description, join_code)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (join_code) DO NOTHING
                    RETURNING id, name, description, join_code
                    """,
                    (user_id, name, description, join_code),
                )
                row = cursor.fetchone()
                if row:
                    break
            else:
                app.logger.error("Could not generate a unique classroom join code")
                return jsonify({"error": "Could not create a class right now. Please try again."}), 503
    return jsonify({
        "classroom": {
            "id": row[0], "name": row[1], "description": row[2],
            "join_code": row[3], "student_count": 0,
        }
    }), 201


@app.route("/api/classrooms/join", methods=["POST"])
@login_required
def join_classroom():
    user_id, error = require_database_user()
    if error:
        return error
    role_error = require_role("student")
    if role_error:
        return role_error
    data = request.get_json(silent=True) or {}
    code = data.get("join_code", "").strip().upper() if isinstance(data.get("join_code"), str) else ""
    if not re.fullmatch(r"[A-Z0-9]{6,8}", code):
        return jsonify({"error": "Enter a valid 6–8 character class code."}), 400
    initialize_database()
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM classrooms WHERE join_code = %s", (code,))
            classroom = cursor.fetchone()
            if not classroom:
                return jsonify({"error": "No class was found with that code."}), 404
            cursor.execute(
                """
                INSERT INTO classroom_members (classroom_id, student_id)
                VALUES (%s, %s) ON CONFLICT DO NOTHING
                """,
                (classroom[0], user_id),
            )
            cursor.execute(
                "SELECT id, name, description FROM classrooms WHERE id = %s",
                (classroom[0],),
            )
            row = cursor.fetchone()
    return jsonify({"classroom": {
        "id": row[0], "name": row[1], "description": row[2], "join_code": None,
    }})


@app.route("/api/classrooms/<int:classroom_id>/assignments", methods=["GET", "POST"])
@login_required
def classroom_assignments_api(classroom_id):
    user_id, error = require_database_user()
    if error:
        return error
    role = session.get("role")
    role_error = require_role("instructor" if request.method == "POST" else role)
    if role_error:
        return role_error
    initialize_database()
    with get_connection() as connection:
        with connection.cursor() as cursor:
            if not classroom_access(cursor, classroom_id, user_id, role):
                return jsonify({"error": "Class not found or you do not have access."}), 404

            if request.method == "GET":
                cursor.execute(
                    """
                    SELECT a.id, a.title, a.instructions, a.due_date,
                           a.attachment_name,
                           COUNT(DISTINCT s.id) AS submission_count,
                           mine.id AS my_submission_id,
                           mine.filename AS my_filename,
                           mine.submitted_at AS my_submitted_at
                    FROM classroom_assignments a
                    LEFT JOIN classroom_members m ON m.classroom_id = a.classroom_id
                    LEFT JOIN classroom_submissions s ON s.assignment_id = a.id
                    LEFT JOIN classroom_submissions mine ON mine.assignment_id = a.id
                        AND mine.student_id = %s
                    WHERE a.classroom_id = %s
                    GROUP BY a.id, mine.id, mine.filename, mine.submitted_at
                    ORDER BY a.due_date, a.id
                    """,
                    (user_id, classroom_id),
                )
                rows = cursor.fetchall()
                return jsonify({"assignments": [
                    {
                        "id": row[0], "title": row[1], "instructions": row[2],
                        "due_date": row[3].isoformat(), "attachment_name": row[4],
                        "submission_count": row[5], "my_submission_id": row[6],
                        "my_filename": row[7],
                        "my_submitted_at": row[8].isoformat() if row[8] else None,
                    }
                    for row in rows
                ]})

            title = request.form.get("title", "").strip()
            instructions = request.form.get("instructions", "").strip()
            due_value = request.form.get("due_date", "").strip()
            if not title or len(title) > 200 or len(instructions) > 5000:
                return jsonify({"error": "Enter a title (up to 200 characters) and instructions (up to 5000 characters)."}), 400
            try:
                due_date = date.fromisoformat(due_value)
            except ValueError:
                return jsonify({"error": "Choose a valid due date."}), 400
            filename, content_type, file_data, upload_error = upload_data(request.files.get("file"))
            if upload_error:
                return upload_error
            cursor.execute(
                """
                INSERT INTO classroom_assignments
                    (classroom_id, instructor_id, title, instructions, due_date,
                     attachment_name, attachment_type, attachment_data)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, title, instructions, due_date, attachment_name
                """,
                (classroom_id, user_id, title, instructions, due_date,
                 filename, content_type, file_data),
            )
            row = cursor.fetchone()
    return jsonify({"assignment": {
        "id": row[0], "title": row[1], "instructions": row[2],
        "due_date": row[3].isoformat(), "attachment_name": row[4],
        "submission_count": 0, "my_submission_id": None,
        "my_filename": None, "my_submitted_at": None,
    }}), 201


@app.route("/api/classroom-assignments/<int:assignment_id>/submit", methods=["POST"])
@login_required
def submit_classroom_assignment(assignment_id):
    user_id, error = require_database_user()
    if error:
        return error
    role_error = require_role("student")
    if role_error:
        return role_error
    filename, content_type, file_data, upload_error = upload_data(request.files.get("file"))
    if upload_error:
        return upload_error
    if not filename:
        return jsonify({"error": "Choose a file to submit."}), 400
    initialize_database()
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO classroom_submissions
                    (assignment_id, student_id, filename, content_type, file_data)
                SELECT a.id, %s, %s, %s, %s
                FROM classroom_assignments a
                JOIN classroom_members m ON m.classroom_id = a.classroom_id
                    AND m.student_id = %s
                WHERE a.id = %s
                ON CONFLICT (assignment_id, student_id) DO UPDATE SET
                    filename = EXCLUDED.filename,
                    content_type = EXCLUDED.content_type,
                    file_data = EXCLUDED.file_data,
                    submitted_at = CURRENT_TIMESTAMP
                RETURNING id
                """,
                (user_id, filename, content_type, file_data, user_id, assignment_id),
            )
            if not cursor.fetchone():
                return jsonify({"error": "Assignment not found or you are not enrolled in its class."}), 404
    return jsonify({"success": True, "filename": filename})


@app.route("/api/classroom-assignments/<int:assignment_id>/submissions")
@login_required
def classroom_submission_list(assignment_id):
    user_id, error = require_database_user()
    if error:
        return error
    role_error = require_role("instructor")
    if role_error:
        return role_error
    initialize_database()
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT u.display_name, u.username, s.filename, s.submitted_at,
                       s.id, a.classroom_id
                FROM classroom_assignments a
                JOIN classroom_submissions s ON s.assignment_id = a.id
                JOIN users u ON u.id = s.student_id
                WHERE a.id = %s AND a.instructor_id = %s
                ORDER BY s.submitted_at
                """,
                (assignment_id, user_id),
            )
            rows = cursor.fetchall()
            if not rows:
                cursor.execute(
                    "SELECT 1 FROM classroom_assignments WHERE id = %s AND instructor_id = %s",
                    (assignment_id, user_id),
                )
                if not cursor.fetchone():
                    return jsonify({"error": "Assignment not found."}), 404
    return jsonify({"submissions": [
        {
            "student_name": row[0], "username": row[1], "filename": row[2],
            "submitted_at": row[3].isoformat(), "id": row[4],
        }
        for row in rows
    ]})


@app.route("/api/classroom-files/<kind>/<int:file_id>")
@login_required
def classroom_file_download(kind, file_id):
    user_id, error = require_database_user()
    if error:
        return error
    role = session.get("role")
    initialize_database()
    with get_connection() as connection:
        with connection.cursor() as cursor:
            if kind == "assignment":
                cursor.execute(
                    """
                    SELECT a.attachment_name, a.attachment_data
                    FROM classroom_assignments a
                    LEFT JOIN classroom_members m ON m.classroom_id = a.classroom_id
                        AND m.student_id = %s
                    WHERE a.id = %s
                      AND (a.instructor_id = %s OR m.student_id = %s)
                      AND a.attachment_data IS NOT NULL
                    """,
                    (user_id, file_id, user_id, user_id),
                )
            elif kind == "submission" and role == "instructor":
                cursor.execute(
                    """
                    SELECT s.filename, s.file_data
                    FROM classroom_submissions s
                    JOIN classroom_assignments a ON a.id = s.assignment_id
                    WHERE s.id = %s AND a.instructor_id = %s
                    """,
                    (file_id, user_id),
                )
            elif kind == "submission":
                cursor.execute(
                    """
                    SELECT filename, file_data FROM classroom_submissions
                    WHERE id = %s AND student_id = %s
                    """,
                    (file_id, user_id),
                )
            else:
                return jsonify({"error": "File not found."}), 404
            row = cursor.fetchone()
    if not row:
        return jsonify({"error": "File not found."}), 404
    response = make_response(row[1])
    response.headers["Content-Type"] = "application/octet-stream"
    response.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(row[0])}"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def require_database_user():
    user_id = session.get("user_id")
    if not DATABASE_URL or not user_id:
        return None, (jsonify({
            "error": "Assignment storage is unavailable for this account. Please create an account and configure PostgreSQL."
        }), 503)
    return user_id, None


@app.route("/api/assignments", methods=["GET", "POST"])
@login_required
def assignments_api():
    user_id, error = require_database_user()
    if error:
        return error

    if request.method == "GET":
        initialize_database()
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, title, due_date, completed, filename
                    FROM student_assignments
                    WHERE user_id = %s
                    ORDER BY completed, due_date, id
                    """,
                    (user_id,),
                )
                rows = cursor.fetchall()
        return jsonify({"assignments": [assignment_payload(row) for row in rows]})

    title = request.form.get("title", "").strip()
    due_date = request.form.get("due_date", "").strip()
    if not title or len(title) > 200:
        return jsonify({"error": "Add a task title up to 200 characters long."}), 400
    try:
        parsed_due_date = date.fromisoformat(due_date)
    except ValueError:
        return jsonify({"error": "Choose a valid due date."}), 400

    upload = request.files.get("file")
    filename = None
    content_type = None
    file_data = None
    if upload and upload.filename:
        filename = secure_filename(upload.filename)
        extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if not filename or extension not in ALLOWED_UPLOAD_EXTENSIONS:
            return jsonify({"error": "That file type is not supported. Upload a PDF, presentation, document, spreadsheet, image, text file, or ZIP."}), 400
        file_data = upload.read(15 * 1024 * 1024 + 1)
        if len(file_data) > 15 * 1024 * 1024:
            return jsonify({"error": "Files must be 15 MB or smaller."}), 413
        content_type = upload.mimetype or "application/octet-stream"

    initialize_database()
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO student_assignments
                    (user_id, title, due_date, filename, content_type, file_data)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, title, due_date, completed, filename
                """,
                (user_id, title, parsed_due_date, filename, content_type, file_data),
            )
            row = cursor.fetchone()
    return jsonify({"assignment": assignment_payload(row)}), 201


@app.route("/api/assignments/<int:assignment_id>/complete", methods=["POST"])
@login_required
def complete_assignment(assignment_id):
    user_id, error = require_database_user()
    if error:
        return error
    initialize_database()
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE student_assignments SET completed = TRUE
                WHERE id = %s AND user_id = %s
                RETURNING id
                """,
                (assignment_id, user_id),
            )
            if not cursor.fetchone():
                return jsonify({"error": "Task not found."}), 404
    return jsonify({"success": True})


@app.route("/api/assignments/<int:assignment_id>/delete", methods=["POST"])
@login_required
def delete_assignment(assignment_id):
    user_id, error = require_database_user()
    if error:
        return error
    initialize_database()
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM student_assignments WHERE id = %s AND user_id = %s",
                (assignment_id, user_id),
            )
            if cursor.rowcount == 0:
                return jsonify({"error": "Task not found."}), 404
    return jsonify({"success": True})


@app.route("/api/assignments/<int:assignment_id>/download")
@login_required
def download_assignment(assignment_id):
    user_id, error = require_database_user()
    if error:
        return error
    initialize_database()
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT filename, content_type, file_data
                FROM student_assignments
                WHERE id = %s AND user_id = %s AND file_data IS NOT NULL
                """,
                (assignment_id, user_id),
            )
            row = cursor.fetchone()
    if not row:
        return jsonify({"error": "Attachment not found."}), 404
    response = make_response(row[2])
    response.headers["Content-Type"] = "application/octet-stream"
    response.headers["Content-Disposition"] = (
        f"attachment; filename*=UTF-8''{quote(row[0])}"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.route("/api/chat", methods=["POST"])
@login_required
def chat():
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return jsonify({"error": "The AI study buddy is not configured yet. Add OPENAI_API_KEY to the app environment."}), 503
    user_id, error = require_database_user()
    if error:
        return error

    data = request.get_json(silent=True)
    messages = data.get("messages") if isinstance(data, dict) else None
    if not isinstance(messages, list) or not messages or len(messages) > 12:
        return jsonify({"error": "Send between 1 and 12 chat messages."}), 400

    safe_messages = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}:
            return jsonify({"error": "The chat history contains an invalid message."}), 400
        content = message.get("content")
        if not isinstance(content, str) or not content.strip() or len(content) > 3000:
            return jsonify({"error": "Each chat message must contain 1–3000 characters."}), 400
        safe_messages.append({"role": message["role"], "content": content.strip()})
    if safe_messages[-1]["role"] != "user":
        return jsonify({"error": "Your latest chat message must be from you."}), 400

    try:
        initialize_database()
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO ai_chat_usage (user_id, usage_date, request_count)
                    VALUES (%s, CURRENT_DATE, 1)
                    ON CONFLICT (user_id) DO UPDATE SET
                        usage_date = CURRENT_DATE,
                        request_count = CASE
                            WHEN ai_chat_usage.usage_date < CURRENT_DATE THEN 1
                            ELSE ai_chat_usage.request_count + 1
                        END
                    WHERE ai_chat_usage.usage_date < CURRENT_DATE
                       OR ai_chat_usage.request_count < %s
                    RETURNING request_count
                    """,
                    (user_id, AI_DAILY_REQUEST_LIMIT),
                )
                usage = cursor.fetchone()
    except psycopg.Error:
        app.logger.exception("Could not record StudyVerse AI usage")
        return jsonify({"error": "The AI study buddy is temporarily unavailable. Please try again."}), 503
    if not usage:
        return jsonify({"error": "You’ve reached today’s AI study buddy limit. Please come back tomorrow."}), 429

    payload = {
        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are StudyVerse Study Buddy, a supportive learning assistant. "
                    "Explain concepts clearly, encourage independent thinking, and help "
                    "students learn rather than complete graded work dishonestly. Keep "
                    "answers focused and age-appropriate."
                ),
            },
            *safe_messages,
        ],
        "max_tokens": 700,
    }
    api_request = Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(api_request, timeout=25) as response:
            result = json.loads(response.read())
        reply = result["choices"][0]["message"]["content"]
        if not isinstance(reply, str) or not reply.strip():
            raise ValueError("AI response did not contain a message")
    except HTTPError as error:
        app.logger.warning("OpenAI request failed with HTTP status %s", error.code)
        return jsonify({"error": "The AI study buddy could not respond right now. Please try again."}), 502
    except (URLError, TimeoutError, json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError):
        app.logger.exception("Could not get a valid response from OpenAI")
        return jsonify({"error": "The AI study buddy could not respond right now. Please try again."}), 502
    return jsonify({"reply": reply.strip()})


@app.route("/api/recommendation", methods=["POST"])
@login_required
def recommendation():
    data = request.get_json(silent=True) or {}
    progress = data.get("progress", [])

    if not isinstance(progress, list):
        return jsonify({"error": "Progress must be a list"}), 400

    valid = [
        item for item in progress
        if isinstance(item, dict)
        and isinstance(item.get("progress"), (int, float))
        and not isinstance(item.get("progress"), bool)
        and 0 <= item["progress"] <= 100
        and isinstance(item.get("name"), str)
    ]

    if not valid:
        return jsonify({
            "recommendation":
                "Start with one course and record your progress."
        })

    weakest = min(valid, key=lambda item: item["progress"])

    return jsonify({
        "recommendation":
            f"Focus on {weakest['name']}. "
            "Study for 25 minutes and practise related questions."
    })


if __name__ == "__main__":
    app.run(
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "5000")),
        debug=not IS_PRODUCTION,
    )
