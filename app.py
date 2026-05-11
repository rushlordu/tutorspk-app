import os
import re
import smtplib
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit
from dotenv import load_dotenv
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from rtc.extensions import db
from rtc import rtc_bp
import calendar
import random 

from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
    session,
)
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from flask_sqlalchemy import SQLAlchemy
from rtc.extensions import db
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from authlib.integrations.flask_client import OAuth

load_dotenv()

def get_youtube_embed(url):
    if not url:
        return ""

    # youtu.be/xxxxx
    if "youtu.be/" in url:
        return url.replace("youtu.be/", "www.youtube.com/embed/")

    # youtube.com/watch?v=xxxxx
    if "watch?v=" in url:
        return url.replace("watch?v=", "embed/")

    return url

def hhmm_to_minutes(value: str) -> int:
    try:
        hours, minutes = value.split(":")
        return int(hours) * 60 + int(minutes)
    except Exception:
        return 0


def minutes_to_hhmm(total_minutes: int) -> str:
    hours = total_minutes // 60
    minutes = total_minutes % 60
    return f"{hours:02d}:{minutes:02d}"

def build_slots_for_date(tutor, target_date):
    weekday = target_date.weekday()

    rules = (
        TutorAvailabilityRule.query
        .filter_by(tutor_id=tutor.id, weekday=weekday, is_active=True)
        .order_by(TutorAvailabilityRule.start_time.asc())
        .all()
    )

    exceptions = (
        TutorAvailabilityException.query
        .filter_by(tutor_id=tutor.id, exception_date=target_date)
        .order_by(TutorAvailabilityException.start_time.asc())
        .all()
    )

    # If a full-day blocked exception exists, nothing should be available.
    for ex in exceptions:
        if ex.is_blocked and not (ex.start_time or "").strip() and not (ex.end_time or "").strip():
            return []

    slots = []

    def add_slot(start_hhmm: str, end_hhmm: str):
        slot = {
            "start": start_hhmm,
            "end": end_hhmm,
            "datetime_local": f"{target_date.isoformat()}T{start_hhmm}",
            "label": f"{start_hhmm} - {end_hhmm}",
        }
        if not any(s["start"] == slot["start"] and s["end"] == slot["end"] for s in slots):
            slots.append(slot)

    # 1) Build normal recurring weekly slots
    for rule in rules:
        start_minutes = hhmm_to_minutes(rule.start_time)
        end_minutes = hhmm_to_minutes(rule.end_time)
        slot_minutes = int(rule.slot_minutes or 60)

        current = start_minutes
        while current + slot_minutes <= end_minutes:
            slot_start = minutes_to_hhmm(current)
            slot_end = minutes_to_hhmm(current + slot_minutes)

            blocked = False
            for ex in exceptions:
                if not ex.is_blocked:
                    continue

                ex_start = hhmm_to_minutes((ex.start_time or "").strip() or "00:00")
                ex_end = hhmm_to_minutes((ex.end_time or "").strip() or "23:59")
                slot_start_m = hhmm_to_minutes(slot_start)
                slot_end_m = hhmm_to_minutes(slot_end)

                if slot_start_m < ex_end and slot_end_m > ex_start:
                    blocked = True
                    break

            if not blocked:
                add_slot(slot_start, slot_end)

            current += slot_minutes

    # 2) Add one-off custom available slots from exceptions where is_blocked = False
    for ex in exceptions:
        if ex.is_blocked:
            continue

        ex_start_raw = (ex.start_time or "").strip()
        ex_end_raw = (ex.end_time or "").strip()
        if not ex_start_raw or not ex_end_raw:
            continue

        ex_start = hhmm_to_minutes(ex_start_raw)
        ex_end = hhmm_to_minutes(ex_end_raw)
        if ex_end <= ex_start:
            continue

        # Use same slot size as the first rule for that weekday if available, else 60 mins
        slot_minutes = int(rules[0].slot_minutes if rules else 60)
        current = ex_start

        while current + slot_minutes <= ex_end:
            slot_start = minutes_to_hhmm(current)
            slot_end = minutes_to_hhmm(current + slot_minutes)
            add_slot(slot_start, slot_end)
            current += slot_minutes

    slots.sort(key=lambda s: s["start"])
    return slots

def get_tutor_schedule_context(tutor):
    today = date.today()

    selected_date_str = request.args.get("selected_date", "").strip()
    week_start_str = request.args.get("week_start", "").strip()

    anchor_date = today
    if week_start_str:
        try:
            anchor_date = datetime.strptime(week_start_str, "%Y-%m-%d").date()
        except ValueError:
            anchor_date = today
    elif selected_date_str:
        try:
            anchor_date = datetime.strptime(selected_date_str, "%Y-%m-%d").date()
        except ValueError:
            anchor_date = today

    week_start, week_days = build_week_schedule_for_tutor(tutor, anchor_date)
    week_end = week_start + timedelta(days=6)

    selected_date = None
    selected_slots = []

    if selected_date_str:
        try:
            selected_date = datetime.strptime(selected_date_str, "%Y-%m-%d").date()
            selected_slots = build_slots_for_date(tutor, selected_date)
        except ValueError:
            selected_date = None
            selected_slots = []

    if not selected_date:
        first_day_with_slots = next((d for d in week_days if d["has_slots"]), None)
        if first_day_with_slots:
            selected_date = first_day_with_slots["date"]
            selected_slots = first_day_with_slots["slots"]

    prev_week_start = week_start - timedelta(days=7)
    next_week_start = week_start + timedelta(days=7)

    if week_start.month == week_end.month:
        schedule_range_label = f"{calendar.month_abbr[week_start.month]} {week_start.day}–{week_end.day}, {week_end.year}"
    else:
        schedule_range_label = (
            f"{calendar.month_abbr[week_start.month]} {week_start.day} – "
            f"{calendar.month_abbr[week_end.month]} {week_end.day}, {week_end.year}"
        )

    return {
        "week_days": week_days,
        "week_start": week_start,
        "prev_week_start": prev_week_start,
        "next_week_start": next_week_start,
        "schedule_range_label": schedule_range_label,
        "selected_date": selected_date,
        "selected_slots": selected_slots,
    }


def is_htmx_like_request():
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"

def build_calendar_for_tutor(tutor, year: int, month: int):
    cal = calendar.Calendar(firstweekday=0)
    weeks = []
    month_matrix = cal.monthdatescalendar(year, month)

    for week in month_matrix:
        week_cells = []
        for day_obj in week:
            in_month = day_obj.month == month
            day_slots = build_slots_for_date(tutor, day_obj) if in_month else []
            week_cells.append({
                "date": day_obj,
                "day": day_obj.day,
                "in_month": in_month,
                "slots": day_slots,
                "slot_count": len(day_slots),
                "has_slots": len(day_slots) > 0,
                "timing_preview": ", ".join(
                    f"{slot['start']}" for slot in day_slots[:2]
                ) + ("..." if len(day_slots) > 2 else "")
            })
        weeks.append(week_cells)

    return weeks

def build_week_schedule_for_tutor(tutor, anchor_date):
    week_start = anchor_date - timedelta(days=anchor_date.weekday())
    week_days = []

    for offset in range(7):
        day_obj = week_start + timedelta(days=offset)
        slots = build_slots_for_date(tutor, day_obj)
        week_days.append({
            "date": day_obj,
            "day_name": calendar.day_abbr[day_obj.weekday()],
            "day_number": day_obj.day,
            "slots": slots,
            "has_slots": len(slots) > 0,
        })

    return week_start, week_days

def booking_time_window_status(booking, early_minutes=10, late_minutes=10):
    now = datetime.now()
    start = booking.scheduled_at
    end = booking.scheduled_at + timedelta(minutes=booking.duration_minutes or 60)

    allowed_start = start - timedelta(minutes=early_minutes)
    allowed_end = end + timedelta(minutes=late_minutes)

    if now < allowed_start:
        return "too_early", allowed_start, allowed_end

    if now > allowed_end:
        return "expired", allowed_start, allowed_end

    return "open", allowed_start, allowed_end

def get_next_available_slot(tutor, days_ahead=14):
    """
    Returns:
      {
        "date": date_obj,
        "slot": slot_dict,
        "label": "Today 06:00 PM"
      }
    or None
    """
    now = datetime.now()

    for offset in range(days_ahead + 1):
        target_date = date.today() + timedelta(days=offset)
        slots = build_slots_for_date(tutor, target_date)

        for slot in slots:
            try:
                slot_dt = datetime.strptime(
                    f"{target_date.isoformat()} {slot['start']}",
                    "%Y-%m-%d %H:%M"
                )
            except Exception:
                continue

            if slot_dt <= now:
                continue

            if offset == 0:
                day_label = "Today"
            elif offset == 1:
                day_label = "Tomorrow"
            else:
                day_label = target_date.strftime("%a %d %b")

            return {
                "date": target_date,
                "slot": slot,
                "label": f"{day_label} · {slot_dt.strftime('%I:%M %p')}",
            }

    return None

def send_email(to_email, subject, body, is_html=False, reply_to=None):
    try:
        import smtplib
        import os
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        smtp_server = os.getenv("SMTP_SERVER")
        smtp_port = int(os.getenv("SMTP_PORT", 587))
        username = os.getenv("SMTP_USERNAME")
        password = os.getenv("SMTP_PASSWORD")
        from_email = os.getenv("FROM_EMAIL")

        if isinstance(to_email, str):
            recipients = [to_email]
        else:
            recipients = to_email

        msg = MIMEMultipart()
        msg["From"] = from_email
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = subject

        if reply_to:
            msg["Reply-To"] = reply_to

        msg.attach(MIMEText(body, "html" if is_html else "plain"))

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(username, password)
            server.send_message(msg)

        print(f"✅ Email sent to {recipients}")
        return True

    except Exception as e:
        print("❌ Email failed:", str(e))
        return False

def safe_send_email(to_email, subject, body, is_html=False, reply_to=None):
    try:
        return send_email(
            to_email=to_email,
            subject=subject,
            body=body,
            is_html=is_html,
            reply_to=reply_to,
        )
    except Exception as e:
        print("❌ safe_send_email failed:", str(e))
        return False


def send_signup_emails(user):
    # Admin notification
    safe_send_email(
        "superadmin@tutorsonline.pk",
        f"New TutorsOnline.pk {user.role.title()} Registration",
        f"""A new account has been created.

Role: {user.role.title()}
Name: {user.full_name}
Public Name: {user.public_name}
Email: {user.email}
City: {user.city or '-'}
Gender: {user.gender or '-'}

Please review this account in admin panel if needed.
"""
    )

    # User confirmation
    if user.role == "tutor":
        user_subject = "Tutor application received - TutorsOnline.pk"
        user_body = f"""Assalam-o-Alaikum,

Thank you for joining TutorsOnline.pk as a tutor.

We have received your profile and it is now under review.

Account details:
Name: {user.full_name}
Email: {user.email}

You will receive another email once your tutor profile is approved or if it remains pending review.

Regards,
TutorsOnline.pk
superadmin@tutorsonline.pk
"""
    else:
        user_subject = "Welcome to TutorsOnline.pk"
        user_body = f"""Assalam-o-Alaikum,

Welcome to TutorsOnline.pk.

Your student account has been created successfully.

Account details:
Name: {user.full_name}
Email: {user.email}

You can now log in and start exploring tutors.

Regards,
TutorsOnline.pk
superadmin@tutorsonline.pk
"""

    safe_send_email(user.email, user_subject, user_body)


def send_tutor_review_email(user, action, reason=""):
    if user.role != "tutor":
        return

    if action == "request_fee":
        subject = "Tutor selected - registration fee required - TutorsOnline.pk"
        body = f"""Assalam-o-Alaikum {user.full_name},

Thank you for applying as a tutor on TutorsOnline.pk.

Your profile has been selected in principle, and the next step is the final registration payment of PKR 500.

Please send the payment as instructed by the platform team. Once payment is confirmed, your tutor profile will be activated.

{f"Note: {reason}" if reason else ""}

Regards,
TutorsOnline.pk
superadmin@tutorsonline.pk
"""

    elif action == "activate":
        subject = "Your tutor profile is now active - TutorsOnline.pk"
        body = f"""Assalam-o-Alaikum {user.full_name},

Your tutor registration has been completed successfully.

Your profile is now active on TutorsOnline.pk and can be shown to students.

Regards,
TutorsOnline.pk
superadmin@tutorsonline.pk
"""

    elif action == "reject":
        subject = "Tutor profile review update - TutorsOnline.pk"
        body = f"""Assalam-o-Alaikum {user.full_name},

Your tutor application could not be approved at this time.

{f"Reason: {reason}" if reason else "Please review your submitted details and contact us if needed."}

Regards,
TutorsOnline.pk
superadmin@tutorsonline.pk
"""

    elif action == "pend":
        subject = "Tutor profile pending review - TutorsOnline.pk"
        body = f"""Assalam-o-Alaikum {user.full_name},

Your tutor profile is still under review.

{f"Note: {reason}" if reason else "We will notify you once the review is complete."}

Regards,
TutorsOnline.pk
superadmin@tutorsonline.pk
"""
    else:
        return

    safe_send_email(user.email, subject, body)

def get_tutor_completion_status(user):
    missing = tutor_missing_requirements_from_user(user)
    total_items = 14
    highest = (user.qualification or "").strip().lower()
    next_choice = (getattr(user, "previous_path_choice", "") or "").strip().lower()
    if highest == "phd" and next_choice in {"mphil", "masters"}:
        total_items += 8
    elif highest == "mphil":
        total_items += 8
    elif highest == "masters":
        total_items += 4
    completion = max(0, int(round(((total_items - len(missing)) / total_items) * 100))) if total_items else 0
    stage = compute_tutor_profile_stage(user)
    return {
        "missing": missing,
        "completion": min(100, completion),
        "stage": stage,
        "stage_meta": get_stage_badge(stage),
    }

def send_booking_emails(booking):
    student = booking.student
    tutor = booking.tutor
    when = booking.scheduled_at.strftime("%d %b %Y %I:%M %p") if booking.scheduled_at else "-"

    student_subject = "Booking confirmed - TutorsOnline.pk"
    student_body = f"""Assalam-o-Alaikum {student.full_name},

Your session has been booked successfully.

Tutor: {tutor.public_name}
Subject: {booking.subject}
Level: {booking.class_level}
Date & Time: {when}
Duration: {booking.duration_minutes} minutes
Credits Used: {booking.credits_cost}

Regards,
TutorsOnline.pk
superadmin@tutorsonline.pk
"""

    tutor_subject = "New student booking - TutorsOnline.pk"
    tutor_body = f"""Assalam-o-Alaikum {tutor.full_name},

You have received a new booking on TutorsOnline.pk.

Student: {student.public_name}
Subject: {booking.subject}
Level: {booking.class_level}
Date & Time: {when}
Duration: {booking.duration_minutes} minutes

Regards,
TutorsOnline.pk
superadmin@tutorsonline.pk
"""

    safe_send_email(student.email, student_subject, student_body)
    safe_send_email(tutor.email, tutor_subject, tutor_body)

BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", BASE_DIR / "uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

LOCAL_DB_PATH = (INSTANCE_DIR / "tutorpk.db").resolve().as_posix()

app = Flask(__name__)
IS_PRODUCTION = os.getenv("FLASK_ENV", "").lower() == "production" or os.getenv("APP_ENV", "").lower() == "production"

app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-change-me")
if IS_PRODUCTION and app.config["SECRET_KEY"] == "dev-secret-change-me":
    raise RuntimeError("SECRET_KEY must be set in production.")

app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
    "DATABASE_URL", f"sqlite:///{LOCAL_DB_PATH}"
)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
app.logger.info("Using DB: %s", app.config["SQLALCHEMY_DATABASE_URI"])

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

try:
    app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_UPLOAD_MB", "20")) * 1024 * 1024
except ValueError:
    app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

app.config["UPLOAD_FOLDER"] = str(UPLOAD_DIR)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = os.getenv("SESSION_COOKIE_SAMESITE", "Lax")
app.config["SESSION_COOKIE_SECURE"] = os.getenv("SESSION_COOKIE_SECURE", "true" if IS_PRODUCTION else "false").lower() == "true"
app.config["REMEMBER_COOKIE_HTTPONLY"] = True
app.config["REMEMBER_COOKIE_SECURE"] = app.config["SESSION_COOKIE_SECURE"]

PROFILE_IMAGE_FALLBACK = "images/default-avatar.svg"
MEDIA_CACHE_SECONDS = int(os.getenv("MEDIA_CACHE_SECONDS", "86400"))
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".m4v"}
ALLOWED_DOCUMENT_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}
IMAGE_EXTENSIONS = ALLOWED_IMAGE_EXTENSIONS | {".svg"}
DEV_ROUTE_ENABLED = os.getenv("ALLOW_DEV_ROUTES", "false").lower() == "true"

# Temporary site status flag
UNDER_CONSTRUCTION = os.getenv("UNDER_CONSTRUCTION", "false").lower() == "true"

