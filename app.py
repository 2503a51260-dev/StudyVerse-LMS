import os, secrets, smtplib, logging
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from functools import wraps
import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")
if not app.secret_key:
    raise RuntimeError("Set FLASK_SECRET_KEY in .env or hosting environment variables.")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "false").lower() == "true",
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
)
csrf = CSRFProtect(app)
DB = os.getenv("DATABASE_URL", "")
OTP_TTL = 10
OTP_MAX_TRIES = 5
OTP_COOLDOWN = 60

def db():
    if not DB:
        raise RuntimeError("DATABASE_URL is missing.")
    return psycopg.connect(DB, row_factory=dict_row)

def init_db():
    # schema.sql is also included; these statements allow first-run setup automatically.
    sql = open("schema.sql", encoding="utf-8").read()
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)

def send_email(to_email, code):
    address, app_password = os.getenv("GMAIL_ADDRESS"), os.getenv("GMAIL_APP_PASSWORD")
    if not address or not app_password:
        raise RuntimeError("Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD.")
    msg = EmailMessage()
    msg["Subject"] = "Your StudyVerse LMS password reset code"
    msg["From"] = f"StudyVerse LMS <{address}>"
    msg["To"] = to_email
    msg.set_content(f"Your StudyVerse password reset code is {code}.\nIt expires in {OTP_TTL} minutes and can be used once. Do not share it. If you did not request this, ignore this email.")
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(address, app_password)
        server.send_message(msg)

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("student_id"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper

@app.context_processor
def context():
    return {"current_student_name": session.get("student_name", "")}

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if session.get("student_id"): return redirect(url_for("home"))
    if request.method == "POST":
        name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if len(name) < 2 or len(name) > 100:
            flash("Enter your name (2–100 characters).", "error")
        elif len(email) > 254 or "@" not in email or "." not in email.split("@")[-1]:
            flash("Enter a valid email address.", "error")
        elif len(password) < 12:
            flash("Use a password with at least 12 characters.", "error")
        else:
            try:
                with db() as conn:
                    with conn.cursor() as cur:
                        cur.execute("INSERT INTO students(full_name,email,password_hash) VALUES(%s,%s,%s) RETURNING id",
                                    (name,email,generate_password_hash(password)))
                        sid = cur.fetchone()["id"]
                session.clear(); session.permanent = True
                session["student_id"], session["student_name"] = sid, name
                return redirect(url_for("home"))
            except psycopg.errors.UniqueViolation:
                flash("If you already have an account, sign in instead. Otherwise check the email and try again.", "error")
            except Exception:
                log.exception("Signup failed")
                flash("Could not create account right now. Try again later.", "error")
    return render_template("signup.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("student_id"): return redirect(url_for("home"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        try:
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT id,full_name,password_hash FROM students WHERE email=%s",(email,))
                    user = cur.fetchone()
            if user and check_password_hash(user["password_hash"], password):
                session.clear(); session.permanent = True
                session["student_id"], session["student_name"] = user["id"], user["full_name"]
                return redirect(url_for("home"))
            flash("Incorrect email or password. Please try again.", "error")
        except Exception:
            log.exception("Login failed")
            flash("Sign-in is temporarily unavailable. Try again later.", "error")
    return render_template("login.html")

@app.post("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        generic = "If an account matches that email, a reset code will be sent when allowed. Check inbox and spam."
        try:
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT id FROM students WHERE email=%s", (email,))
                    user = cur.fetchone()
                    code = None
                    if user:
                        cur.execute("SELECT last_sent_at FROM password_reset_otps WHERE student_id=%s ORDER BY created_at DESC LIMIT 1",(user["id"],))
                        previous = cur.fetchone()
                        now = datetime.now(timezone.utc)
                        if previous and (now - previous["last_sent_at"]).total_seconds() < OTP_COOLDOWN:
                            flash(generic, "success")
                            return render_template("forgot_password.html")
                        code = f"{secrets.randbelow(1000000):06d}"
                        cur.execute("DELETE FROM password_reset_otps WHERE student_id=%s",(user["id"],))
                        cur.execute("INSERT INTO password_reset_otps(student_id,otp_hash,expires_at,attempts,last_sent_at) VALUES(%s,%s,%s,0,%s)",
                                    (user["id"],generate_password_hash(code),now+timedelta(minutes=OTP_TTL),now))
            if user and code:
                try:
                    send_email(email, code)
                except Exception:
                    log.exception("Could not send reset email")
                    with db() as conn:
                        with conn.cursor() as cur:
                            cur.execute("DELETE FROM password_reset_otps WHERE student_id=%s",(user["id"],))
            flash(generic, "success")
        except Exception:
            log.exception("Forgot password failed")
            flash("Could not process the request right now. Try again later.", "error")
    return render_template("forgot_password.html")

@app.route("/verify-otp", methods=["GET", "POST"])
def verify_otp():
    email = request.form.get("email", "").strip().lower() if request.method == "POST" else request.args.get("email", "")
    if request.method == "POST":
        code = request.form.get("otp", "").strip()
        if len(code) != 6 or not code.isdigit():
            flash("Enter the 6-digit code.", "error")
            return render_template("verify_otp.html", email=email)
        try:
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute("""SELECT r.id AS rid,r.otp_hash,r.expires_at,r.attempts,s.id AS sid
                      FROM password_reset_otps r JOIN students s ON s.id=r.student_id
                      WHERE s.email=%s ORDER BY r.created_at DESC LIMIT 1""",(email,))
                    row = cur.fetchone()
                    if not row or row["expires_at"] <= datetime.now(timezone.utc) or row["attempts"] >= OTP_MAX_TRIES:
                        if row: cur.execute("DELETE FROM password_reset_otps WHERE id=%s",(row["rid"],))
                        flash("The code is invalid or expired. Request a new code.", "error")
                        return render_template("verify_otp.html", email=email)
                    if not check_password_hash(row["otp_hash"], code):
                        cur.execute("UPDATE password_reset_otps SET attempts=attempts+1 WHERE id=%s",(row["rid"],))
                        flash("The code is invalid or expired. Request a new code if needed.", "error")
                        return render_template("verify_otp.html", email=email)
                    cur.execute("DELETE FROM password_reset_otps WHERE id=%s",(row["rid"],))
            session["reset_student_id"] = row["sid"]
            session["reset_verified_at"] = int(datetime.now(timezone.utc).timestamp())
            return redirect(url_for("reset_password"))
        except Exception:
            log.exception("OTP verification failed")
            flash("Could not verify the code right now. Try again.", "error")
    return render_template("verify_otp.html", email=email)

@app.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    sid, verified = session.get("reset_student_id"), session.get("reset_verified_at")
    now = int(datetime.now(timezone.utc).timestamp())
    if not sid or not verified or now - verified > 600:
        session.pop("reset_student_id", None); session.pop("reset_verified_at", None)
        flash("Please request and verify a new reset code.", "error")
        return redirect(url_for("forgot_password"))
    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if len(password) < 12: flash("Use a password with at least 12 characters.", "error")
        elif password != confirm: flash("The passwords do not match.", "error")
        else:
            try:
                with db() as conn:
                    with conn.cursor() as cur:
                        cur.execute("UPDATE students SET password_hash=%s WHERE id=%s",(generate_password_hash(password),sid))
                        cur.execute("DELETE FROM password_reset_otps WHERE student_id=%s",(sid,))
                session.clear()
                flash("Password updated. Sign in with your new password.", "success")
                return redirect(url_for("login"))
            except Exception:
                log.exception("Password update failed")
                flash("Could not update the password. Try again.", "error")
    return render_template("reset_password.html")

@app.route("/")
@login_required
def home():
    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id,title,description,instructor FROM courses ORDER BY id")
                courses = cur.fetchall()
                cur.execute("SELECT id,title,description,due_date FROM assignments ORDER BY due_date NULLS LAST,id LIMIT 10")
                assignments = cur.fetchall()
        return render_template("index.html", courses=courses, assignments=assignments)
    except Exception:
        log.exception("Dashboard load failed")
        return render_template("index.html", courses=[], assignments=[])

@app.get("/api/health")
@csrf.exempt
def health():
    return jsonify({"status":"running","project":"StudyVerse LMS"})

@app.get("/api/courses")
@login_required
def api_courses():
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id,title,description,instructor FROM courses ORDER BY id")
            return jsonify(cur.fetchall())

if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=int(os.getenv("PORT","5000")), debug=False)