# Configurable placeholders for later setup
app.config["MAIL_SERVER"] = os.getenv("MAIL_SERVER", "")
app.config["MAIL_PORT"] = int(os.getenv("MAIL_PORT", "587"))
app.config["MAIL_USE_TLS"] = os.getenv("MAIL_USE_TLS", "true").lower() == "true"
app.config["MAIL_USERNAME"] = os.getenv("MAIL_USERNAME", "")
app.config["MAIL_PASSWORD"] = os.getenv("MAIL_PASSWORD", "")
app.config["MAIL_DEFAULT_SENDER"] = os.getenv("MAIL_DEFAULT_SENDER", "")
app.config["ADMIN_NOTIFICATION_EMAIL"] = os.getenv(
    "ADMIN_NOTIFICATION_EMAIL", "jojopk44@gmail.com"
)
app.config["BANK_ACCOUNT_TITLE"] = os.getenv("BANK_ACCOUNT_TITLE", "TutorsOnline.pk")
app.config["BANK_IBAN"] = os.getenv("BANK_IBAN", "PK47ASCM0000111000196711")
app.config["BANK_NAME"] = os.getenv("BANK_NAME", "Askari Bank")
app.config["WHATSAPP_SUPPORT"] = os.getenv("WHATSAPP_SUPPORT", "+923558500230")
app.config["CREDIT_RATE"] = int(os.getenv("CREDIT_RATE", "10"))
app.config["GOOGLE_CLIENT_ID"] = os.getenv("GOOGLE_CLIENT_ID", "")
app.config["GOOGLE_CLIENT_SECRET"] = os.getenv("GOOGLE_CLIENT_SECRET", "")
app.config["GOOGLE_REDIRECT_URI"] = os.getenv("GOOGLE_REDIRECT_URI", "")

oauth = OAuth(app)

google = oauth.register(
    name='google',
    client_id=app.config["GOOGLE_CLIENT_ID"],
    client_secret=app.config["GOOGLE_CLIENT_SECRET"],
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={
        'scope': 'openid email profile'
    }
)

db.init_app(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"


def admin_required_response():
    if not current_user.is_authenticated:
        return redirect(url_for("login", next=request.url))
    if current_user.role != "admin":
        flash("Unauthorized.", "danger")
        return redirect(url_for("dashboard"))
    return None


def get_or_create_conversation(student_id, tutor_id, booking_id=None):
    query = Conversation.query.filter_by(
        student_id=student_id,
        tutor_id=tutor_id
    )

    if booking_id:
        query = query.filter_by(booking_id=booking_id)

    convo = query.first()

    if convo:
        return convo

    convo = Conversation(
        student_id=student_id,
        tutor_id=tutor_id,
        booking_id=booking_id
    )

    db.session.add(convo)
    db.session.flush()

    return convo


@app.template_filter("dt")
def format_dt(value, fmt="%d %b %Y, %I:%M %p"):
    if not value:
        return ""
    try:
        return value.strftime(fmt)
    except Exception:
        return str(value)


@app.template_filter("pkr")
def format_pkr(value):
    try:
        value = int(value or 0)
        return f"{value:,}"
    except Exception:
        return str(value or 0)
    
PHONE_OR_EMAIL_PATTERNS = [
    re.compile(r"\b\+?\d[\d\s\-]{7,}\b"),
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    re.compile(
        r"\b(whatsapp|telegram|gmail|phone|call me|dm me|contact me|instagram)\b",
        re.I,
    ),
]

SUBJECT_OPTIONS = [
    ("mathematics", "Mathematics"),
    ("physics", "Physics"),
    ("chemistry", "Chemistry"),
    ("biology", "Biology"),
    ("english", "English"),
    ("urdu", "Urdu"),
    ("computer_science", "Computer Science"),
    ("pakistan_studies", "Pakistan Studies"),
    ("islamiat", "Islamiat"),
    ("ielts", "IELTS"),
    ("spoken_english", "Spoken English"),
    ("arabic", "Arabic"),
    ("french", "French"),
    ("quran", "Quran"),
    ("computer_course", "Computer Course"),
    ("ai_courses", "AI Courses"),
    ("content_creation", "Content Creation"),
    ("other", "Other"),
]

LEVEL_OPTIONS = [
    ("grade_1_5", "Grade 1–5"),
    ("grade_6_8", "Grade 6–8"),
    ("matric", "Matric"),
    ("intermediate", "Intermediate"),
    ("o_level", "O Level"),
    ("a_level", "A Level"),
    ("university", "University"),
    ("language_learning", "Language Learning"),
    ("other", "Other"),
]


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="student")
    full_name = db.Column(db.String(120), nullable=False)
    public_name = db.Column(db.String(120), nullable=False)
    qualification = db.Column(db.String(120), default="")
    subjects = db.Column(db.String(255), default="")
    class_levels = db.Column(db.String(255), default="")
    experience_years = db.Column(db.Integer, default=0)
    bio = db.Column(db.Text, default="")
    
    degree_title = db.Column(db.String(255), default="")
    degree_major = db.Column(db.String(255), default="")
    degree_institution = db.Column(db.String(255), default="")
    degree_year = db.Column(db.String(50), default="")
    degree_grade = db.Column(db.String(50), default="")
    previous_path_choice = db.Column(db.String(50), default="")

    mphil_title = db.Column(db.String(255), default="")
    mphil_major = db.Column(db.String(255), default="")
    mphil_institution = db.Column(db.String(255), default="")
    mphil_year = db.Column(db.String(50), default="")
    mphil_grade = db.Column(db.String(50), default="")
    mphil_additional_note = db.Column(db.String(255), default="")

    masters_title = db.Column(db.String(255), default="")
    masters_major = db.Column(db.String(255), default="")
    masters_institution = db.Column(db.String(255), default="")
    masters_year = db.Column(db.String(50), default="")
    masters_grade = db.Column(db.String(50), default="")
    masters_additional_note = db.Column(db.String(255), default="")

    bachelor_title = db.Column(db.String(255), default="")
    bachelor_major = db.Column(db.String(255), default="")
    bachelor_institution = db.Column(db.String(255), default="")
    bachelor_year = db.Column(db.String(50), default="")
    bachelor_grade = db.Column(db.String(50), default="")
    bachelor_additional_note = db.Column(db.String(255), default="")

    inter_program = db.Column(db.String(100), default="")
    inter_institution = db.Column(db.String(255), default="")
    inter_grade = db.Column(db.String(50), default="")
    inter_year = db.Column(db.String(50), default="")

    matric_program = db.Column(db.String(100), default="")
    matric_institution = db.Column(db.String(255), default="")
    matric_grade = db.Column(db.String(50), default="")
    matric_year = db.Column(db.String(50), default="")

    gender = db.Column(db.String(50), default="")
    city = db.Column(db.String(120), default="")
    main_subject = db.Column(db.String(120), default="")
    additional_subjects = db.Column(db.String(255), default="")
    student_level = db.Column(db.String(120), default="")
    student_subject_needed = db.Column(db.String(120), default="")
    preferred_tutor_gender = db.Column(db.String(50), default="")
    learning_mode = db.Column(db.String(50), default="")
    teaching_mode = db.Column(db.String(50), default="")
    hourly_rate = db.Column(db.Integer, default=0)
    mobile_number = db.Column(db.String(40), default="")
    cnic_number = db.Column(db.String(40), default="")
    
    profile_image = db.Column(db.String(255), default="")
    degree_file = db.Column(db.String(255), default="")
    additional_qualification_level = db.Column(db.String(80), default="")
    additional_qualification_title = db.Column(db.String(255), default="")
    additional_qualification_major = db.Column(db.String(255), default="")
    additional_qualification_institution = db.Column(db.String(255), default="")
    additional_qualification_year = db.Column(db.String(50), default="")
    additional_qualification_grade = db.Column(db.String(50), default="")
    additional_qualification_file = db.Column(db.String(255), default="")
    demo_video_url = db.Column(db.String(255), default="")
    demo_video_file = db.Column(db.String(255), default="")
    modest_profile = db.Column(db.Boolean, default=False)
    audio_only = db.Column(db.Boolean, default=False)
    is_active_user = db.Column(db.Boolean, default=True)
    is_verified_tutor = db.Column(db.Boolean, default=False)
    profile_stage = db.Column(db.String(40), default="basic_complete")
    verification_submitted_at = db.Column(db.DateTime)
    approved_at = db.Column(db.DateTime)
    rejected_at = db.Column(db.DateTime)
    admin_review_note = db.Column(db.Text, default="")
    is_public_tutor = db.Column(db.Boolean, default=False)
    tutor_category = db.Column(db.String(80), default="")
    bonus_credits = db.Column(db.Integer, default=0)
    total_earnings_pkr = db.Column(db.Integer, default=0)
    monthly_earnings_pkr = db.Column(db.Integer, default=0)
    sessions_completed = db.Column(db.Integer, default=0)
    rating_avg = db.Column(db.Float, default=0.0)
    rating_count = db.Column(db.Integer, default=0)
    credits_balance = db.Column(db.Integer, default=0)
    pending_payout_pkr = db.Column(db.Integer, default=0)
    payout_method = db.Column(db.String(30), default="bank")
    payout_account_title = db.Column(db.String(120), default="")
    payout_account_number = db.Column(db.String(120), default="")
    payout_iban = db.Column(db.String(64), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    bookings_as_student = db.relationship(
        "Booking",
        backref="student",
        lazy=True,
        foreign_keys="Booking.student_id",
    )
    bookings_as_tutor = db.relationship(
        "Booking",
        backref="tutor",
        lazy=True,
        foreign_keys="Booking.tutor_id",
    )

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self):
        return self.is_active_user


class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    tutor_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    subject = db.Column(db.String(120), nullable=False)
    class_level = db.Column(db.String(80), nullable=False)
    scheduled_at = db.Column(db.DateTime, nullable=False)
    duration_minutes = db.Column(db.Integer, default=60)
    credits_cost = db.Column(db.Integer, default=100)
    status = db.Column(db.String(30), default="scheduled")
    student_marked_complete = db.Column(db.Boolean, default=False)
    tutor_marked_complete = db.Column(db.Boolean, default=False)
    payout_released = db.Column(db.Boolean, default=False)
    
    reschedule_requested_at = db.Column(db.DateTime)
    proposed_scheduled_at = db.Column(db.DateTime)
    reschedule_status = db.Column(db.String(20), default="none")
    reschedule_note = db.Column(db.Text, default="")
    
    room_code = db.Column(db.String(50), default=lambda: uuid4().hex[:10])
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class TutorAvailabilityRule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tutor_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    weekday = db.Column(db.Integer, nullable=False)  # 0 = Monday, 6 = Sunday
    start_time = db.Column(db.String(5), nullable=False)  # HH:MM
    end_time = db.Column(db.String(5), nullable=False)    # HH:MM
    slot_minutes = db.Column(db.Integer, default=60)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    tutor = db.relationship("User", backref="availability_rules")


class TutorAvailabilityException(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tutor_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    exception_date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.String(5), default="")      # optional HH:MM
    end_time = db.Column(db.String(5), default="")        # optional HH:MM
    is_blocked = db.Column(db.Boolean, default=True)      # True = blocked/unavailable
    note = db.Column(db.String(255), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    tutor = db.relationship("User", backref="availability_exceptions")

class CreditTransaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    credits_change = db.Column(db.Integer, nullable=False)
    rupee_amount = db.Column(db.Integer, default=0)
    tx_type = db.Column(db.String(50), nullable=False)
    note = db.Column(db.String(255), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship("User", backref="credit_transactions")


class PaymentNotice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    amount_sent_pkr = db.Column(db.Integer, nullable=False)
    claimed_credits = db.Column(db.Integer, nullable=False)
    sender_name = db.Column(db.String(120), default="")
    sender_account = db.Column(db.String(120), default="")
    transfer_method = db.Column(db.String(50), default="bank")
    screenshot_filename = db.Column(db.String(255), default="")
    note = db.Column(db.Text, default="")
    status = db.Column(db.String(20), default="pending")
    admin_note = db.Column(db.Text, default="")
    reviewed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    student = db.relationship("User", backref="payment_notices")


class Feedback(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("booking.id"), nullable=False)
    tutor_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    punctuality = db.Column(db.Integer, nullable=False)
    explanation = db.Column(db.Integer, nullable=False)
    professionalism = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    tutor = db.relationship("User", foreign_keys=[tutor_id], backref="feedback_received")
    student = db.relationship("User", foreign_keys=[student_id])
    booking = db.relationship("Booking", backref="feedback_entries")


class TutorBonus(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tutor_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    bonus_type = db.Column(db.String(50), nullable=False)
    credits_awarded = db.Column(db.Integer, nullable=False)
    note = db.Column(db.String(255), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    tutor = db.relationship("User", backref="bonus_entries")



class WithdrawalRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tutor_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    amount_pkr = db.Column(db.Integer, nullable=False)
    payout_method = db.Column(db.String(30), default="bank")
    payout_account_title = db.Column(db.String(120), default="")
    payout_account_number = db.Column(db.String(120), default="")
    payout_iban = db.Column(db.String(64), default="")
    status = db.Column(db.String(20), default="requested")
    admin_note = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    tutor = db.relationship("User", backref="withdrawals")


class LiveSessionLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("booking.id"), nullable=False)
    room_code = db.Column(db.String(50), nullable=False)
    student_joined = db.Column(db.Boolean, default=False)
    tutor_joined = db.Column(db.Boolean, default=False)
    admin_joined = db.Column(db.Boolean, default=False)
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    ended_at = db.Column(db.DateTime)
    last_activity_note = db.Column(db.String(255), default="Session created")
    booking = db.relationship("Booking", backref="live_log")

app.extensions["rtc_models"] = {
    "db": db,
    "Booking": Booking,
    "LiveSessionLog": LiveSessionLog,
}
app.register_blueprint(rtc_bp)

class ChatFlag(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("booking.id"), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    message = db.Column(db.Text, nullable=False)
    reason = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    booking = db.relationship("Booking", backref="chat_flags")
    sender = db.relationship("User")

class Conversation(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    tutor_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    booking_id = db.Column(db.Integer, db.ForeignKey("booking.id"), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    student = db.relationship("User", foreign_keys=[student_id], backref="student_conversations")
    tutor = db.relationship("User", foreign_keys=[tutor_id], backref="tutor_conversations")
    booking = db.relationship("Booking", backref="conversation")


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    conversation_id = db.Column(
        db.Integer,
        db.ForeignKey("conversation.id"),
        nullable=False
    )

    sender_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    body = db.Column(db.Text, nullable=False)

    is_read = db.Column(db.Boolean, default=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    conversation = db.relationship(
        "Conversation",
        backref=db.backref(
            "messages",
            lazy=True,
            order_by="Message.created_at.asc()"
        )
    )

    sender = db.relationship("User")

# Add this model near PaymentNotice if not already present
class TutorFeeNotice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tutor_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    amount_sent_pkr = db.Column(db.Integer, nullable=False, default=500)
    sender_name = db.Column(db.String(120), default="")
    sender_account = db.Column(db.String(120), default="")
    transfer_method = db.Column(db.String(50), default="easypaisa")
    screenshot_filename = db.Column(db.String(255), default="")
    note = db.Column(db.Text, default="")
    status = db.Column(db.String(20), default="pending")
    admin_note = db.Column(db.Text, default="")
    reviewed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    tutor = db.relationship("User", backref="tutor_fee_notices")

@app.context_processor
def inject_message_badge():
    return {
        "global_unread_messages": unread_message_count_for(current_user)
        if current_user.is_authenticated else 0
    }

@app.route("/messages")
@login_required
def messages_inbox():
    if current_user.role not in ["student", "tutor", "admin"]:
        flash("Messaging is not available for this account.", "danger")
        return redirect(url_for("dashboard"))

    if current_user.role == "student":
        conversations = (
            Conversation.query
            .filter_by(student_id=current_user.id)
            .order_by(Conversation.updated_at.desc())
            .all()
        )
    elif current_user.role == "tutor":
        conversations = (
            Conversation.query
            .filter_by(tutor_id=current_user.id)
            .order_by(Conversation.updated_at.desc())
            .all()
        )
    else:
        conversations = []

    unread_count = 0
    for convo in conversations:
        unread_count += Message.query.filter(
            Message.conversation_id == convo.id,
            Message.sender_id != current_user.id,
            Message.is_read == False
        ).count()

    return render_template(
        "messages_inbox.html",
        conversations=conversations,
        unread_count=unread_count,
    )


@app.route("/messages/start/<int:tutor_id>")
@login_required
def start_conversation(tutor_id):
    if current_user.role != "student":
        flash("Only students can start a conversation with tutors.", "danger")
        return redirect(url_for("dashboard"))

    tutor = User.query.get_or_404(tutor_id)
    if tutor.role != "tutor":
        flash("Tutor not found.", "danger")
        return redirect(url_for("tutors"))

    convo = get_or_create_conversation(
        student_id=current_user.id,
        tutor_id=tutor.id,
        booking_id=None
    )
    db.session.commit()

    return redirect(url_for("message_thread", conversation_id=convo.id))


@app.route("/messages/<int:conversation_id>", methods=["GET", "POST"])
@login_required
def message_thread(conversation_id):
    convo = Conversation.query.get_or_404(conversation_id)

    allowed = (
        current_user.id == convo.student_id or
        current_user.id == convo.tutor_id or
        current_user.role == "admin"
    )
    if not allowed:
        flash("You are not allowed to view this conversation.", "danger")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        body = (request.form.get("body", "") or "").strip()

        if not body:
            flash("Message cannot be empty.", "danger")
            return redirect(url_for("message_thread", conversation_id=convo.id))

        for pattern in PHONE_OR_EMAIL_PATTERNS:
            if pattern.search(body):
                flash("Phone numbers, emails, and off-platform contact prompts are not allowed in chat.", "danger")
                return redirect(url_for("message_thread", conversation_id=convo.id))

        msg = Message(
            conversation_id=convo.id,
            sender_id=current_user.id,
            body=body,
            is_read=False,
        )
        db.session.add(msg)
        convo.updated_at = datetime.utcnow()
        db.session.commit()

        return redirect(url_for("message_thread", conversation_id=convo.id))

    unread_messages = Message.query.filter(
        Message.conversation_id == convo.id,
        Message.sender_id != current_user.id,
        Message.is_read == False
    ).all()

    for msg in unread_messages:
        msg.is_read = True
    if unread_messages:
        db.session.commit()

    other_user = convo.tutor if current_user.id == convo.student_id else convo.student

    return render_template(
        "message_thread.html",
        conversation=convo,
        other_user=other_user,
        messages=convo.messages,
    )

@app.route("/booking/<int:booking_id>/mark-complete", methods=["POST"])
@login_required
def mark_booking_complete(booking_id):
    booking = Booking.query.get_or_404(booking_id)

    ok, message = mark_booking_complete_for_user(booking, current_user)
    if not ok:
        flash(message, "danger" if message == "Unauthorized." else "warning")
        return redirect(url_for("dashboard"))

    db.session.commit()
    flash(message, "success")
    return redirect(url_for("dashboard"))



@app.route("/tutor/registration-fee", methods=["GET", "POST"])
@login_required
def tutor_registration_fee():
    if current_user.role != "tutor":
        flash("Only tutors can access this page.", "danger")
        return redirect(url_for("dashboard"))

    if current_user.profile_stage != "fee_pending":
        flash("No tutor registration fee is pending on your account.", "info")
        return redirect(url_for("dashboard"))

    form_data = request.form.to_dict(flat=True) if request.method == "POST" else {}

    if request.method == "POST":
        if request.form.get("payment_confirmed", "").strip() != "yes":
            flash("Please confirm the payment before submitting.", "danger")
            return render_template("tutor_registration_fee.html", form_data=form_data)

        sender_name = (request.form.get("sender_name", "") or "").strip()
        sender_account = (request.form.get("sender_account", "") or "").strip()
        transfer_method = (request.form.get("transfer_method", "easypaisa") or "easypaisa").strip()

        if not sender_name or not sender_account:
            flash("Please enter the sender name and account/mobile number.", "danger")
            return render_template("tutor_registration_fee.html", form_data=form_data)

        existing_pending = TutorFeeNotice.query.filter(
            TutorFeeNotice.tutor_id == current_user.id,
            TutorFeeNotice.status.in_(PAYMENT_REVIEWABLE_STATUSES),
        ).order_by(TutorFeeNotice.created_at.desc()).first()

        if existing_pending:
            flash("You already have a tutor fee notice under admin review.", "warning")
            return redirect(url_for("dashboard"))

        screenshot = request.files.get("screenshot")
        if not screenshot or not screenshot.filename:
            flash("Please attach payment screenshot.", "danger")
            return render_template("tutor_registration_fee.html", form_data=form_data)

        filename, upload_error = save_upload(
            screenshot,
            prefix="tutor_fee",
            allowed_extensions=ALLOWED_DOCUMENT_EXTENSIONS,
            label="Payment screenshot",
            subfolder="payments",
        )
        if upload_error:
            flash(upload_error, "danger")
            return render_template("tutor_registration_fee.html", form_data=form_data)

        notice = TutorFeeNotice(
            tutor_id=current_user.id,
            amount_sent_pkr=500,
            sender_name=sender_name,
            sender_account=sender_account,
            transfer_method=transfer_method,
            screenshot_filename=filename,
            note=(request.form.get("note", "") or "").strip(),
            status="pending",
        )
        db.session.add(notice)
        db.session.commit()

        safe_send_email(
            "superadmin@tutorsonline.pk",
            "Tutor registration fee notice - TutorsOnline.pk",
            f"""Tutor: {current_user.full_name} ({current_user.email})
Amount: PKR 500
Sender: {notice.sender_name}
Account: {notice.sender_account}
Method: {notice.transfer_method}
Notice ID: {notice.id}
Screenshot: {filename}

Status: pending
Action required: Admin approval or decline
""",
        )

        flash("Tutor fee notice submitted successfully. Admin will review it shortly.", "success")
        return redirect(url_for("dashboard"))

    return render_template("tutor_registration_fee.html", form_data=form_data)


@app.route("/admin/tutor-fee-notices")
@login_required
def admin_tutor_fee_notices():
    if current_user.role != "admin":
        flash("Unauthorized.", "danger")
        return redirect(url_for("dashboard"))

    notices = TutorFeeNotice.query.order_by(TutorFeeNotice.created_at.desc()).all()
    return render_template("admin_tutor_fee_notices.html", notices=notices)


@app.route("/admin/tutor-fee-notices/<int:notice_id>/action", methods=["POST"])
@login_required
def admin_tutor_fee_action(notice_id):
    if current_user.role != "admin":
        flash("Unauthorized.", "danger")
        return redirect(url_for("dashboard"))

    notice = TutorFeeNotice.query.get_or_404(notice_id)
    action = (request.form.get("action", "") or "").strip().lower()
    reason = (request.form.get("reason", "") or "").strip()

    if notice.status in {"approved", "declined"}:
        flash("This tutor fee notice has already been finalized.", "warning")
        return redirect(url_for("admin_tutor_fee_notices"))

    if action == "approve":
        notice.status = "approved"
        notice.admin_note = reason
        notice.reviewed_at = datetime.utcnow()

        tutor = notice.tutor
        tutor.profile_stage = "approved"
        tutor.is_verified_tutor = True
        tutor.is_public_tutor = True
        tutor.approved_at = datetime.utcnow()

        db.session.commit()

        safe_send_email(
            tutor.email,
            "Tutor activation complete - TutorsOnline.pk",
            f"""Assalam-o-Alaikum {tutor.full_name},

Your PKR 500 registration fee has been verified.

Your tutor profile is now active on TutorsOnline.pk.

{f"Admin note: {reason}" if reason else ""}

Regards,
TutorsOnline.pk
superadmin@tutorsonline.pk
""",
        )

        flash("Tutor fee approved and tutor activated.", "success")
        return redirect(url_for("admin_tutor_fee_notices"))

    if action == "decline":
        notice.status = "declined"
        notice.admin_note = reason or "Fee proof could not be verified."
        notice.reviewed_at = datetime.utcnow()
        db.session.commit()

        safe_send_email(
            notice.tutor.email,
            "Tutor fee notice declined - TutorsOnline.pk",
            f"""Assalam-o-Alaikum {notice.tutor.full_name},

Your tutor registration fee notice could not be approved.

Reason: {notice.admin_note}

Your profile remains pending fee confirmation.

Regards,
TutorsOnline.pk
superadmin@tutorsonline.pk
""",
        )

        flash("Tutor fee notice declined.", "info")
        return redirect(url_for("admin_tutor_fee_notices"))

    if action == "on_hold":
        notice.status = "on_hold"
        notice.admin_note = reason or "Tutor fee notice is under review."
        notice.reviewed_at = datetime.utcnow()
        db.session.commit()
        flash("Tutor fee notice marked on hold.", "warning")
        return redirect(url_for("admin_tutor_fee_notices"))

    flash("Invalid action.", "danger")
    return redirect(url_for("admin_tutor_fee_notices"))



@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def unread_message_count_for(user):
    if not user or not getattr(user, "is_authenticated", False):
        return 0

    if user.role == "student":
        convo_ids = [
            c.id for c in Conversation.query.filter_by(student_id=user.id).all()
        ]
    elif user.role == "tutor":
        convo_ids = [
            c.id for c in Conversation.query.filter_by(tutor_id=user.id).all()
        ]
    elif user.role == "admin":
        return 0
    else:
        return 0

    if not convo_ids:
        return 0

    return Message.query.filter(
        Message.conversation_id.in_(convo_ids),
        Message.sender_id != user.id,
        Message.is_read == False
    ).count()

@app.context_processor
def inject_globals():
    return {
        "credit_rate": app.config["CREDIT_RATE"],
        "google_configured": bool(
            app.config["GOOGLE_CLIENT_ID"] and app.config["GOOGLE_CLIENT_SECRET"]
        ),
        "under_construction": UNDER_CONSTRUCTION,
    }


@app.after_request
def add_security_headers(response):
    """Lightweight security headers that are safe for the current templates/RTC pages."""
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(self), microphone=(self), geolocation=()",
    )
    return response


@app.route("/preview-off")
@login_required
def preview_off():
    session.pop("preview_mode", None)
    flash("Preview mode disabled", "info")
    return redirect(url_for("index"))

@app.route("/test-email")
@login_required
def test_email():
    guard = admin_required_response()
    if guard:
        return guard

    ok = send_email(
        "superadmin@tutorsonline.pk",
        "TutorsOnline.pk SMTP Test",
        """Assalam-o-Alaikum,

This is a successful SMTP test email from TutorsOnline.pk.

Regards,
TutorsOnline.pk
superadmin@tutorsonline.pk
"""
    )
    return "Email sent!" if ok else "Email failed!"


@app.before_request
def construction_gate():
    # simple in-memory visitor count for admin only
    if request.method == "GET" and not request.path.startswith("/static/"):
        if not session.get("_visit_counted"):
            session["_visit_counted"] = True
            app.config["VISITOR_COUNT"] = app.config.get("VISITOR_COUNT", 0) + 1

    # admin preview bypass
    if current_user.is_authenticated and current_user.role == "admin":
        if request.args.get("preview") == "1":
            return

    # Admin preview activation
    if current_user.is_authenticated and current_user.role == "admin":
        if request.args.get("preview") == "1":
            session["preview_mode"] = True

    # If preview mode is active, allow everything
    if session.get("preview_mode"):
        return


    if not UNDER_CONSTRUCTION:
        return

    path = request.path

    if (
        path == "/"
        or path.startswith("/login")
        or path.startswith("/logout")
        or path.startswith("/register")
        or path.startswith("/google-login")
        or path.startswith("/login/google/callback")
        or path.startswith("/complete-google-signup")
        or path.startswith("/admin")
        or path.startswith("/dashboard")
        or path.startswith("/contact")
        or path.startswith("/static/")
        or path.startswith("/uploads/")
        or path.startswith("/seed")
    ):
        return

    return redirect(url_for("index"))


def send_notification_email(subject: str, body: str):
    outbox = BASE_DIR / "email_outbox.log"
    stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    existing = outbox.read_text(encoding="utf-8") if outbox.exists() else ""
    message = (
        f"\n[{stamp}] TO: {app.config['ADMIN_NOTIFICATION_EMAIL']}\n"
        f"SUBJECT: {subject}\n{body}\n"
    )
    outbox.write_text(existing + message, encoding="utf-8")
    return not bool(app.config["MAIL_SERVER"])


def add_credits(user: User, credits: int, tx_type: str, note: str = "", rupees: int = 0):
    user.credits_balance += credits
    db.session.add(
        CreditTransaction(
            user_id=user.id,
            credits_change=credits,
            rupee_amount=rupees,
            tx_type=tx_type,
            note=note,
        )
    )


PAYMENT_STATUS_PENDING = "pending"
PAYMENT_STATUS_APPROVED = "approved"
PAYMENT_STATUS_DECLINED = "declined"
PAYMENT_STATUS_ON_HOLD = "on_hold"
PAYMENT_REVIEWABLE_STATUSES = {
    PAYMENT_STATUS_PENDING,
    PAYMENT_STATUS_ON_HOLD,
    "under_review",
}
PAYMENT_FINAL_STATUSES = {PAYMENT_STATUS_APPROVED, PAYMENT_STATUS_DECLINED}
PAYMENT_STATUS_ALIASES = {
    "rejected": PAYMENT_STATUS_DECLINED,
    "decline": PAYMENT_STATUS_DECLINED,
}


def normalize_payment_status(notice):
    """Keep legacy payment notice statuses aligned with the current workflow."""
    original_status = (notice.status or PAYMENT_STATUS_PENDING).strip().lower()
    normalized_status = PAYMENT_STATUS_ALIASES.get(original_status, original_status)

    if notice.status != normalized_status:
        notice.status = normalized_status
        return True

    return False


def normalize_payment_notices(notices):
    changed = False
    for notice in notices:
        changed = normalize_payment_status(notice) or changed

    if changed:
        db.session.commit()


def approve_student_payment_notice(notice, admin_note: str = ""):
    normalize_payment_status(notice)

    if notice.status == PAYMENT_STATUS_APPROVED:
        return False, "This payment notice has already been approved."

    if notice.status == PAYMENT_STATUS_DECLINED:
        return False, "Declined payment notices cannot be approved. Ask the student to submit a new notice."

    if notice.status not in PAYMENT_REVIEWABLE_STATUSES:
        return False, "This payment notice is not available for review."

    add_credits(
        notice.student,
        notice.claimed_credits,
        "manual_topup",
        f"Approved payment notice #{notice.id}",
        rupees=notice.amount_sent_pkr,
    )
    notice.status = PAYMENT_STATUS_APPROVED
    notice.admin_note = admin_note
    notice.reviewed_at = datetime.utcnow()
    return True, f"Approved notice #{notice.id} and added {notice.claimed_credits} credits."


def decline_student_payment_notice(notice, admin_note: str = ""):
    normalize_payment_status(notice)

    if notice.status == PAYMENT_STATUS_APPROVED:
        return False, "Approved payment notices cannot be declined."

    if notice.status == PAYMENT_STATUS_DECLINED:
        return False, "This payment notice has already been declined."

    if notice.status not in PAYMENT_REVIEWABLE_STATUSES:
        return False, "This payment notice is not available for review."

    notice.status = PAYMENT_STATUS_DECLINED
    notice.admin_note = admin_note or "Payment proof could not be verified."
    notice.reviewed_at = datetime.utcnow()
    return True, f"Declined notice #{notice.id}."


def hold_student_payment_notice(notice, admin_note: str = ""):
    normalize_payment_status(notice)

    if notice.status in PAYMENT_FINAL_STATUSES:
        return False, "Finalized payment notices cannot be put on hold."

    notice.status = PAYMENT_STATUS_ON_HOLD
    notice.admin_note = admin_note or "Payment notice is being reviewed."
    notice.reviewed_at = datetime.utcnow()
    return True, f"Notice #{notice.id} marked on hold."


def get_youtube_embed(url):
    if not url:
        return ""

    url = url.strip()

    if "youtu.be/" in url:
        return url.replace("youtu.be/", "https://www.youtube.com/embed/")

    if "youtube.com/watch?v=" in url:
        return url.replace("youtube.com/watch?v=", "youtube.com/embed/")

    if "youtube.com/embed/" in url:
        return url

    return ""

def validate_tutor_application_form(form):
    missing = tutor_missing_requirements_from_form(form)

    demo_length_confirmed = form.get("demo_length_confirmed", "").strip()
    if demo_length_confirmed != "yes":
        missing.append("demo length confirmation")

    if missing:
        return "Please complete: " + ", ".join(missing) + "."

    return None

def tutor_missing_requirements_from_form(form):
    missing = []

    highest = pick_with_other(form, "qualification")
    next_choice = (form.get("previous_path_choice", "") or "").strip().lower()

    if not highest:
        missing.append("highest qualification")

    if highest == "phd" and next_choice not in {"mphil", "masters"}:
        missing.append("qualification before highest")

    if not normalize_subjects(form, "tutor"):
        missing.append("subjects")

    if not normalize_levels(form):
        missing.append("teaching level")

    if not form.get("experience_years", "").strip():
        missing.append("experience")

    if not form.get("bio", "").strip():
        missing.append("bio")

    if not form.get("demo_video_url", "").strip():
        missing.append("demo video")

    if not form.get("degree_title", "").strip():
        missing.append("highest qualification title")

    if not form.get("degree_institution", "").strip():
        missing.append("highest qualification institution")

    if not form.get("degree_year", "").strip():
        missing.append("highest qualification year")

    if not form.get("degree_grade", "").strip():
        missing.append("highest qualification grade")

    if not form.get("mobile_number", "").strip():
        missing.append("mobile number")

    if not form.get("cnic_number", "").strip():
        missing.append("CNIC number")

    def require_if_visible(prefix, label):
        if not form.get(f"{prefix}_title", "").strip():
            missing.append(f"{label} title")
        if not form.get(f"{prefix}_institution", "").strip():
            missing.append(f"{label} institution")
        if not form.get(f"{prefix}_year", "").strip():
            missing.append(f"{label} year")
        if not form.get(f"{prefix}_grade", "").strip():
            missing.append(f"{label} grade")

    chain = lower_qualification_chain(highest, next_choice)

    if "mphil" in chain:
        require_if_visible("mphil", "MPhil")
    if "masters" in chain:
        require_if_visible("masters", "Master's")
    if "bachelor" in chain:
        require_if_visible("bachelor", "Bachelor's")

    if "inter" in chain:
        if not form.get("inter_program", "").strip():
            missing.append("intermediate")
        if not form.get("inter_institution", "").strip():
            missing.append("intermediate institution")
        if not form.get("inter_year", "").strip():
            missing.append("intermediate year")
        if not form.get("inter_grade", "").strip():
            missing.append("intermediate grade")

    if "matric" in chain:
        if not form.get("matric_program", "").strip():
            missing.append("matric")
        if not form.get("matric_institution", "").strip():
            missing.append("matric institution")
        if not form.get("matric_year", "").strip():
            missing.append("matric year")
        if not form.get("matric_grade", "").strip():
            missing.append("matric grade")

    return list(dict.fromkeys(missing))

def apply_bonus_if_eligible(tutor: User):
    milestones = [
        (50000, "earnings_milestone_50k", 100),
        (100000, "earnings_milestone_100k", 300),
        (200000, "earnings_milestone_200k", 700),
    ]
    existing_types = {b.bonus_type for b in tutor.bonus_entries}
    for amount, bonus_type, credits in milestones:
        if tutor.total_earnings_pkr >= amount and bonus_type not in existing_types:
            tutor.bonus_credits += credits
            add_credits(
                tutor,
                credits,
                "bonus",
                f"Bonus awarded for crossing PKR {amount}",
            )
            db.session.add(
                TutorBonus(
                    tutor_id=tutor.id,
                    bonus_type=bonus_type,
                    credits_awarded=credits,
                    note=f"Milestone bonus at PKR {amount}",
                )
            )


def classify_teacher(subjects: str, levels: str) -> str:
    levels = levels.lower()
    if "a level" in levels:
        return "A Level Specialist"
    if "o level" in levels:
        return "O Level Specialist"
    if "intermediate" in levels:
        return "Intermediate Specialist"
    if "matric" in levels:
        return "Matric Specialist"
    return "Grade 5–8 Tutor"

def pick_with_other(form, field_name: str) -> str:
    value = form.get(field_name, "").strip()
    other_value = form.get(f"{field_name}_other", "").strip()
    if value == "other" and other_value:
        return other_value
    return value




def get_multi_values(form, field_name: str):
    values = []
    getter = getattr(form, "getlist", None)
    if getter:
        values.extend([v.strip() for v in getter(field_name) if v and v.strip()])
    single = form.get(field_name, "").strip()
    if single and single not in values:
        values.append(single)
    return values

def normalize_subjects(form, role="tutor"):
    if role == "student":
        selected = get_multi_values(form, "student_subject_needed")
        manual = [form.get(f"student_other_subject_{i}", "").strip() for i in range(1, 4)]
    else:
        selected = get_multi_values(form, "main_subject")
        manual = [form.get(f"additional_subject_{i}", "").strip() for i in range(1, 4)]
    merged = []
    for item in selected + manual:
        if item and item not in merged:
            merged.append(item)
    return merged

def normalize_levels(form):
    levels = get_multi_values(form, "class_levels")
    if not levels:
        single = pick_with_other(form, "class_levels")
        if single:
            levels = [single]
    unique = []
    for item in levels:
        if item and item not in unique:
            unique.append(item)
    return unique

def qualification_allowed_levels(qualification: str):
    qualification = (qualification or "").strip().lower()
    if qualification == "intermediate":
        return {"grade_1_5", "grade_6_8", "matric"}
    if qualification == "bachelors":
        return {"grade_1_5", "grade_6_8", "matric", "intermediate"}
    if qualification in {"masters", "mphil", "phd"}:
        return {"grade_1_5", "grade_6_8", "matric", "intermediate", "o_level", "a_level", "university"}
    return {"grade_1_5", "grade_6_8", "matric", "intermediate"}

def lower_qualification_chain(highest, next_choice=""):
    highest = (highest or "").strip().lower()
    next_choice = (next_choice or "").strip().lower()

    if highest == "phd":
        if next_choice == "mphil":
            return ["mphil", "masters", "bachelor", "inter", "matric"]
        if next_choice == "masters":
            return ["masters", "bachelor", "inter", "matric"]
        return []
    if highest == "mphil":
        return ["masters", "bachelor", "inter", "matric"]
    if highest == "masters":
        return ["bachelor", "inter", "matric"]
    if highest == "bachelors":
        return ["inter", "matric"]
    if highest == "intermediate":
        return ["matric"]
    if highest == "other":
        return ["inter", "matric"]
    return []


def media_url(path, fallback=None):
    """Return a safe public URL for uploaded/static media.

    Stored profile images may be saved as just "file.jpg", "/uploads/file.jpg",
    "uploads/file.jpg", or a complete external URL depending on the form or import
    that created them. Normalizing here avoids broken image URLs after deployment.
    """
    fallback = fallback or url_for("static", filename=PROFILE_IMAGE_FALLBACK)
    path = (path or "").strip()
    if not path:
        return fallback

    if path.startswith(("http://", "https://")):
        return path

    path = path.replace("\\", "/").lstrip("/")

    if path.startswith("static/"):
        return url_for("static", filename=path[len("static/"):])

    if path.startswith("demo_seed/"):
        return url_for("demo_seed_file", filename=path[len("demo_seed/"):])

    if path.startswith("uploads/"):
        path = path[len("uploads/"):]

    if not path or ".." in Path(path).parts:
        return fallback

    return url_for("uploaded_file", filename=path)


def image_url(path, fallback=None):
    """Template helper for profile/course images with a safe default avatar."""
    return media_url(path, fallback=fallback)


app.jinja_env.globals["media_url"] = media_url
app.jinja_env.globals["image_url"] = image_url


def is_safe_redirect_url(target):
    """Allow only same-host relative redirects to prevent open-redirect bugs."""
    if not target:
        return False

    ref = urlsplit(request.host_url)
    test = urlsplit(target)
    return (not test.netloc or test.netloc == ref.netloc) and test.scheme in ("", ref.scheme)


def safe_next_url(default_endpoint="dashboard"):
    target = request.args.get("next") or request.form.get("next")
    if is_safe_redirect_url(target):
        return target
    return url_for(default_endpoint)


def login_rate_limited(email):
    """Small session-based login throttle without adding new dependencies."""
    key = f"login_failures:{email}"
    now = datetime.utcnow()
    failures = session.get(key, [])

    valid_failures = []
    for timestamp in failures:
        try:
            failure_time = datetime.fromisoformat(timestamp)
        except (TypeError, ValueError):
            continue
        if now - failure_time < timedelta(minutes=15):
            valid_failures.append(timestamp)

    session[key] = valid_failures
    return len(valid_failures) >= 6


def record_login_failure(email):
    key = f"login_failures:{email}"
    failures = session.get(key, [])
    failures.append(datetime.utcnow().isoformat())
    session[key] = failures[-10:]


def clear_login_failures(email):
    session.pop(f"login_failures:{email}", None)


def file_extension(filename):
    return Path(filename or "").suffix.lower()


def allowed_upload(filename, allowed_extensions):
    return file_extension(filename) in allowed_extensions


def upload_error_message(label, allowed_extensions):
    extensions = ", ".join(sorted(ext.lstrip(".") for ext in allowed_extensions))
    return f"{label} must be one of: {extensions}."


def build_upload_filename(prefix, original_filename):
    original_name = secure_filename(original_filename or "")
    suffix = file_extension(original_name)
    stem = Path(original_name).stem[:50] or "upload"
    return f"{prefix}_{uuid4().hex}_{stem}{suffix}"


def save_upload(file_storage, prefix, allowed_extensions, label, subfolder=""):
    """Validate and save an upload. Returns (filename, error_message)."""
    if not file_storage or not file_storage.filename:
        return "", ""

    if not allowed_upload(file_storage.filename, allowed_extensions):
        return "", upload_error_message(label, allowed_extensions)

    target_dir = Path(app.config["UPLOAD_FOLDER"])
    if subfolder:
        safe_parts = [secure_filename(part) for part in subfolder.split("/") if secure_filename(part)]
        target_dir = target_dir.joinpath(*safe_parts)

    target_dir.mkdir(parents=True, exist_ok=True)

    filename = build_upload_filename(prefix, file_storage.filename)
    file_storage.save(target_dir / filename)

    stored_path = f"{subfolder.strip('/')}/{filename}" if subfolder else filename
    return stored_path, ""


def save_profile_image(file_storage, prefix="profile"):
    """Save profile image with extension validation and optional Pillow resizing.

    Pillow is optional. If it is installed, large photos are resized/compressed.
    If it is not installed, the validated image is saved as uploaded.
    """
    filename, error = save_upload(
        file_storage,
        prefix=prefix,
        allowed_extensions=ALLOWED_IMAGE_EXTENSIONS,
        label="Profile image",
        subfolder="profiles",
    )
    if error or not filename:
        return filename, error

    try:
        from PIL import Image  # Optional dependency.
    except Exception:
        return filename, ""

    path = Path(app.config["UPLOAD_FOLDER"]) / filename
    try:
        with Image.open(path) as img:
            img.thumbnail((900, 900))
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
            save_kwargs = {"optimize": True}
            if path.suffix.lower() in {".jpg", ".jpeg"}:
                save_kwargs["quality"] = 86
            img.save(path, **save_kwargs)
    except Exception as exc:
        app.logger.warning("Profile image optimization skipped for %s: %s", filename, exc)

    return filename, ""


def tutor_missing_requirements_from_user(user):
    missing = []

    highest = (user.qualification or "").strip().lower()
    next_choice = (getattr(user, "previous_path_choice", "") or "").strip().lower()

    if not user.qualification:
        missing.append("highest qualification")

    if highest == "phd" and next_choice not in {"mphil", "masters"}:
        missing.append("qualification before highest")

    if not user.main_subject:
        missing.append("main subject")

    if not user.class_levels:
        missing.append("teaching level")

    if not user.experience_years:
        missing.append("experience")

    if not user.bio:
        missing.append("bio")

    if not user.demo_video_url:
        missing.append("demo video")

    if not user.degree_title:
        missing.append("highest qualification title")

    if not user.degree_institution:
        missing.append("highest qualification institution")

    if not user.degree_year:
        missing.append("highest qualification year")

    if not user.degree_grade:
        missing.append("highest qualification grade")

    if not getattr(user, "mobile_number", ""):
        missing.append("mobile number")

    if not getattr(user, "cnic_number", ""):
        missing.append("CNIC number")

    def require_if_visible(title, institution, year, grade, label):
        if not title:
            missing.append(f"{label} title")
        if not institution:
            missing.append(f"{label} institution")
        if not year:
            missing.append(f"{label} year")
        if not grade:
            missing.append(f"{label} grade")

    chain = lower_qualification_chain(highest, next_choice)

    if "mphil" in chain:
        require_if_visible(
            getattr(user, "mphil_title", ""),
            getattr(user, "mphil_institution", ""),
            getattr(user, "mphil_year", ""),
            getattr(user, "mphil_grade", ""),
            "MPhil",
        )

    if "masters" in chain:
        require_if_visible(
            getattr(user, "masters_title", ""),
            getattr(user, "masters_institution", ""),
            getattr(user, "masters_year", ""),
            getattr(user, "masters_grade", ""),
            "Master's",
        )

    if "bachelor" in chain:
        require_if_visible(
            getattr(user, "bachelor_title", ""),
            getattr(user, "bachelor_institution", ""),
            getattr(user, "bachelor_year", ""),
            getattr(user, "bachelor_grade", ""),
            "Bachelor's",
        )

    if "inter" in chain:
        if not getattr(user, "inter_program", ""):
            missing.append("intermediate")
        if not getattr(user, "inter_institution", ""):
            missing.append("intermediate institution")
        if not getattr(user, "inter_year", ""):
            missing.append("intermediate year")
        if not getattr(user, "inter_grade", ""):
            missing.append("intermediate grade")

    if "matric" in chain:
        if not getattr(user, "matric_program", ""):
            missing.append("matric")
        if not getattr(user, "matric_institution", ""):
            missing.append("matric institution")
        if not getattr(user, "matric_year", ""):
            missing.append("matric year")
        if not getattr(user, "matric_grade", ""):
            missing.append("matric grade")

    return list(dict.fromkeys(missing))

def validate_tutor_application_form(form):
    missing = tutor_missing_requirements_from_form(form)
    if form.get("demo_length_confirmed", "").strip() != "yes":
        missing.append("demo length confirmation")
    if form.get("accept_privacy", "").strip() != "yes":
        missing.append("privacy acceptance")
    qualification = pick_with_other(form, "qualification")
    levels = normalize_levels(form)
    allowed = qualification_allowed_levels(qualification)
    if levels and any(level not in allowed for level in levels):
        return "Selected teaching level is above the tutor's qualification allowance."
    if len(normalize_subjects(form, "tutor")) > 5:
        return "Please select up to 5 subjects total."
    if missing:
        return "Please complete: " + ", ".join(list(dict.fromkeys(missing))) + "."
    return None

def validate_option_a_student_form(form):
    missing = []
    for field, label in [
        ("full_name", "full name"),
        ("public_name", "display name"),
        ("email", "email"),
        ("password", "password"),
        ("student_level", "level"),
    ]:
        if not form.get(field, "").strip():
            missing.append(label)
    if not normalize_subjects(form, "student"):
        missing.append("subjects you want to study")
    if form.get("accept_privacy", "").strip() != "yes":
        missing.append("privacy acceptance")
    if missing:
        return "Please complete: " + ", ".join(missing) + "."
    return None

def get_stage_badge(stage: str):
    stage = (stage or "quick_profile").strip().lower()
    mapping = {
        "quick_profile": ("Quick profile", "Fill teaching and education details to continue."),
        "basic_complete": ("Quick profile complete", "You can now finish verification and submit for review."),
        "verification_incomplete": ("Complete verification", "Add the missing verification details below."),
        "under_review": ("Under review", "Your application is with admin review."),
        "fee_pending": ("Fee pending", "You were selected. Registration fee is pending."),
        "approved": ("Approved", "Your tutor profile is live."),
        "rejected": ("Rejected", "Your application was not approved at this time."),
    }
    return mapping.get(stage, (stage.replace("_", " ").title(), ""))

def compute_tutor_profile_stage(user):
    if user.role != "tutor":
        return user.profile_stage or "basic_complete"
    if user.is_verified_tutor or (user.profile_stage == "approved"):
        return "approved"
    if user.profile_stage in {"under_review", "fee_pending", "rejected"}:
        return user.profile_stage
    if tutor_missing_requirements_from_user(user):
        return "verification_incomplete"
    return "basic_complete"

def sync_tutor_stage(user):
    if user.role == "tutor":
        user.profile_stage = compute_tutor_profile_stage(user)
    return user.profile_stage

def build_user_from_option_a_form(form, files, google_email=None, google_name=None):
    role = form.get("role", "").strip().lower()
    email = (google_email or form.get("email", "")).strip().lower()
    full_name = form.get("full_name", google_name or "").strip() or (google_name or "")
    public_name = form.get("public_name", full_name).strip() or full_name
    gender = pick_with_other(form, "gender")
    city = pick_with_other(form, "city")
    password_value = form.get("password") or uuid4().hex

    if role == "student":
        student_level = pick_with_other(form, "student_level")
        student_subjects = normalize_subjects(form, "student")
        user = User(
            email=email,
            role="student",
            full_name=full_name,
            public_name=public_name,
            gender=gender,
            city=city,
            student_level=student_level,
            student_subject_needed=", ".join(student_subjects),
            preferred_tutor_gender=form.get("preferred_tutor_gender", "").strip(),
            learning_mode="online",
            teaching_mode="online",
            bio="Student account",
            subjects=", ".join(student_subjects),
            class_levels=student_level,
        )
    else:
        tutor_subjects = normalize_subjects(form, "tutor")
        levels = normalize_levels(form)
        user = User(
            email=email,
            role="tutor",
            full_name=full_name,
            public_name=public_name,
            qualification=pick_with_other(form, "qualification"),
            subjects=", ".join(tutor_subjects),
            class_levels=", ".join(levels),
            experience_years=int(form.get("experience_years") or 0),
            bio=form.get("bio", "").strip(),
            modest_profile=bool(form.get("modest_profile")),
            audio_only=bool(form.get("audio_only")),
            gender=gender,
            city=city,
            main_subject=", ".join(tutor_subjects[:3]),
            additional_subjects=", ".join(tutor_subjects[3:]),
            learning_mode="online",
            teaching_mode="online",
            hourly_rate=int(form.get("hourly_rate") or 0),
            demo_video_url=form.get("demo_video_url", "").strip(),
            degree_title=form.get("degree_title", ""),
            degree_major=form.get("degree_major", ""),
            degree_institution=form.get("degree_institution", ""),
            degree_year=form.get("degree_year", ""),
            degree_grade=form.get("degree_grade", ""),
            previous_path_choice=form.get("previous_path_choice", ""),
            mphil_title=form.get("mphil_title", ""),
            mphil_major=form.get("mphil_major", ""),
            mphil_institution=form.get("mphil_institution", ""),
            mphil_year=form.get("mphil_year", ""),
            mphil_grade=form.get("mphil_grade", ""),
            mphil_additional_note=form.get("mphil_additional_note", ""),
            masters_title=form.get("masters_title", ""),
            masters_major=form.get("masters_major", ""),
            masters_institution=form.get("masters_institution", ""),
            masters_year=form.get("masters_year", ""),
            masters_grade=form.get("masters_grade", ""),
            masters_additional_note=form.get("masters_additional_note", ""),
            bachelor_title=form.get("bachelor_title", ""),
            bachelor_major=form.get("bachelor_major", ""),
            bachelor_institution=form.get("bachelor_institution", ""),
            bachelor_year=form.get("bachelor_year", ""),
            bachelor_grade=form.get("bachelor_grade", ""),
            bachelor_additional_note=form.get("bachelor_additional_note", ""),
            inter_program=form.get("inter_program", ""),
            inter_institution=form.get("inter_institution", ""),
            inter_grade=form.get("inter_grade", ""),
            inter_year=form.get("inter_year", ""),
            matric_program=form.get("matric_program", ""),
            matric_institution=form.get("matric_institution", ""),
            matric_grade=form.get("matric_grade", ""),
            matric_year=form.get("matric_year", ""),
            mobile_number=form.get("mobile_number", "").strip(),
            cnic_number=form.get("cnic_number", "").strip(),
        )
        user.is_verified_tutor = False
        user.profile_stage = "verification_incomplete" if tutor_missing_requirements_from_user(user) else "basic_complete"
        user.is_public_tutor = False

    user.tutor_category = classify_teacher(user.subjects or "", user.class_levels or "")
    user.set_password(password_value)

    image_file = files.get("profile_image_file")
    if image_file and image_file.filename:
        filename, upload_error = save_profile_image(image_file, prefix="profile")
        if upload_error:
            raise ValueError(upload_error)
        user.profile_image = filename

    degree_file = files.get("degree_file")
    if degree_file and degree_file.filename:
        degree_filename, upload_error = save_upload(
            degree_file,
            prefix="degree",
            allowed_extensions=ALLOWED_DOCUMENT_EXTENSIONS,
            label="Degree file",
            subfolder="documents",
        )
        if upload_error:
            raise ValueError(upload_error)
        user.degree_file = degree_filename

    extra_file = files.get("additional_qualification_file")
    if extra_file and extra_file.filename and hasattr(user, "additional_qualification_file"):
        extra_filename, upload_error = save_upload(
            extra_file,
            prefix="extra",
            allowed_extensions=ALLOWED_DOCUMENT_EXTENSIONS,
            label="Additional qualification file",
            subfolder="documents",
        )
        if upload_error:
            raise ValueError(upload_error)
        user.additional_qualification_file = extra_filename

    return user

def dashboard_notifications_for(user):
    notes = []
    if user.role == "student":
        for notice in PaymentNotice.query.filter_by(student_id=user.id, status="pending").order_by(PaymentNotice.created_at.desc()).limit(5).all():
            notes.append(f"Your credit purchase notice for PKR {notice.amount_sent_pkr} is under review.")
    if user.role == "tutor":
        if user.profile_stage == "fee_pending":
            notes.append("You have a pending activation fee action. Please follow the dashboard instructions.")
        elif user.profile_stage == "under_review":
            notes.append("Your tutor profile is under admin review.")
        elif user.profile_stage == "rejected":
            notes.append("Your tutor profile was not approved yet. Please review the admin note.")
    return notes

def ensure_user_columns():
    engine = db.engine
    if engine.url.get_backend_name() != "sqlite":
        return
    wanted = {
        "previous_path_choice": "VARCHAR(50) DEFAULT ''",
        "mphil_title": "VARCHAR(255) DEFAULT ''",
        "mphil_major": "VARCHAR(255) DEFAULT ''",
        "mphil_institution": "VARCHAR(255) DEFAULT ''",
        "mphil_year": "VARCHAR(50) DEFAULT ''",
        "mphil_grade": "VARCHAR(50) DEFAULT ''",
        "mphil_additional_note": "VARCHAR(255) DEFAULT ''",
        "masters_title": "VARCHAR(255) DEFAULT ''",
        "masters_major": "VARCHAR(255) DEFAULT ''",
        "masters_institution": "VARCHAR(255) DEFAULT ''",
        "masters_year": "VARCHAR(50) DEFAULT ''",
        "masters_grade": "VARCHAR(50) DEFAULT ''",
        "masters_additional_note": "VARCHAR(255) DEFAULT ''",
        "bachelor_major": "VARCHAR(255) DEFAULT ''",
        "bachelor_additional_note": "VARCHAR(255) DEFAULT ''",
        "mobile_number": "VARCHAR(40) DEFAULT ''",
        "cnic_number": "VARCHAR(40) DEFAULT ''",
        "additional_qualification_level": "VARCHAR(80) DEFAULT ''",
        "additional_qualification_title": "VARCHAR(255) DEFAULT ''",
        "additional_qualification_major": "VARCHAR(255) DEFAULT ''",
        "additional_qualification_institution": "VARCHAR(255) DEFAULT ''",
        "additional_qualification_year": "VARCHAR(50) DEFAULT ''",
        "additional_qualification_grade": "VARCHAR(50) DEFAULT ''",
        "additional_qualification_file": "VARCHAR(255) DEFAULT ''",
        "admin_review_note": "TEXT DEFAULT ''",
        "demo_video_file": "VARCHAR(255) DEFAULT ''",
    }
    with engine.begin() as conn:
        existing = {row[1] for row in conn.execute(text("PRAGMA table_info(user)"))}
        for name, ddl in wanted.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE user ADD COLUMN {name} {ddl}"))

def ensure_default_admin():
    email = os.getenv("DEFAULT_ADMIN_EMAIL", "admin@tutorsonline.pk").strip().lower()
    password = os.getenv("DEFAULT_ADMIN_PASSWORD", "Admin@12345").strip()
    name = os.getenv("DEFAULT_ADMIN_NAME", "Super Admin").strip()

    admin = User.query.filter_by(email=email).first()

    if admin:
        changed = False

        if admin.role != "admin":
            admin.role = "admin"
            changed = True

        if not admin.full_name:
            admin.full_name = name
            changed = True

        if not admin.public_name:
            admin.public_name = name
            changed = True

        if not admin.is_active_user:
            admin.is_active_user = True
            changed = True

        # IMPORTANT:
        # Do NOT reset/re-hash password every startup.
        # Only set password if hash is missing.
        if not admin.password_hash:
            admin.password_hash = generate_password_hash(password, method="pbkdf2:sha256")
            changed = True

        if changed:
            db.session.commit()

        return admin

    admin = User(
        email=email,
        role="admin",
        full_name=name,
        public_name=name,
        is_active_user=True,
    )
    admin.password_hash = generate_password_hash(password, method="pbkdf2:sha256")
    db.session.add(admin)
    db.session.commit()
    return admin

@app.route("/admin/payment-notices")
@login_required
def admin_payment_notices():
    if current_user.role != "admin":
        flash("Unauthorized.", "danger")
        return redirect(url_for("dashboard"))

    notices = PaymentNotice.query.order_by(
        PaymentNotice.created_at.desc()
    ).all()
    normalize_payment_notices(notices)

    return render_template("admin_payment_notices.html", notices=notices)

@app.route("/admin/payment-notices/<int:notice_id>/action", methods=["POST"])
@login_required
def admin_payment_action(notice_id):
    if current_user.role != "admin":
        flash("Unauthorized.", "danger")
        return redirect(url_for("dashboard"))

    notice = PaymentNotice.query.get_or_404(notice_id)
    action = (request.form.get("action", "") or "").strip().lower()
    reason = (request.form.get("reason", "") or request.form.get("admin_note", "") or "").strip()

    if action == "approve":
        changed, message = approve_student_payment_notice(notice, reason)
        if changed:
            db.session.commit()
            safe_send_email(
                notice.student.email,
                "Credits approved - TutorsOnline.pk",
                f"""Assalam-o-Alaikum {notice.student.full_name},

Your payment notice has been approved.

Credits added: {notice.claimed_credits}
Amount received: PKR {notice.amount_sent_pkr}

{f"Admin note: {reason}" if reason else ""}

Regards,
TutorsOnline.pk
superadmin@tutorsonline.pk
""",
            )
            flash(message, "success")
        else:
            db.session.rollback()
            flash(message, "warning")
        return redirect(url_for("admin_payment_notices"))

    if action in {"decline", "reject"}:
        changed, message = decline_student_payment_notice(notice, reason)
        if changed:
            db.session.commit()
            safe_send_email(
                notice.student.email,
                "Payment notice declined - TutorsOnline.pk",
                f"""Assalam-o-Alaikum {notice.student.full_name},

Your payment notice could not be approved.

Reason: {notice.admin_note}

No credits were added to your account.

Regards,
TutorsOnline.pk
superadmin@tutorsonline.pk
""",
            )
            flash(message, "info")
        else:
            db.session.rollback()
            flash(message, "warning")
        return redirect(url_for("admin_payment_notices"))

    if action == "on_hold":
        changed, message = hold_student_payment_notice(notice, reason)
        if changed:
            db.session.commit()
            safe_send_email(
                notice.student.email,
                "Payment notice on hold - TutorsOnline.pk",
                f"""Assalam-o-Alaikum {notice.student.full_name},

Your payment notice is currently on hold for review.

{f"Admin note: {notice.admin_note}" if notice.admin_note else ""}

No credits have been added yet.

Regards,
TutorsOnline.pk
superadmin@tutorsonline.pk
""",
            )
            flash(message, "warning")
        else:
            db.session.rollback()
            flash(message, "warning")
        return redirect(url_for("admin_payment_notices"))

    flash("Invalid action.", "danger")
    return redirect(url_for("admin_payment_notices"))


@app.route("/")
def index():
    if UNDER_CONSTRUCTION:
        return render_template("under_construction.html")

    featured_tutors = (
    User.query.filter_by(
        role="tutor",
        is_verified_tutor=True,
        is_public_tutor=True,
        profile_stage="approved",
        is_active_user=True,
        )
        .order_by(User.rating_avg.desc(), User.sessions_completed.desc())
        .limit(8)
        .all()
    )

    for tutor in featured_tutors:
        tutor.next_available = get_next_available_slot(tutor)


    recent_reviews = (
        Feedback.query.order_by(Feedback.created_at.desc())
        .limit(6)
        .all()
    )
    demo_topics = [
        "Fractions for Grade 6",
        "Linear Equations for O Level",
        "Trigonometry Basics for Intermediate",
        "A Level Mechanics Demo",
        "Essay Writing Skills",
        "Chemistry Stoichiometry Primer",
    ]

    return render_template(
        "index.html",
        featured_tutors=featured_tutors,
        recent_reviews=recent_reviews,
        demo_topics=demo_topics,
    )

@app.route("/book/<int:tutor_id>", methods=["GET", "POST"])
@login_required
def book_tutor(tutor_id):
    if UNDER_CONSTRUCTION:
        return redirect(url_for("index"))

    if current_user.role != "student":
        flash("Only students can book tutors.", "danger")
        return redirect(url_for("dashboard"))

    tutor = User.query.get_or_404(tutor_id)
    if tutor.role != "tutor":
        flash("Tutor not found.", "danger")
        return redirect(url_for("tutors"))

    subject_prefill = (request.args.get("subject", "") or "").strip()
    level_prefill = (request.args.get("level", "") or "").strip()
    scheduled_at_prefill = (request.args.get("scheduled_at", "") or "").strip()

    # sensible defaults
    duration_prefill = 60
    credits_prefill = 100

    if request.method == "POST":
        form_subject = (request.form.get("subject", "") or "").strip()
        form_level = (request.form.get("class_level", "") or "").strip()
        scheduled_at_raw = (request.form.get("scheduled_at", "") or "").strip()

        try:
            duration_minutes = int(request.form.get("duration_minutes", 60))
        except Exception:
            duration_minutes = 60

        # lock credits to duration instead of trusting raw free user input
        if duration_minutes <= 30:
            duration_minutes = 30
        elif duration_minutes <= 60:
            duration_minutes = 60
        elif duration_minutes <= 90:
            duration_minutes = 90
        else:
            duration_minutes = 120

        credits_cost = {
            30: 50,
            60: 100,
            90: 150,
            120: 200,
        }[duration_minutes]

        if not form_subject or not form_level or not scheduled_at_raw:
            flash("Please complete all booking details.", "danger")
            return render_template(
                "book_tutor.html",
                tutor=tutor,
                subject_options=SUBJECT_OPTIONS,
                level_options=LEVEL_OPTIONS,
                subject_prefill=form_subject or subject_prefill,
                level_prefill=form_level or level_prefill,
                scheduled_at_prefill=scheduled_at_raw or scheduled_at_prefill,
                duration_prefill=duration_minutes,
                credits_prefill=credits_cost,
                credit_rate=app.config.get("CREDIT_RATE", 10),
                booking_success=False,
            )

        try:
            scheduled_at = datetime.strptime(scheduled_at_raw, "%Y-%m-%dT%H:%M")
        except ValueError:
            flash("Invalid date/time selected.", "danger")
            return render_template(
                "book_tutor.html",
                tutor=tutor,
                subject_options=SUBJECT_OPTIONS,
                level_options=LEVEL_OPTIONS,
                subject_prefill=form_subject or subject_prefill,
                level_prefill=form_level or level_prefill,
                scheduled_at_prefill=scheduled_at_raw or scheduled_at_prefill,
                duration_prefill=duration_minutes,
                credits_prefill=credits_cost,
                credit_rate=app.config.get("CREDIT_RATE", 10),
                booking_success=False,
            )

        if scheduled_at <= datetime.now():
            flash("Please choose a future date and time.", "danger")
            return render_template(
                "book_tutor.html",
                tutor=tutor,
                subject_options=SUBJECT_OPTIONS,
                level_options=LEVEL_OPTIONS,
                subject_prefill=form_subject or subject_prefill,
                level_prefill=form_level or level_prefill,
                scheduled_at_prefill=scheduled_at_raw or scheduled_at_prefill,
                duration_prefill=duration_minutes,
                credits_prefill=credits_cost,
                credit_rate=app.config.get("CREDIT_RATE", 10),
                booking_success=False,
            )

        if current_user.credits_balance < credits_cost:
            flash("Insufficient credits. Please top up first.", "danger")
            return redirect(url_for("buy_credits"))

        booking = Booking(
            student_id=current_user.id,
            tutor_id=tutor.id,
            subject=form_subject,
            class_level=form_level,
            scheduled_at=scheduled_at,
            duration_minutes=duration_minutes,
            credits_cost=credits_cost,
            status="scheduled",
        )

        add_credits(
            current_user,
            -credits_cost,
            "session_deduction",
            f"Booking with {tutor.public_name}",
        )

        db.session.add(booking)
        db.session.commit()

        # auto-create / reuse conversation linked to this booking
        convo = get_or_create_conversation(
            student_id=current_user.id,
            tutor_id=tutor.id,
            booking_id=booking.id
        )
        db.session.commit()

        send_booking_emails(booking)

        return render_template(
            "book_tutor.html",
            tutor=tutor,
            subject_options=SUBJECT_OPTIONS,
            level_options=LEVEL_OPTIONS,
            subject_prefill=form_subject,
            level_prefill=form_level,
            scheduled_at_prefill=scheduled_at_raw,
            duration_prefill=duration_minutes,
            credits_prefill=credits_cost,
            credit_rate=app.config.get("CREDIT_RATE", 10),
            booking_success=True,
            booked_session={
                "subject": form_subject,
                "class_level": form_level,
                "scheduled_at_label": scheduled_at.strftime("%d %b %Y, %I:%M %p"),
                "duration_minutes": duration_minutes,
                "credits_cost": credits_cost,
            },
        )

    return render_template(
        "book_tutor.html",
        tutor=tutor,
        subject_options=SUBJECT_OPTIONS,
        level_options=LEVEL_OPTIONS,
        subject_prefill=subject_prefill,
        level_prefill=level_prefill,
        scheduled_at_prefill=scheduled_at_prefill,
        duration_prefill=duration_prefill,
        credits_prefill=credits_prefill,
        credit_rate=app.config.get("CREDIT_RATE", 10),
        booking_success=False,
    )

@app.route("/booking/<int:booking_id>/cancel", methods=["POST"])
@login_required
def cancel_booking(booking_id):
    booking = Booking.query.get_or_404(booking_id)

    if current_user.id not in [booking.student_id, booking.tutor_id]:
        flash("Unauthorized.", "danger")
        return redirect(url_for("dashboard"))

    if booking.status not in ["scheduled", "confirmed"]:
        flash("This booking can no longer be cancelled.", "warning")
        return redirect(url_for("dashboard"))

    booking.status = "cancelled"

    add_credits(
        booking.student,
        booking.credits_cost,
        "booking_refund",
        f"Refund for cancelled booking #{booking.id}"
    )

    db.session.commit()
    flash("Booking cancelled and credits refunded.", "success")
    return redirect(url_for("dashboard"))


@app.route("/register")
def register():
    return render_template("register_choice.html")

@app.route("/register/student", methods=["GET", "POST"])
def register_student():
    form_data = request.form.to_dict(flat=True) if request.method == "POST" else {}
    selected_student_subjects = request.form.getlist("student_subject_needed") if request.method == "POST" else []

    if request.method == "POST":
        email = (request.form.get("email", "") or "").strip().lower()

        if User.query.filter_by(email=email).first():
            flash("Email already registered. Please log in instead.", "danger")
        else:
            error = validate_option_a_student_form(request.form)
            if error:
                flash(error, "danger")
            else:
                try:
                    user = build_user_from_option_a_form(request.form, request.files)
                    user.email = email
                except ValueError as exc:
                    flash(str(exc), "danger")
                    return render_template(
                        "register_student.html",
                        form_data=form_data,
                        selected_student_subjects=selected_student_subjects,
                        subject_options=SUBJECT_OPTIONS,
                        level_options=LEVEL_OPTIONS,
                    )

                try:
                    db.session.add(user)
                    db.session.commit()
                except IntegrityError:
                    db.session.rollback()
                    flash("Email already registered. Please log in instead.", "danger")
                    return render_template(
                        "register_student.html",
                        form_data=form_data,
                        selected_student_subjects=selected_student_subjects,
                        subject_options=SUBJECT_OPTIONS,
                        level_options=LEVEL_OPTIONS,
                    )

                send_signup_emails(user)
                flash("Registration completed successfully. Please log in.", "success")
                return redirect(url_for("login"))

    return render_template(
        "register_student.html",
        form_data=form_data,
        selected_student_subjects=selected_student_subjects,
        subject_options=SUBJECT_OPTIONS,
        level_options=LEVEL_OPTIONS,
    )



@app.route("/register/tutor", methods=["GET", "POST"])
def register_tutor():
    form_data = request.form.to_dict(flat=True) if request.method == "POST" else {}
    selected_tutor_subjects = request.form.getlist("main_subject") if request.method == "POST" else []
    selected_tutor_levels = request.form.getlist("class_levels") if request.method == "POST" else []
    form_data.setdefault("active_step", request.args.get("step", "1"))

    if request.method == "POST":
        email = (request.form.get("email", "") or "").strip().lower()

        if User.query.filter_by(email=email).first():
            flash("Email already registered. Please log in instead.", "danger")
        else:
            error = validate_tutor_application_form(request.form)
            if error:
                flash(error, "danger")
                form_data["active_step"] = request.form.get("active_step", "4")
            else:
                try:
                    user = build_user_from_option_a_form(request.form, request.files)
                    user.email = email
                except ValueError as exc:
                    flash(str(exc), "danger")
                    form_data["active_step"] = request.form.get("active_step", "4")
                    return render_template(
                        "register_tutor.html",
                        form_data=form_data,
                        selected_tutor_subjects=selected_tutor_subjects,
                        selected_tutor_levels=selected_tutor_levels,
                        subject_options=SUBJECT_OPTIONS,
                        level_options=LEVEL_OPTIONS,
                    )

                try:
                    db.session.add(user)
                    db.session.commit()
                except IntegrityError:
                    db.session.rollback()
                    flash("Email already registered. Please log in instead.", "danger")
                    return render_template(
                        "register_tutor.html",
                        form_data=form_data,
                        selected_tutor_subjects=selected_tutor_subjects,
                        selected_tutor_levels=selected_tutor_levels,
                        subject_options=SUBJECT_OPTIONS,
                        level_options=LEVEL_OPTIONS,
                    )

                send_signup_emails(user)
                flash("Tutor application submitted. If selected after review, you will be asked to pay PKR 500 to activate your profile.", "success")
                return redirect(url_for("login"))

    return render_template(
        "register_tutor.html",
        form_data=form_data,
        selected_tutor_subjects=selected_tutor_subjects,
        selected_tutor_levels=selected_tutor_levels,
        subject_options=SUBJECT_OPTIONS,
        level_options=LEVEL_OPTIONS,
    )

@app.route("/google-login")
def google_login():
    if not app.config["GOOGLE_CLIENT_ID"]:
        flash("Google login not configured.", "danger")
        return redirect(url_for("login"))
    redirect_uri = app.config["GOOGLE_REDIRECT_URI"] or url_for("google_callback", _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route("/login/google/callback")
def google_callback():
    token = google.authorize_access_token()
    user_info = token.get("userinfo") or {}

    email = user_info.get("email", "").lower().strip()
    name = user_info.get("name", "").strip() or email.split("@")[0]

    if not email:
        flash("Google account did not return an email address.", "danger")
        return redirect(url_for("login"))

    user = User.query.filter_by(email=email).first()
    if user:
        login_user(user)
        flash("Logged in with Google.", "success")
        next_page = request.args.get("next")
        if user.role == "admin":
            return redirect(url_for("admin_dashboard"))
        return redirect(next_page or url_for("dashboard"))

    session["google_signup"] = {"email": email, "name": name}
    return redirect(url_for("complete_google_signup"))

@app.route("/complete-google-signup")
def complete_google_signup():
    google_signup = session.get("google_signup")
    if not google_signup:
        flash("Your Google signup session expired. Please try again.", "warning")
        return redirect(url_for("login"))

    existing_user = User.query.filter_by(email=google_signup["email"]).first()
    if existing_user:
        login_user(existing_user)
        flash("Account already exists. Logged in successfully.", "success")
        if existing_user.role == "admin":
            return redirect(url_for("admin_dashboard"))
        return redirect(url_for("dashboard"))

    return render_template("register_choice.html", google_signup=True)

@app.route("/complete-google-signup/student", methods=["GET", "POST"])
def complete_google_signup_student():
    google_signup = session.get("google_signup")
    if not google_signup:
        flash("Your Google signup session expired. Please try again.", "warning")
        return redirect(url_for("login"))

    form_data = request.form.to_dict(flat=True) if request.method == "POST" else {}
    selected_student_subjects = request.form.getlist("student_subject_needed") if request.method == "POST" else []
    email = google_signup["email"]
    fallback_name = google_signup["name"]

    if request.method == "POST":
        error = validate_option_a_student_form(request.form)
        if error:
            flash(error, "danger")
        else:
            try:
                user = build_user_from_option_a_form(request.form, request.files, google_email=email, google_name=fallback_name)
            except ValueError as exc:
                flash(str(exc), "danger")
                return render_template(
                    "google_complete_student.html",
                    form_data=form_data,
                    selected_student_subjects=selected_student_subjects,
                    subject_options=SUBJECT_OPTIONS,
                    level_options=LEVEL_OPTIONS,
                    google_email=email,
                    google_name=fallback_name,
                )
            db.session.add(user)
            db.session.commit()
            send_signup_emails(user)
            session.pop("google_signup", None)
            login_user(user)
            flash("Google signup completed successfully.", "success")
            return redirect(url_for("dashboard"))

    return render_template(
        "google_complete_student.html",
        form_data=form_data,
        selected_student_subjects=selected_student_subjects,
        subject_options=SUBJECT_OPTIONS,
        level_options=LEVEL_OPTIONS,
        google_email=email,
        google_name=fallback_name,
    )

@app.route("/complete-google-signup/tutor", methods=["GET", "POST"])
def complete_google_signup_tutor():
    google_signup = session.get("google_signup")
    if not google_signup:
        flash("Your Google signup session expired. Please try again.", "warning")
        return redirect(url_for("login"))

    form_data = request.form.to_dict(flat=True) if request.method == "POST" else {}
    selected_tutor_subjects = request.form.getlist("main_subject") if request.method == "POST" else []
    selected_tutor_levels = request.form.getlist("class_levels") if request.method == "POST" else []
    form_data.setdefault("active_step", request.args.get("step", "1"))
    email = google_signup["email"]
    fallback_name = google_signup["name"]

    if request.method == "POST":
        error = validate_tutor_application_form(request.form)
        if error:
            flash(error, "danger")
            form_data["active_step"] = request.form.get("active_step", "4")
        else:
            try:
                user = build_user_from_option_a_form(request.form, request.files, google_email=email, google_name=fallback_name)
            except ValueError as exc:
                flash(str(exc), "danger")
                form_data["active_step"] = request.form.get("active_step", "4")
                return render_template(
                    "google_complete_tutor.html",
                    form_data=form_data,
                    selected_tutor_subjects=selected_tutor_subjects,
                    selected_tutor_levels=selected_tutor_levels,
                    subject_options=SUBJECT_OPTIONS,
                    level_options=LEVEL_OPTIONS,
                    google_email=email,
                    google_name=fallback_name,
                )
            db.session.add(user)
            db.session.commit()
            send_signup_emails(user)
            session.pop("google_signup", None)
            login_user(user)
            flash("Google signup completed. Tutor profile created and sent for review.", "success")
            return redirect(url_for("dashboard"))

    return render_template(
        "google_complete_tutor.html",
        form_data=form_data,
        selected_tutor_subjects=selected_tutor_subjects,
        selected_tutor_levels=selected_tutor_levels,
        subject_options=SUBJECT_OPTIONS,
        level_options=LEVEL_OPTIONS,
        google_email=email,
        google_name=fallback_name,
    )

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = (request.form.get("email", "") or "").strip().lower()
        password = request.form.get("password", "")

        if login_rate_limited(email):
            flash("Too many failed login attempts. Please wait a few minutes and try again.", "danger")
            return redirect(url_for("login"))

        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            clear_login_failures(email)
            login_user(user)
            flash("Logged in successfully.", "success")
            if user.role == "admin":
                return redirect(url_for("admin_dashboard"))
            return redirect(safe_next_url("dashboard"))

        record_login_failure(email)
        flash("Invalid credentials.", "danger")
        return redirect(url_for("login"))
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Logged out.", "info")
    return redirect(url_for("index"))


@app.route("/dashboard")
@login_required
def dashboard():
    if current_user.role == "admin":
        return redirect(url_for("admin_dashboard"))

    bookings = (
        Booking.query.filter(
            ((Booking.student_id == current_user.id) | (Booking.tutor_id == current_user.id))
        )
        .order_by(Booking.scheduled_at.desc())
        .limit(10)
        .all()
    )
    pending_notices = []
    if current_user.role == "student":
        pending_notices = (
            PaymentNotice.query.filter(
                PaymentNotice.student_id == current_user.id,
                PaymentNotice.status.in_(PAYMENT_REVIEWABLE_STATUSES),
            )
            .order_by(PaymentNotice.created_at.desc())
            .all()
        )
        normalize_payment_notices(pending_notices)

    completion_data = None
    if current_user.role == "tutor":
        sync_tutor_stage(current_user)
        completion_data = get_tutor_completion_status(current_user)

    notifications = dashboard_notifications_for(current_user)

    recent_conversations = []

    if current_user.role == "student":
        recent_conversations = (
            Conversation.query
            .filter_by(student_id=current_user.id)
            .order_by(Conversation.updated_at.desc())
            .limit(5)
            .all()
        )

    elif current_user.role == "tutor":
        recent_conversations = (
            Conversation.query
            .filter_by(tutor_id=current_user.id)
            .order_by(Conversation.updated_at.desc())
            .limit(5)
            .all()
        )

    return render_template(
        "dashboard.html",
        bookings=bookings,
        pending_notices=pending_notices,
        completion_data=completion_data,
        notifications=notifications,
        recent_conversations=recent_conversations,
        tutor_fee_instructions="Deposit PKR 500 to the instructed account and wait for admin confirmation.",
    )

@app.route("/profile")
@login_required
def profile():
    return render_template("profile.html")


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        current_user.full_name = (request.form.get("full_name", "") or "").strip() or current_user.full_name
        current_user.public_name = (request.form.get("public_name", "") or "").strip() or current_user.public_name
        current_user.mobile_number = (request.form.get("mobile_number", "") or "").strip()
        current_user.city = (request.form.get("city", "") or "").strip()
        current_user.bio = (request.form.get("bio", "") or "").strip()

        if current_user.role == "tutor":
            current_user.demo_video_url = (request.form.get("demo_video_url", "") or "").strip()
            current_user.audio_only = bool(request.form.get("audio_only"))
            current_user.modest_profile = bool(request.form.get("modest_profile"))

        profile_image = request.files.get("profile_image")
        if profile_image and profile_image.filename:
            image_name, upload_error = save_profile_image(profile_image, prefix=f"profile_u{current_user.id}")
            if upload_error:
                flash(upload_error, "danger")
                return redirect(url_for("settings"))
            current_user.profile_image = image_name

        if current_user.role == "tutor":
            demo_video = request.files.get("demo_video_file")
            if demo_video and demo_video.filename:
                video_name, upload_error = save_upload(
                    demo_video,
                    prefix=f"demo_u{current_user.id}",
                    allowed_extensions=ALLOWED_VIDEO_EXTENSIONS,
                    label="Demo video",
                    subfolder="demo_videos",
                )
                if upload_error:
                    flash(upload_error, "danger")
                    return redirect(url_for("settings"))
                current_user.demo_video_file = video_name

        db.session.commit()
        flash("Settings updated successfully.", "success")
        return redirect(url_for("settings"))

    availability_rules = []
    availability_exceptions = []

    if current_user.role == "tutor":
        availability_rules = (
            TutorAvailabilityRule.query
            .filter_by(tutor_id=current_user.id)
            .order_by(TutorAvailabilityRule.weekday.asc(), TutorAvailabilityRule.start_time.asc())
            .all()
        )
        availability_exceptions = (
            TutorAvailabilityException.query
            .filter_by(tutor_id=current_user.id)
            .order_by(TutorAvailabilityException.exception_date.desc())
            .all()
        )

    return render_template(
        "settings.html",
        availability_rules=availability_rules,
        availability_exceptions=availability_exceptions,
    )

@app.route("/settings/availability/add", methods=["POST"])
@login_required
def add_availability_rule():
    if current_user.role != "tutor":
        flash("Only tutors can manage availability.", "danger")
        return redirect(url_for("settings"))

    weekday_raw = (request.form.get("weekday", "") or "").strip()
    start_time = (request.form.get("start_time", "") or "").strip()
    end_time = (request.form.get("end_time", "") or "").strip()
    slot_minutes_raw = (request.form.get("slot_minutes", "") or "60").strip()

    if weekday_raw == "" or not start_time or not end_time:
        flash("Please fill weekday, start time, and end time.", "danger")
        return redirect(url_for("settings"))

    try:
        weekday = int(weekday_raw)
        slot_minutes = int(slot_minutes_raw or 60)
    except ValueError:
        flash("Invalid availability values.", "danger")
        return redirect(url_for("settings"))

    if weekday < 0 or weekday > 6:
        flash("Weekday must be between 0 and 6.", "danger")
        return redirect(url_for("settings"))

    rule = TutorAvailabilityRule(
        tutor_id=current_user.id,
        weekday=weekday,
        start_time=start_time,
        end_time=end_time,
        slot_minutes=slot_minutes if slot_minutes > 0 else 60,
        is_active=True,
    )
    db.session.add(rule)
    db.session.commit()

    flash("Availability rule added.", "success")
    return redirect(url_for("settings"))


@app.route("/settings/availability/<int:rule_id>/delete", methods=["POST"])
@login_required
def delete_availability_rule(rule_id):
    if current_user.role != "tutor":
        flash("Only tutors can manage availability.", "danger")
        return redirect(url_for("settings"))

    rule = TutorAvailabilityRule.query.get_or_404(rule_id)
    if rule.tutor_id != current_user.id:
        flash("Unauthorized.", "danger")
        return redirect(url_for("settings"))

    db.session.delete(rule)
    db.session.commit()

    flash("Availability rule deleted.", "success")
    return redirect(url_for("settings"))

@app.route("/settings/availability/preset", methods=["POST"])
@login_required
def apply_availability_preset():
    if current_user.role != "tutor":
        flash("Only tutors can manage availability.", "danger")
        return redirect(url_for("settings"))

    preset = (request.form.get("preset", "") or "").strip()

    preset_map = {
        "weekdays_9_5": [
            (0, "09:00", "17:00"),
            (1, "09:00", "17:00"),
            (2, "09:00", "17:00"),
            (3, "09:00", "17:00"),
            (4, "09:00", "17:00"),
        ],
        "weekends_10_8": [
            (5, "10:00", "20:00"),
            (6, "10:00", "20:00"),
        ],
        "allweek_9_5": [
            (0, "09:00", "17:00"),
            (1, "09:00", "17:00"),
            (2, "09:00", "17:00"),
            (3, "09:00", "17:00"),
            (4, "09:00", "17:00"),
            (5, "09:00", "17:00"),
            (6, "09:00", "17:00"),
        ],
        "allweek_6_10": [
            (0, "18:00", "22:00"),
            (1, "18:00", "22:00"),
            (2, "18:00", "22:00"),
            (3, "18:00", "22:00"),
            (4, "18:00", "22:00"),
            (5, "18:00", "22:00"),
            (6, "18:00", "22:00"),
        ],
    }

    if preset not in preset_map:
        flash("Invalid preset selected.", "danger")
        return redirect(url_for("settings"))

    slot_minutes_raw = (request.form.get("slot_minutes", "") or "60").strip()
    try:
        slot_minutes = int(slot_minutes_raw or 60)
    except ValueError:
        slot_minutes = 60

    for weekday, start_time, end_time in preset_map[preset]:
        existing = TutorAvailabilityRule.query.filter_by(
            tutor_id=current_user.id,
            weekday=weekday,
            start_time=start_time,
            end_time=end_time,
        ).first()

        if not existing:
            db.session.add(
                TutorAvailabilityRule(
                    tutor_id=current_user.id,
                    weekday=weekday,
                    start_time=start_time,
                    end_time=end_time,
                    slot_minutes=slot_minutes if slot_minutes > 0 else 60,
                    is_active=True,
                )
            )

    db.session.commit()
    flash("Availability preset applied. You can now tailor individual days if needed.", "success")
    return redirect(url_for("settings"))

@app.route("/tutors")
def tutors():
    level = request.args.get("level", "").strip()
    subject = request.args.get("subject", "").strip()

    if subject == "other":
        subject = request.args.get("subject_other", "").strip()
    if level == "other":
        level = request.args.get("level_other", "").strip()

    query = User.query.filter_by(
        role="tutor",
        is_verified_tutor=True,
        is_public_tutor=True,
        profile_stage="approved",
        is_active_user=True,
    )

    if level:
        query = query.filter(User.class_levels.ilike(f"%{level}%"))
    if subject:
        query = query.filter(User.subjects.ilike(f"%{subject}%"))

    tutors_list = query.all()

    for tutor in tutors_list:
        tutor.next_available = get_next_available_slot(tutor)

    return render_template(
        "tutors.html",
        tutors=tutors_list,
        level=level,
        subject=subject,
        subject_options=SUBJECT_OPTIONS,
        level_options=LEVEL_OPTIONS,
    )

@app.route("/tutors/<int:tutor_id>", methods=["GET", "POST"])
def tutor_profile(tutor_id):
    if UNDER_CONSTRUCTION:
        return redirect(url_for("index"))

    tutor = User.query.get_or_404(tutor_id)
    if tutor.role != "tutor":
        flash("Tutor not found.", "danger")
        return redirect(url_for("tutors"))

    if request.method == "POST":
        if not current_user.is_authenticated or current_user.role != "student":
            flash("Only students can leave feedback.", "danger")
            return redirect(url_for("login"))

        booking_id = int(request.form["booking_id"])
        booking = Booking.query.get_or_404(booking_id)
        if booking.student_id != current_user.id or booking.tutor_id != tutor.id:
            flash("Invalid booking.", "danger")
            return redirect(url_for("tutor_profile", tutor_id=tutor.id))

        feedback = Feedback(
            booking_id=booking.id,
            tutor_id=tutor.id,
            student_id=current_user.id,
            rating=int(request.form["rating"]),
            punctuality=int(request.form["punctuality"]),
            explanation=int(request.form["explanation"]),
            professionalism=int(request.form["professionalism"]),
            comment=request.form.get("comment", "").strip(),
        )
        db.session.add(feedback)
        db.session.flush()

        tutor.rating_count += 1
        tutor.rating_avg = round(
            ((tutor.rating_avg * (tutor.rating_count - 1)) + feedback.rating)
            / tutor.rating_count,
            2,
        )

        if tutor.rating_avg >= 4.7 and not any(
            b.bonus_type == "rating_bonus_4_7" for b in tutor.bonus_entries
        ):
            tutor.bonus_credits += 100
            add_credits(tutor, 100, "bonus", "Rating bonus for maintaining 4.7+")
            db.session.add(
                TutorBonus(
                    tutor_id=tutor.id,
                    bonus_type="rating_bonus_4_7",
                    credits_awarded=100,
                    note="Rating bonus",
                )
            )

        db.session.commit()
        flash("Feedback submitted.", "success")
        return redirect(url_for("tutor_profile", tutor_id=tutor.id))

    completed_bookings = []
    if current_user.is_authenticated and current_user.role == "student":
        completed_bookings = Booking.query.filter_by(
            student_id=current_user.id,
            tutor_id=tutor.id,
            status="completed",
        ).all()

    youtube_embed = get_youtube_embed(tutor.demo_video_url)
    schedule_ctx = get_tutor_schedule_context(tutor)

    context = dict(
    tutor=tutor,
    completed_bookings=completed_bookings,
    youtube_embed=youtube_embed,
    **schedule_ctx,
    )

    if is_htmx_like_request() and request.args.get("fragment") == "schedule":
        return render_template("partials/tutor_schedule_section.html", **context)

    return render_template("tutor_profile.html", **context)

@app.route("/submit-tutor", methods=["POST"])
@login_required
def submit_tutor_for_review():
    if current_user.role != "tutor":
        return redirect(url_for("dashboard"))

    data = get_tutor_completion_status(current_user)

    if data["completion"] < 100:
        flash("Complete your profile before submission", "error")
        return redirect(url_for("dashboard"))

    current_user.profile_stage = "under_review"
    current_user.verification_submitted_at = datetime.utcnow()

    db.session.commit()

    flash("Submitted for admin review", "success")
    return redirect(url_for("dashboard"))

@app.route("/terms")
def terms():
    return render_template("terms.html")

@app.route("/privacy", endpoint="privacy")
@app.route("/privacy-policy", endpoint="privacy_policy")
def privacy_policy():
    return render_template("privacy.html")


@app.route("/admin/credits")
@login_required
def admin_credit_notices():
    if current_user.role != "admin":
        return redirect(url_for("dashboard"))

    return redirect(url_for("admin_payment_notices"))


@app.route("/admin/credits/<int:notice_id>/review", methods=["POST"])
@login_required
def admin_review_credit_notice(notice_id):
    if current_user.role != "admin":
        return redirect(url_for("dashboard"))

    notice = PaymentNotice.query.get_or_404(notice_id)
    action = (request.form.get("action", "") or "").strip().lower()
    reason = (request.form.get("reason", "") or request.form.get("admin_note", "") or "").strip()

    if action == "approve":
        changed, message = approve_student_payment_notice(notice, reason)
        category = "success" if changed else "warning"
    elif action in {"decline", "reject"}:
        changed, message = decline_student_payment_notice(notice, reason)
        category = "info" if changed else "warning"
    elif action == "on_hold":
        changed, message = hold_student_payment_notice(notice, reason)
        category = "warning"
    else:
        changed = False
        message = "Invalid action."
        category = "danger"

    if changed:
        db.session.commit()
    else:
        db.session.rollback()

    flash(message, category)
    return redirect(url_for("admin_payment_notices"))



@app.route("/accessibility")
def accessibility():
    return render_template("accessibility.html")


@app.route("/about")
def about():
    return render_template("about.html")

@app.route("/buy-credits", methods=["GET", "POST"])
@login_required
def buy_credits():
    if current_user.role != "student":
        flash("Only students can buy credits.", "danger")
        return redirect(url_for("dashboard"))

    form_data = request.form.to_dict(flat=True) if request.method == "POST" else {}
    credit_rate = app.config.get("CREDIT_RATE", 10)

    if request.method == "POST":
        selected = (request.form.get("credits_requested", "") or "").strip()

        try:
            if selected == "other":
                credits = int((request.form.get("credits_requested_other") or "0").strip())
            else:
                credits = int(selected or 0)
        except ValueError:
            flash("Please enter a valid credit amount.", "danger")
            return render_template(
                "buy_credits_v2.html",
                form_data=form_data,
                credit_rate=credit_rate,
            )

        if credits < 10:
            flash("Minimum purchase is 10 credits.", "danger")
            return render_template(
                "buy_credits_v2.html",
                form_data=form_data,
                credit_rate=credit_rate,
            )

        if request.form.get("payment_confirmed", "").strip() != "yes":
            flash("Please confirm the transfer before submitting.", "danger")
            return render_template(
                "buy_credits_v2.html",
                form_data=form_data,
                credit_rate=credit_rate,
            )

        sender_name = (request.form.get("sender_name", "") or "").strip()
        sender_account = (request.form.get("sender_account", "") or "").strip()
        transfer_method = (request.form.get("transfer_method", "easypaisa") or "easypaisa").strip()

        if not sender_name or not sender_account:
            flash("Please enter the sender name and account/mobile number.", "danger")
            return render_template(
                "buy_credits_v2.html",
                form_data=form_data,
                credit_rate=credit_rate,
            )

        # Prevent duplicate active notices for the same student/amount before admin review
        existing_pending = PaymentNotice.query.filter(
            PaymentNotice.student_id == current_user.id,
            PaymentNotice.claimed_credits == credits,
            PaymentNotice.status.in_(PAYMENT_REVIEWABLE_STATUSES),
        ).order_by(PaymentNotice.created_at.desc()).first()

        if existing_pending:
            flash(
                "You already have a payment notice for this credit request under admin review.",
                "warning",
            )
            return redirect(url_for("student_wallet"))

        screenshot = request.files.get("screenshot")
        if not screenshot or not screenshot.filename:
            flash("Please attach transfer screenshot.", "danger")
            return render_template(
                "buy_credits_v2.html",
                form_data=form_data,
                credit_rate=credit_rate,
            )

        amount = credits * credit_rate
        filename, upload_error = save_upload(
            screenshot,
            prefix="payment",
            allowed_extensions=ALLOWED_DOCUMENT_EXTENSIONS,
            label="Transfer screenshot",
            subfolder="payments",
        )
        if upload_error:
            flash(upload_error, "danger")
            return render_template(
                "buy_credits_v2.html",
                form_data=form_data,
                credit_rate=credit_rate,
            )

        notice = PaymentNotice(
            student_id=current_user.id,
            amount_sent_pkr=amount,
            claimed_credits=credits,
            sender_name=sender_name,
            sender_account=sender_account,
            transfer_method=transfer_method,
            screenshot_filename=filename,
            note=(request.form.get("note", "") or "").strip(),
            status="pending",
        )
        db.session.add(notice)
        db.session.commit()

        # IMPORTANT: No credits are added here.
        # Credits must only be granted from admin approval route.
        safe_send_email(
            "superadmin@tutorsonline.pk",
            "TutorsOnline.pk Credit Purchase Notice",
            f"""Student: {current_user.full_name} ({current_user.email})
Requested credits: {credits}
Amount: PKR {amount}
Sender: {notice.sender_name}
Account: {notice.sender_account}
Method: {notice.transfer_method}
Notice ID: {notice.id}
Screenshot: {filename}

Status: pending
Action required: Admin approval or decline
""",
        )

        flash(
            "Payment notice submitted successfully. Your credits will be added only after admin approval.",
            "success",
        )
        return redirect(url_for("student_wallet"))

    return render_template(
        "buy_credits_v2.html",
        form_data=form_data,
        credit_rate=credit_rate,
    )

@app.route("/wallet")

@login_required
def student_wallet():
    if current_user.role == "student":
        txs = (
            CreditTransaction.query.filter_by(user_id=current_user.id)
            .order_by(CreditTransaction.created_at.desc())
            .all()
        )
        notices = (
            PaymentNotice.query.filter_by(student_id=current_user.id)
            .order_by(PaymentNotice.created_at.desc())
            .all()
        )
        normalize_payment_notices(notices)
        return render_template("student_wallet.html", txs=txs, notices=notices)

    txs = (
        CreditTransaction.query.filter_by(user_id=current_user.id)
        .order_by(CreditTransaction.created_at.desc())
        .all()
    )
    return render_template("tutor_wallet.html", txs=txs)

def booking_time_window_status(booking, early_minutes=10, late_minutes=10):
    now = datetime.now()
    start = booking.scheduled_at
    end = booking.scheduled_at + timedelta(minutes=booking.duration_minutes or 60)

    allowed_start = start - timedelta(minutes=early_minutes)
    allowed_end = end + timedelta(minutes=late_minutes)

    if now < allowed_start:
        return "too_early", allowed_start, allowed_end

    if now > allowed_end:
        return "expired", allowed_start, allowed_end

    return "open", allowed_start, allowed_end


def mark_booking_complete_for_user(booking, user):
    if user.role == "admin":
        return False, "Admin observers cannot mark sessions complete."

    if booking.status == "cancelled":
        return False, "Cancelled bookings cannot be marked complete."

    if booking.status not in ["scheduled", "confirmed", "live", "completed"]:
        return False, "This booking cannot be marked complete."

    if user.id == booking.student_id:
        booking.student_marked_complete = True
    elif user.id == booking.tutor_id:
        booking.tutor_marked_complete = True
    else:
        return False, "Unauthorized."

    if booking.student_marked_complete and booking.tutor_marked_complete:
        booking.status = "completed"
        return True, "Both sides confirmed completion. Admin will release tutor payout."

    other_side = "tutor" if user.id == booking.student_id else "student"
    return True, f"You marked the session complete. Waiting for the {other_side} to confirm."


@app.route("/live/<int:booking_id>", methods=["GET"])
@login_required
def live_session(booking_id):
    booking = Booking.query.get_or_404(booking_id)

    if current_user.role != "admin" and current_user.id not in [booking.student_id, booking.tutor_id]:
        flash("Unauthorized.", "danger")
        return redirect(url_for("dashboard"))

    if booking.status == "cancelled":
        flash("This booking has been cancelled.", "warning")
        return redirect(url_for("dashboard"))

    if booking.status == "completed" and current_user.role != "admin":
        flash("This session is already completed.", "info")
        return redirect(url_for("dashboard"))

    window_status, allowed_start, allowed_end = booking_time_window_status(booking)

    if current_user.role != "admin" and window_status == "too_early":
        flash(
            f"Session will open 10 minutes before scheduled time: {booking.scheduled_at.strftime('%d %b %Y, %I:%M %p')}",
            "warning",
        )
        return redirect(url_for("dashboard"))

    if current_user.role != "admin" and window_status == "expired":
        flash("This session time has expired.", "warning")
        return redirect(url_for("dashboard"))

    log = LiveSessionLog.query.filter_by(booking_id=booking.id).first()

    return render_template(
        "live_session.html",
        booking=booking,
        log=log,
        window_status=window_status,
        allowed_start=allowed_start,
        allowed_end=allowed_end,
    )


@app.route("/admin/bookings/<int:booking_id>/release-payout", methods=["POST"])
@login_required
def admin_release_payout(booking_id):
    if current_user.role != "admin":
        flash("Unauthorized.", "danger")
        return redirect(url_for("dashboard"))

    booking = Booking.query.get_or_404(booking_id)

    if booking.status != "completed":
        flash("Tutor credits can only be approved after session is completed.", "warning")
        return redirect(url_for("admin_dashboard"))

    if booking.payout_released:
        flash("Tutor credits already approved for this booking.", "warning")
        return redirect(url_for("admin_dashboard"))

    tutor = booking.tutor
    tutor_share_credits = int(booking.credits_cost * 0.8)
    tutor_share_pkr = tutor_share_credits * app.config["CREDIT_RATE"]

    before_credits = tutor.credits_balance
    before_pending = tutor.pending_payout_pkr or 0

    add_credits(
        tutor,
        tutor_share_credits,
        "tutor_session_earning",
        f"Admin-approved tutor earning for booking #{booking.id}",
        rupees=tutor_share_pkr,
    )

    tutor.pending_payout_pkr = before_pending + tutor_share_pkr
    tutor.total_earnings_pkr += tutor_share_pkr
    tutor.monthly_earnings_pkr += tutor_share_pkr
    tutor.sessions_completed += 1
    booking.payout_released = True

    # Do not call bonus here
    # apply_bonus_if_eligible(tutor)

    db.session.commit()

    flash(
        f"Approved {tutor_share_credits} tutor credits / PKR {tutor_share_pkr}. "
        f"Tutor credits: {before_credits} → {tutor.credits_balance}. "
        f"Withdrawable payout: PKR {before_pending} → PKR {tutor.pending_payout_pkr}.",
        "success",
    )
    return redirect(url_for("admin_dashboard"))

@app.route("/bookings/<int:booking_id>/complete/student", methods=["POST"])
@login_required
def complete_student(booking_id):
    booking = Booking.query.get_or_404(booking_id)

    ok, message = mark_booking_complete_for_user(booking, current_user)
    if not ok:
        flash(message, "danger" if message == "Unauthorized." else "warning")
        return redirect(url_for("dashboard"))

    db.session.commit()
    flash(message, "success")
    return redirect(url_for("dashboard"))


@app.route("/admin/reset-demo-passwords")
@login_required
def reset_demo_passwords():
    if current_user.role != "admin":
        flash("Unauthorized.", "danger")
        return redirect(url_for("dashboard"))

    users = User.query.filter(
        User.email.like("demo_%@test.com")
    ).all()

    for user in users:
        user.set_password("Demo@12345")

    db.session.commit()

    flash(f"Reset {len(users)} demo users to Demo@12345", "success")
    return redirect(url_for("admin_users"))

@app.route("/withdraw", methods=["POST"])
@login_required
def withdraw():
    if current_user.role != "tutor":
        flash("Only tutors can request withdrawals.", "danger")
        return redirect(url_for("dashboard"))

    try:
        amount = int((request.form.get("amount_pkr") or "0").strip())
    except ValueError:
        amount = 0

    if amount <= 0:
        flash("Please enter a valid withdrawal amount.", "danger")
        return redirect(url_for("student_wallet"))

    available_payout = current_user.pending_payout_pkr or 0
    if amount > available_payout:
        flash("Insufficient withdrawable payout.", "danger")
        return redirect(url_for("student_wallet"))

    payout_method = (request.form.get("payout_method") or current_user.payout_method or "bank").strip()
    payout_account_title = (
        request.form.get("payout_account_title") or current_user.payout_account_title or ""
    ).strip()
    payout_account_number = (
        request.form.get("payout_account_number") or current_user.payout_account_number or ""
    ).strip()
    payout_iban = (request.form.get("payout_iban") or current_user.payout_iban or "").strip()

    if not payout_account_title or not (payout_account_number or payout_iban):
        flash("Please provide payout account title and account number or IBAN.", "danger")
        return redirect(url_for("student_wallet"))

    wr = WithdrawalRequest(
        tutor_id=current_user.id,
        amount_pkr=amount,
        payout_method=payout_method,
        payout_account_title=payout_account_title,
        payout_account_number=payout_account_number,
        payout_iban=payout_iban,
    )
    current_user.pending_payout_pkr = available_payout - amount
    current_user.payout_method = payout_method
    current_user.payout_account_title = payout_account_title
    current_user.payout_account_number = payout_account_number
    current_user.payout_iban = payout_iban
    db.session.add(wr)

    send_notification_email(
        "TutorsOnline.pk Withdrawal Request",
        f"Tutor: {current_user.full_name} ({current_user.email})\n"
        f"Amount: PKR {amount}\n"
        f"Method: {wr.payout_method}",
    )
    db.session.commit()
    flash("Withdrawal request submitted for admin payment.", "success")
    return redirect(url_for("student_wallet"))


@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        message = request.form.get("message", "").strip()

        if not name or not email or not message:
            flash("Please fill in all fields.", "danger")
            return redirect(url_for("contact"))

        admin_sent = send_email(
            "superadmin@tutorsonline.pk",
            f"New Contact Message from {name}",
            f"""A new contact form message has been received.

Name: {name}
Email: {email}

Message:
{message}
""",
            reply_to=email,
        )

        user_sent = send_email(
            email,
            "We received your message - TutorsOnline.pk",
            f"""Assalam-o-Alaikum,

Thank you for contacting TutorsOnline.pk.

We have received your message and will get back to you as soon as possible.

Regards,
TutorsOnline.pk
superadmin@tutorsonline.pk
"""
        )

        if admin_sent and user_sent:
            flash("Message sent successfully.", "success")
        elif admin_sent:
            flash("Your message was received, but confirmation email could not be sent.", "warning")
        else:
            flash("Your message could not be sent right now. Please try again shortly.", "danger")

        return redirect(url_for("contact"))

    return render_template("contact.html")


@app.route("/admin")
@login_required
def admin_dashboard():
    if current_user.role != "admin":
        flash("Unauthorized.", "danger")
        return redirect(url_for("dashboard"))

    stats = {
        "users": User.query.count(),
        "tutors": User.query.filter_by(role="tutor").count(),
        "students": User.query.filter_by(role="student").count(),
        "pending_notices": PaymentNotice.query.filter(
            PaymentNotice.status.in_(PAYMENT_REVIEWABLE_STATUSES)
        ).count(),
        "live_sessions": LiveSessionLog.query.filter(LiveSessionLog.ended_at.is_(None)).count(),
        "withdrawals": WithdrawalRequest.query.filter_by(status="requested").count(),
        "visitors": app.config.get("VISITOR_COUNT", 0),
    }
    recent_notices = PaymentNotice.query.order_by(PaymentNotice.created_at.desc()).limit(10).all()
    normalize_payment_notices(recent_notices)
    live_sessions = LiveSessionLog.query.order_by(LiveSessionLog.started_at.desc()).limit(10).all()

    pending_payout_bookings = (
        Booking.query.filter(
            Booking.status == "completed",
            Booking.payout_released == False
        )
        .order_by(Booking.scheduled_at.desc())
        .limit(20)
        .all()
    )

    return render_template(
        "admin_dashboard.html",
        stats=stats,
        recent_notices=recent_notices,
        live_sessions=live_sessions,
        pending_payout_bookings=pending_payout_bookings,
    )


@app.route("/admin/users")
@login_required
def admin_users():
    if current_user.role != "admin":
        return redirect(url_for("dashboard"))

    q = request.args.get("q", "").strip()
    query = User.query
    if q:
        like = f"%{q}%"
        query = query.filter(
            (User.full_name.ilike(like))
            | (User.public_name.ilike(like))
            | (User.email.ilike(like))
            | (User.role.ilike(like))
        )

    users = query.order_by(User.created_at.desc()).all()
    return render_template("admin_users.html", users=users, q=q)


@app.route("/admin/users/<int:user_id>")
@login_required
def admin_user_detail(user_id):
    if current_user.role != "admin":
        return redirect(url_for("dashboard"))

    user = User.query.get_or_404(user_id)
    return render_template("admin_user_detail.html", user=user)


@app.route("/admin/users/<int:user_id>/review", methods=["POST"])
@login_required
def admin_review_user(user_id):
    if current_user.role != "admin":
        return redirect(url_for("dashboard"))

    user = User.query.get_or_404(user_id)
    action = request.form.get("action", "").strip()
    reason = request.form.get("reason", "").strip()
    user.admin_review_note = reason

    if action == "request_fee":
        if user.role == "tutor":
            user.is_verified_tutor = False
            user.profile_stage = "fee_pending"
        flash("Tutor selected. Registration fee request email sent.", "success")

    elif action == "activate":
        if user.role == "tutor":
            user.is_verified_tutor = True
            user.profile_stage = "approved"
            user.is_public_tutor = True
            user.approved_at = datetime.utcnow()
        flash("Tutor activated successfully.", "success")

    elif action == "reject":
        if user.role == "tutor":
            user.is_verified_tutor = False
            user.profile_stage = "rejected"
            user.is_public_tutor = False
            user.rejected_at = datetime.utcnow()
        flash(f"User marked rejected.{f' Reason: {reason}' if reason else ''}", "warning")

    elif action == "pend":
        if user.role == "tutor":
            user.is_verified_tutor = False
            user.profile_stage = "under_review"
        flash("User marked pending review.", "info")

    else:
        flash("Invalid review action.", "danger")
        return redirect(url_for("admin_user_detail", user_id=user.id))

    db.session.commit()
    if user.role == "tutor":
        send_tutor_review_email(user, action, reason)
    return redirect(url_for("admin_user_detail", user_id=user.id))

@app.route("/settings/availability/<int:rule_id>/edit", methods=["GET", "POST"])
@login_required
def edit_availability_rule(rule_id):
    if current_user.role != "tutor":
        flash("Unauthorized.", "danger")
        return redirect(url_for("settings"))

    rule = TutorAvailabilityRule.query.get_or_404(rule_id)

    if rule.tutor_id != current_user.id:
        flash("Unauthorized.", "danger")
        return redirect(url_for("settings"))

    if request.method == "POST":
        start_time = (request.form.get("start_time") or "").strip()
        end_time = (request.form.get("end_time") or "").strip()
        slot_minutes = int(request.form.get("slot_minutes") or 60)

        if not start_time or not end_time:
            flash("Invalid time values.", "danger")
            return redirect(request.url)

        rule.start_time = start_time
        rule.end_time = end_time
        rule.slot_minutes = slot_minutes if slot_minutes > 0 else 60

        db.session.commit()
        flash("Availability updated.", "success")
        return redirect(url_for("settings"))

    return render_template("edit_availability.html", rule=rule)

@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@login_required
def admin_delete_user(user_id):
    if current_user.role != "admin":
        flash("Unauthorized.", "danger")
        return redirect(url_for("dashboard"))

    user = User.query.get_or_404(user_id)

    if user.role == "admin":
        flash("Admin user cannot be deleted.", "danger")
        return redirect(url_for("admin_users"))

    db.session.delete(user)
    db.session.commit()

    flash("User deleted successfully.", "success")
    return redirect(url_for("admin_users"))

@app.route("/admin/users/<int:user_id>/contact", methods=["POST"])
@login_required
def admin_contact_user(user_id):
    if current_user.role != "admin":
        return redirect(url_for("dashboard"))

    user = User.query.get_or_404(user_id)

    subject = request.form.get("subject", "").strip()
    message = request.form.get("message", "").strip()

    if not subject or not message:
        flash("Subject and message are required.", "danger")
        return redirect(url_for("admin_users"))

    try:
        send_email(
            user.email,
            f"[TutorsOnline.pk] {subject}",
            f"""
Assalam-o-Alaikum,

{message}

Regards,
TutorsOnline.pk
superadmin@tutorsonline.pk
"""
        )
        flash("Email sent to user successfully.", "success")
    except Exception as e:
        print("❌ Contact error:", str(e))
        flash("Failed to send email.", "danger")

    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/toggle", methods=["POST"])
@login_required
def admin_toggle_user(user_id):
    if current_user.role != "admin":
        return redirect(url_for("dashboard"))

    user = User.query.get_or_404(user_id)
    user.is_active_user = not user.is_active_user
    db.session.commit()
    flash("User status updated.", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/verify-tutor", methods=["POST"])
@login_required
def admin_verify_tutor(user_id):
    if current_user.role != "admin":
        return redirect(url_for("dashboard"))

    user = User.query.get_or_404(user_id)
    if user.role == "tutor":
        user.is_verified_tutor = True
        db.session.commit()
        flash("Tutor verified.", "success")

    return redirect(url_for("admin_users"))



@app.route("/admin/payment-notices/<int:notice_id>/approve", methods=["POST"])
@login_required
def admin_approve_notice(notice_id):
    if current_user.role != "admin":
        return redirect(url_for("dashboard"))

    notice = PaymentNotice.query.get_or_404(notice_id)
    reason = (request.form.get("admin_note", "") or request.form.get("reason", "") or "").strip()
    changed, message = approve_student_payment_notice(notice, reason)

    if changed:
        db.session.commit()
        flash(message, "success")
    else:
        db.session.rollback()
        flash(message, "warning")

    return redirect(url_for("admin_payment_notices"))


@app.route("/admin/payment-notices/<int:notice_id>/reject", methods=["POST"])
@login_required
def admin_reject_notice(notice_id):
    if current_user.role != "admin":
        return redirect(url_for("dashboard"))

    notice = PaymentNotice.query.get_or_404(notice_id)
    reason = (request.form.get("admin_note", "") or request.form.get("reason", "") or "").strip()
    changed, message = decline_student_payment_notice(notice, reason)

    if changed:
        db.session.commit()
        flash(message, "warning")
    else:
        db.session.rollback()
        flash(message, "warning")

    return redirect(url_for("admin_payment_notices"))


@app.route("/admin/withdrawals")
@login_required
def admin_withdrawals():
    if current_user.role != "admin":
        return redirect(url_for("dashboard"))

    withdrawals = WithdrawalRequest.query.order_by(
        WithdrawalRequest.created_at.desc()
    ).all()
    return render_template("admin_withdrawals.html", withdrawals=withdrawals)


@app.route("/admin/withdrawals/<int:withdrawal_id>/mark-paid", methods=["POST"])
@login_required
def admin_mark_withdrawal_paid(withdrawal_id):
    if current_user.role != "admin":
        return redirect(url_for("dashboard"))

    wr = WithdrawalRequest.query.get_or_404(withdrawal_id)

    if wr.status == "paid":
        flash("Withdrawal is already marked paid.", "warning")
        return redirect(url_for("admin_withdrawals"))

    if wr.status != "requested":
        flash("Only requested withdrawals can be marked paid.", "warning")
        return redirect(url_for("admin_withdrawals"))

    wr.status = "paid"
    wr.admin_note = (request.form.get("admin_note", "") or "").strip()
    db.session.commit()
    flash("Withdrawal marked paid.", "success")
    return redirect(url_for("admin_withdrawals"))


@app.route("/admin/bonuses")
@login_required
def admin_bonuses():
    if current_user.role != "admin":
        return redirect(url_for("dashboard"))

    bonuses = TutorBonus.query.order_by(TutorBonus.created_at.desc()).all()
    return render_template("admin_bonuses.html", bonuses=bonuses)


@app.route("/admin/live-sessions")
@login_required
def admin_live_sessions():
    if current_user.role != "admin":
        return redirect(url_for("dashboard"))

    live_sessions = LiveSessionLog.query.order_by(
        LiveSessionLog.started_at.desc()
    ).all()
    return render_template("admin_live_sessions.html", live_sessions=live_sessions)


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    """Serve uploaded files safely with a graceful fallback for missing media."""
    filename = (filename or "").replace("\\", "/").lstrip("/")
    if filename.startswith("uploads/"):
        filename = filename[len("uploads/"):]

    if not filename or ".." in Path(filename).parts:
        return redirect(url_for("static", filename=PROFILE_IMAGE_FALLBACK))

    upload_root = Path(app.config["UPLOAD_FOLDER"]).resolve()
    upload_path = (upload_root / filename).resolve()

    try:
        upload_path.relative_to(upload_root)
    except ValueError:
        return redirect(url_for("static", filename=PROFILE_IMAGE_FALLBACK))

    if upload_path.is_file():
        return send_from_directory(
            app.config["UPLOAD_FOLDER"],
            filename,
            max_age=MEDIA_CACHE_SECONDS,
        )

    return redirect(url_for("static", filename=PROFILE_IMAGE_FALLBACK))

@app.route("/demo_seed/<path:filename>")
def demo_seed_file(filename):
    filename = (filename or "").replace("\\", "/").lstrip("/")
    if not filename or ".." in Path(filename).parts:
        return redirect(url_for("static", filename=PROFILE_IMAGE_FALLBACK))

    demo_dir = BASE_DIR / "demo_seed"
    if demo_dir.exists():
        candidate = (demo_dir / filename).resolve()
        try:
            candidate.relative_to(demo_dir.resolve())
        except ValueError:
            return redirect(url_for("static", filename=PROFILE_IMAGE_FALLBACK))
        if candidate.is_file():
            return send_from_directory(str(demo_dir), filename, max_age=MEDIA_CACHE_SECONDS)

    return redirect(url_for("uploaded_file", filename=filename))



@app.route("/seed")
@login_required
def seed():
    guard = admin_required_response()
    if guard:
        return guard

    if not DEV_ROUTE_ENABLED:
        flash("Seed route is disabled. Set ALLOW_DEV_ROUTES=true only in a safe local/dev environment.", "warning")
        return redirect(url_for("admin_dashboard"))

    db.create_all()

    if User.query.count() > 0:
        flash("Database already seeded.", "info")
        return redirect(url_for("index"))

    admin = User(
        email="jojopk44@gmail.com",
        role="admin",
        full_name="TutorsOnline.pk Admin",
        public_name="TutorsOnline.pk Admin",
        qualification="Platform Manager",
        bio="Administrative control account for TutorsOnline.pk",
        is_verified_tutor=False,
    )
    admin.set_password("admin123")
    db.session.add(admin)

    student = User(
        email="hamza@example.com",
        role="student",
        full_name="Hamza Ali",
        public_name="Hamza",
        qualification="Grade 10 Student",
        bio="Preparing for board exams.",
        credits_balance=1000,
    )
    student.set_password("Demo@12345")
    db.session.add(student)

    tutor_data = [
        (
            "ayesha@example.com",
            "Ayesha Noor",
            "Ayesha N.",
            "MSc Mathematics",
            "Mathematics",
            "Grade 5-8, Matric",
            7,
            "Patient maths tutor for school learners.",
            "Grade 5–8 Tutor",
        ),
        (
            "bilal@example.com",
            "Bilal Khan",
            "Sir Bilal",
            "MPhil Physics",
            "Physics",
            "Matric, Intermediate",
            6,
            "Concept-first physics lessons for board students.",
            "Intermediate Specialist",
        ),
        (
            "sana@example.com",
            "Sana Fatima",
            "Miss Sana",
            "MA English",
            "English",
            "Grade 5-8, O Level",
            8,
            "Essay writing and grammar specialist.",
            "O Level Specialist",
        ),
        (
            "umar@example.com",
            "Umar Rashid",
            "Umar R.",
            "MSc Chemistry",
            "Chemistry",
            "Matric, Intermediate",
            5,
            "Clear and practical chemistry teaching.",
            "Intermediate Specialist",
        ),
        (
            "hina@example.com",
            "Hina Javed",
            "Hina J.",
            "BS Computer Science",
            "Computer Science, Mathematics",
            "Grade 5-8, O Level",
            4,
            "Friendly tutor for coding and maths foundations.",
            "O Level Specialist",
        ),
        (
            "faraz@example.com",
            "Faraz Ahmed",
            "Sir Faraz",
            "MA Urdu",
            "Urdu, Pakistan Studies",
            "Matric, Intermediate",
            9,
            "Strong board exam preparation support.",
            "Intermediate Specialist",
        ),
    ]

    for (
        email,
        full_name,
        public_name,
        qualification,
        subjects,
        class_levels,
        experience_years,
        bio,
        tutor_category,
    ) in tutor_data:
        tutor = User(
            email=email,
            role="tutor",
            full_name=full_name,
            public_name=public_name,
            qualification=qualification,
            subjects=subjects,
            class_levels=class_levels,
            experience_years=experience_years,
            bio=bio,
            tutor_category=tutor_category,
            is_verified_tutor=True,
            rating_avg=4.8,
            rating_count=12,
            sessions_completed=20,
        )
        tutor.set_password("Demo@12345")
        db.session.add(tutor)

    db.session.commit()
    flash("Database seeded successfully.", "success")
    return redirect(url_for("index"))

@app.route("/settings/availability/exception/add", methods=["POST"])
@login_required
def add_availability_exception():
    if current_user.role != "tutor":
        flash("Only tutors can manage availability.", "danger")
        return redirect(url_for("settings"))

    exception_date_raw = (request.form.get("exception_date", "") or "").strip()
    mode = (request.form.get("mode", "") or "block_full_day").strip()
    start_time = (request.form.get("start_time", "") or "").strip()
    end_time = (request.form.get("end_time", "") or "").strip()
    note = (request.form.get("note", "") or "").strip()

    if not exception_date_raw:
        flash("Please select a date.", "danger")
        return redirect(url_for("settings"))

    try:
        exception_date = datetime.strptime(exception_date_raw, "%Y-%m-%d").date()
    except ValueError:
        flash("Invalid date.", "danger")
        return redirect(url_for("settings"))

    if mode == "block_full_day":
        ex = TutorAvailabilityException(
            tutor_id=current_user.id,
            exception_date=exception_date,
            start_time="",
            end_time="",
            is_blocked=True,
            note=note or "Blocked full day",
        )
        db.session.add(ex)
        db.session.commit()
        flash("Full day blocked successfully.", "success")
        return redirect(url_for("settings"))

    if mode not in {"block_range", "custom_slots"}:
        flash("Invalid override type.", "danger")
        return redirect(url_for("settings"))

    if not start_time or not end_time:
        flash("Please provide both start and end times.", "danger")
        return redirect(url_for("settings"))

    if hhmm_to_minutes(end_time) <= hhmm_to_minutes(start_time):
        flash("End time must be after start time.", "danger")
        return redirect(url_for("settings"))

    ex = TutorAvailabilityException(
        tutor_id=current_user.id,
        exception_date=exception_date,
        start_time=start_time,
        end_time=end_time,
        is_blocked=(mode == "block_range"),
        note=note or ("Blocked time range" if mode == "block_range" else "Custom available slots"),
    )
    db.session.add(ex)
    db.session.commit()

    flash("Date override added successfully.", "success")
    return redirect(url_for("settings"))


@app.route("/settings/availability/exception/<int:exception_id>/delete", methods=["POST"])
@login_required
def delete_availability_exception(exception_id):
    if current_user.role != "tutor":
        flash("Only tutors can manage availability.", "danger")
        return redirect(url_for("settings"))

    ex = TutorAvailabilityException.query.get_or_404(exception_id)
    if ex.tutor_id != current_user.id:
        flash("Unauthorized.", "danger")
        return redirect(url_for("settings"))

    db.session.delete(ex)
    db.session.commit()
    flash("Date override deleted.", "success")
    return redirect(url_for("settings"))

@app.route("/seed-admin")
@login_required
def seed_admin():
    guard = admin_required_response()
    if guard:
        return guard

    if not DEV_ROUTE_ENABLED:
        flash("Seed-admin route is disabled. Set ALLOW_DEV_ROUTES=true only in a safe local/dev environment.", "warning")
        return redirect(url_for("admin_dashboard"))

    with app.app_context():
        db.create_all()
        ensure_user_columns()
        ensure_default_admin()
    return "Default admin ensured."

@app.errorhandler(403)
def forbidden_error(error):
    return render_template("errors/403.html"), 403


@app.errorhandler(404)
def not_found_error(error):
    return render_template("errors/404.html"), 404


@app.errorhandler(413)
def file_too_large_error(error):
    max_mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
    return render_template("errors/413.html", max_mb=max_mb), 413


@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    app.logger.exception("Unhandled server error: %s", error)
    return render_template("errors/500.html"), 500


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        ensure_user_columns()
        ensure_default_admin()

    debug_enabled = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=debug_enabled)
