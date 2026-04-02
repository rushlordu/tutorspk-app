import os
import re
import smtplib
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from dotenv import load_dotenv
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

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
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from authlib.integrations.flask_client import OAuth

load_dotenv()


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
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

LOCAL_DB_PATH = (INSTANCE_DIR / "tutorpk.db").resolve().as_posix()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-change-me")
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
    "DATABASE_URL", f"sqlite:///{LOCAL_DB_PATH}"
)
print("Using DB:", app.config["SQLALCHEMY_DATABASE_URI"])

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024
app.config["UPLOAD_FOLDER"] = str(UPLOAD_DIR)

# Temporary site status flag
UNDER_CONSTRUCTION= False
#UNDER_CONSTRUCTION = os.getenv("UNDER_CONSTRUCTION", "true").lower() == "true"

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

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

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
    
    profile_image = db.Column(db.String(255), default="")
    demo_video_url = db.Column(db.String(255), default="")
    modest_profile = db.Column(db.Boolean, default=False)
    audio_only = db.Column(db.Boolean, default=False)
    is_active_user = db.Column(db.Boolean, default=True)
    is_verified_tutor = db.Column(db.Boolean, default=False)
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
    room_code = db.Column(db.String(50), default=lambda: uuid4().hex[:10])
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


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


class ChatFlag(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("booking.id"), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    message = db.Column(db.Text, nullable=False)
    reason = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    booking = db.relationship("Booking", backref="chat_flags")
    sender = db.relationship("User")


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


@app.context_processor
def inject_globals():
    return {
        "credit_rate": app.config["CREDIT_RATE"],
        "google_configured": bool(
            app.config["GOOGLE_CLIENT_ID"] and app.config["GOOGLE_CLIENT_SECRET"]
        ),
        "under_construction": UNDER_CONSTRUCTION,
    }


@app.route("/preview-off")
@login_required
def preview_off():
    session.pop("preview_mode", None)
    flash("Preview mode disabled", "info")
    return redirect(url_for("index"))

@app.route("/test-email")
def test_email():
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


def validate_tutor_application_form(form):
    qualification = form.get("qualification", "").strip()
    experience_years = form.get("experience_years", "").strip()
    demo_video_url = form.get("demo_video_url", "").strip()
    demo_length_confirmed = form.get("demo_length_confirmed", "").strip()

    if not qualification:
        return "Qualification is required for tutor applications."

    if not experience_years:
        return "Teaching experience is required for tutor applications."

    if not demo_video_url:
        return "A 2–5 minute demo video is required for tutor applications."

    if demo_length_confirmed != "yes":
        return "Please confirm that your demo video is between 2 and 5 minutes."

    return None

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


def build_google_user_from_form(form, email: str, fallback_name: str) -> User:
    role = form.get("role", "student").strip()

    gender = pick_with_other(form, "gender")
    city = pick_with_other(form, "city")

    qualification = ""
    subjects = ""
    class_levels = ""
    experience_years = 0
    bio = ""
    modest_profile = bool(form.get("modest_profile"))
    audio_only = bool(form.get("audio_only"))
    main_subject = ""
    additional_subjects = ""
    student_level = ""
    student_subject_needed = ""
    preferred_tutor_gender = ""
    learning_mode = ""
    teaching_mode = ""
    hourly_rate = 0
    demo_video_url = form.get("demo_video_url", "").strip()

    full_name = form.get("full_name", fallback_name).strip() or fallback_name
    public_name = form.get("public_name", full_name).strip() or full_name

    if role == "student":
        student_level = pick_with_other(form, "student_level")
        student_subject_needed = pick_with_other(form, "student_subject_needed")
        preferred_tutor_gender = form.get("preferred_tutor_gender", "").strip()
        learning_mode = "online"
        subjects = student_subject_needed
        class_levels = student_level
        bio = "Signed up via Google"
    elif role == "tutor":
        qualification = pick_with_other(form, "qualification")
        main_subject = pick_with_other(form, "main_subject")
        additional_subjects = form.get("additional_subjects", "").strip()
        class_levels = pick_with_other(form, "class_levels")
        experience_years = int(form.get("experience_years") or 0)
        teaching_mode = "online"
        hourly_rate = int(form.get("hourly_rate") or 0)
        bio = form.get("bio", "").strip() or "Signed up via Google"
        subjects = ", ".join([s for s in [main_subject, additional_subjects] if s])

    user = User(
        email=email,
        role=role,
        full_name=full_name,
        public_name=public_name,
        qualification=qualification,
        subjects=subjects,
        class_levels=class_levels,
        experience_years=experience_years,
        bio=bio,
        modest_profile=modest_profile,
        audio_only=audio_only,
        gender=gender,
        city=city,
        main_subject=main_subject,
        additional_subjects=additional_subjects,
        student_level=student_level,
        student_subject_needed=student_subject_needed,
        preferred_tutor_gender=preferred_tutor_gender,
        learning_mode=learning_mode,
        teaching_mode=teaching_mode,
        hourly_rate=hourly_rate,
        demo_video_url=demo_video_url,
    )

    user.tutor_category = classify_teacher(user.subjects, user.class_levels)
    user.set_password(uuid4().hex)

    if role == "tutor":
        user.is_verified_tutor = False

    image_file = request.files.get("profile_image_file")
    if image_file and image_file.filename:
        filename = f"{uuid4().hex}_{secure_filename(image_file.filename)}"
        image_file.save(Path(app.config["UPLOAD_FOLDER"]) / filename)
        user.profile_image = filename

    return user

@app.route("/")
def index():
    if UNDER_CONSTRUCTION:
        return render_template("under_construction.html")

    featured_tutors = (
        User.query.filter_by(role="tutor", is_verified_tutor=True)
        .order_by(User.rating_avg.desc(), User.sessions_completed.desc())
        .limit(10)
        .all()
    )
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

@app.route("/select-tutor/<int:tutor_id>", methods=["GET", "POST"])
@login_required
def select_tutor(tutor_id):
    tutor = User.query.get_or_404(tutor_id)

    if request.method == "POST":
        subject = request.form.get("subject")
        level = request.form.get("level")

        return redirect(url_for(
            "book_tutor",
            tutor_id=tutor.id,
            subject=subject,
            level=level
        ))

    return render_template(
        "select_tutor.html",
        tutor=tutor,
        subject_options=SUBJECT_OPTIONS,
        level_options=LEVEL_OPTIONS
    )

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        if User.query.filter_by(email=email).first():
            flash("Email already registered.", "danger")
            return redirect(url_for("register"))

        role = request.form["role"]

        gender = pick_with_other(request.form, "gender")
        city = pick_with_other(request.form, "city")

        qualification = ""
        subjects = ""
        class_levels = ""
        experience_years = 0
        bio = ""
        modest_profile = bool(request.form.get("modest_profile"))
        audio_only = bool(request.form.get("audio_only"))
        main_subject = ""
        additional_subjects = ""
        student_level = ""
        student_subject_needed = ""
        preferred_tutor_gender = ""
        learning_mode = ""
        teaching_mode = ""
        hourly_rate = 0
        demo_video_url = request.form.get("demo_video_url", "").strip()

        if role == "student":
            student_level = pick_with_other(request.form, "student_level")
            student_subject_needed = pick_with_other(request.form, "student_subject_needed")
            preferred_tutor_gender = request.form.get("preferred_tutor_gender", "").strip()
            learning_mode = request.form.get("learning_mode", "").strip()
            subjects = student_subject_needed
            class_levels = student_level
            bio = "Student account"

        elif role == "tutor":
            qualification = pick_with_other(request.form, "qualification")
            main_subject = pick_with_other(request.form, "main_subject")
            additional_subjects = request.form.get("additional_subjects", "").strip()
            class_levels = pick_with_other(request.form, "class_levels")
            experience_years = int(request.form.get("experience_years") or 0)
            teaching_mode = request.form.get("teaching_mode", "").strip()
            hourly_rate = int(request.form.get("hourly_rate") or 0)
            bio = request.form.get("bio", "").strip()
            subjects = ", ".join([s for s in [main_subject, additional_subjects] if s])

        user = User(
            email=email,
            role=role,
            full_name=request.form["full_name"].strip(),
            public_name=request.form.get("public_name", request.form["full_name"]).strip(),
            qualification=qualification,
            subjects=subjects,
            class_levels=class_levels,
            experience_years=experience_years,
            bio=bio,
            modest_profile=modest_profile,
            audio_only=audio_only,
            gender=gender,
            city=city,
            main_subject=main_subject,
            additional_subjects=additional_subjects,
            student_level=student_level,
            student_subject_needed=student_subject_needed,
            preferred_tutor_gender=preferred_tutor_gender,
            learning_mode=learning_mode,
            teaching_mode=teaching_mode,
            hourly_rate=hourly_rate,
            demo_video_url=demo_video_url,
        )

        user.tutor_category = classify_teacher(user.subjects, user.class_levels)
        user.set_password(request.form["password"])

        image_file = request.files.get("profile_image_file")
        if image_file and image_file.filename:
            filename = f"{uuid4().hex}_{secure_filename(image_file.filename)}"
            image_file.save(Path(app.config["UPLOAD_FOLDER"]) / filename)
            user.profile_image = filename

        if role == "tutor":
            user.is_verified_tutor = False


        db.session.add(user)
        db.session.commit()

        
        send_signup_emails(user)

        if role == "tutor":
            flash(
                "Tutor application submitted free of cost. If selected after review, you will be asked to pay PKR 500 to activate your profile.",
                "success",
            )
        else:
            flash("Registration completed successfully. Please log in.", "success")
        return redirect(url_for("login"))


    return render_template("register.html")



@app.route("/google-login")
def google_login():
    if not app.config["GOOGLE_CLIENT_ID"]:
        flash("Google login not configured.", "danger")
        return redirect(url_for("login"))

    redirect_uri = url_for("google_callback", _external=True)
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
        if user.role == "admin":
            return redirect(url_for("admin_dashboard"))
        return redirect(url_for("dashboard"))

    session["google_signup"] = {"email": email, "name": name}
    return redirect(url_for("complete_google_signup"))


@app.route("/complete-google-signup", methods=["GET", "POST"])
def complete_google_signup():
    google_signup = session.get("google_signup")
    
 
    if not google_signup:
        flash("Your Google signup session expired. Please try again.", "warning")
        return redirect(url_for("login"))

    email = google_signup["email"]
    fallback_name = google_signup["name"]

    existing_user = User.query.filter_by(email=email).first()
    if existing_user:
        login_user(existing_user)
        flash("Account already exists. Logged in successfully.", "success")
        if existing_user.role == "admin":
            return redirect(url_for("admin_dashboard"))
        return redirect(url_for("dashboard"))

    
    if request.method == "POST":
        role = request.form.get("role", "student").strip().lower()

        if role == "tutor":
            tutor_error = validate_tutor_application_form(request.form)
            if tutor_error:
                flash(tutor_error, "danger")
                return redirect(url_for("complete_google_signup"))

        user = build_google_user_from_form(request.form, email=email, fallback_name=fallback_name)
        db.session.add(user)
        db.session.commit()

        send_signup_emails(user)

        session.pop("google_signup", None)
        login_user(user)
        if user.role == "tutor":
            flash("Google signup completed. Tutor profile created and sent for review.", "success")
        else:
            flash("Google signup completed successfully.", "success")
        return redirect(url_for("dashboard"))

    return render_template(
        "google_complete_profile.html",
        google_email=email,
        google_name=fallback_name,
    )


@app.route("/login", methods=["GET", "POST"])

def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(request.form["password"]):
            login_user(user)
            flash("Logged in successfully.", "success")
            if user.role == "admin":
                return redirect(url_for("admin_dashboard"))
            return redirect(url_for("dashboard"))
        flash("Invalid credentials.", "danger")
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

    upcoming = (
        Booking.query.filter(
            ((Booking.student_id == current_user.id) | (Booking.tutor_id == current_user.id))
        )
        .order_by(Booking.scheduled_at.desc())
        .limit(10)
        .all()
    )
    pending_notices = []
    if current_user.role == "student":
        pending_notices = PaymentNotice.query.filter_by(
        student_id=current_user.id,
        status="pending"
        ).order_by(PaymentNotice.created_at.desc()).all()
    pending_notices=pending_notices,
    return render_template("dashboard.html", bookings=upcoming)

@app.route("/tutors")
def tutors():
    level = request.args.get("level", "").strip()
    subject = request.args.get("subject", "").strip()

    if subject == "other":
        subject = request.args.get("subject_other", "").strip()
    if level == "other":
        level = request.args.get("level_other", "").strip()

    query = User.query.filter_by(role="tutor", is_verified_tutor=True)
    if level:
        query = query.filter(User.class_levels.ilike(f"%{level}%"))
    if subject:
        query = query.filter(User.subjects.ilike(f"%{subject}%"))

    return render_template(
        "tutors.html",
        tutors=query.all(),
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

    return render_template(
        "tutor_profile.html",
        tutor=tutor,
        completed_bookings=completed_bookings,
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

    subject = request.args.get("subject", "")
    level = request.args.get("level", "")


    if request.method == "POST":
        credits_cost = int(request.form.get("credits_cost", 100))
        if current_user.credits_balance < credits_cost:
            flash("Insufficient credits. Please top up first.", "danger")
            return redirect(url_for("buy_credits"))

        scheduled_at = datetime.strptime(
            request.form["scheduled_at"], "%Y-%m-%dT%H:%M"
        )
        booking = Booking(
            student_id=current_user.id,
            tutor_id=tutor.id,
            subject=request.form["subject"],
            class_level=request.form["class_level"],
            scheduled_at=scheduled_at,
            duration_minutes=int(request.form.get("duration_minutes", 60)),
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

        send_booking_emails(booking)

        flash("Session booked successfully.", "success")
        return redirect(url_for("dashboard"))
    return render_template(
            "book_tutor.html",
                tutor=tutor,
                subject_options=SUBJECT_OPTIONS,
                level_options=LEVEL_OPTIONS,
                subject_prefill=subject,
                level_prefill=level
        )
    

@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


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

    if request.method == "POST":
        credits_requested = int(request.form.get("amount_sent_pkr", 0)) // app.config["CREDIT_RATE"]
        if credits_requested < 10:
            flash("Minimum purchase is 10 credits.", "danger")
            return redirect(url_for("buy_credits"))
        amount = int(request.form["amount_sent_pkr"])
        credits = amount // app.config["CREDIT_RATE"]
        screenshot = request.files.get("screenshot")
        filename = ""

        if screenshot and screenshot.filename:
            filename = f"{uuid4().hex}_{secure_filename(screenshot.filename)}"
            screenshot.save(Path(app.config["UPLOAD_FOLDER"]) / filename)

        notice = PaymentNotice(
            student_id=current_user.id,
            amount_sent_pkr=amount,
            claimed_credits=credits,
            sender_name=request.form.get("sender_name", ""),
            sender_account=request.form.get("sender_account", ""),
            transfer_method=request.form.get("transfer_method", "bank"),
            screenshot_filename=filename,
            note=request.form.get("note", ""),
        )
        db.session.add(notice)
        db.session.commit()

        fallback = send_notification_email(
            "TutorsOnline.pk Payment Notice",
            f"Student: {current_user.full_name} ({current_user.email})\n"
            f"Amount: PKR {amount}\n"
            f"Claimed credits: {credits}\n"
            f"Sender account: {notice.sender_account}\n"
            f"Method: {notice.transfer_method}",
        )
        if fallback:
            flash(
                "Notice saved. SMTP not configured; email logged to email_outbox.log",
                "warning",
            )
        else:
            flash("Payment notice submitted and admin notified.", "success")
        return redirect(url_for("student_wallet"))

    return render_template("buy_credits.html")


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
        return render_template("student_wallet.html", txs=txs, notices=notices)

    txs = (
        CreditTransaction.query.filter_by(user_id=current_user.id)
        .order_by(CreditTransaction.created_at.desc())
        .all()
    )
    return render_template("tutor_wallet.html", txs=txs)


@app.route("/live/<int:booking_id>", methods=["GET", "POST"])
@login_required
def live_session(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    if current_user.role not in ["admin"] and current_user.id not in [
        booking.student_id,
        booking.tutor_id,
    ]:
        flash("Unauthorized.", "danger")
        return redirect(url_for("dashboard"))

    log = LiveSessionLog.query.filter_by(booking_id=booking.id).first()
    if not log:
        log = LiveSessionLog(booking_id=booking.id, room_code=booking.room_code)
        db.session.add(log)

    if current_user.role == "student":
        log.student_joined = True
    elif current_user.role == "tutor":
        log.tutor_joined = True
    elif current_user.role == "admin":
        log.admin_joined = True
        log.last_activity_note = "Admin observer joined transparently"

    db.session.commit()

    if request.method == "POST":
        message = request.form.get("message", "").strip()
        reason = None
        for pattern in PHONE_OR_EMAIL_PATTERNS:
            if pattern.search(message):
                reason = "Possible off-platform contact sharing"
                break

        if reason:
            db.session.add(
                ChatFlag(
                    booking_id=booking.id,
                    sender_id=current_user.id,
                    message=message,
                    reason=reason,
                )
            )
            db.session.commit()
            flash(
                "Message blocked. Contact sharing is not allowed on TutorsOnline.pk.",
                "danger",
            )
        else:
            flash("Message accepted in this MVP room shell.", "success")

    return render_template("live_session.html", booking=booking, log=log)


@app.route("/bookings/<int:booking_id>/complete/student", methods=["POST"])
@login_required
def complete_student(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    if booking.student_id != current_user.id:
        flash("Unauthorized.", "danger")
        return redirect(url_for("dashboard"))

    booking.student_marked_complete = True
    booking.status = "completed"

    if not booking.payout_released:
        tutor = booking.tutor
        tutor_share_pkr = int(booking.credits_cost * app.config["CREDIT_RATE"] * 0.8)
        tutor.pending_payout_pkr += tutor_share_pkr
        tutor.total_earnings_pkr += tutor_share_pkr
        tutor.monthly_earnings_pkr += tutor_share_pkr
        tutor.sessions_completed += 1
        booking.payout_released = True

        apply_bonus_if_eligible(tutor)

        if tutor.sessions_completed in [10, 25, 50] and not any(
            b.bonus_type == f"activity_{tutor.sessions_completed}"
            for b in tutor.bonus_entries
        ):
            credits = {10: 50, 25: 100, 50: 200}[tutor.sessions_completed]
            tutor.bonus_credits += credits
            add_credits(
                tutor,
                credits,
                "bonus",
                f"Activity bonus for {tutor.sessions_completed} completed sessions",
            )
            db.session.add(
                TutorBonus(
                    tutor_id=tutor.id,
                    bonus_type=f"activity_{tutor.sessions_completed}",
                    credits_awarded=credits,
                    note="Activity bonus",
                )
            )

    db.session.commit()
    flash(
        "Session completed and tutor earnings released to pending payout.",
        "success",
    )
    return redirect(url_for("dashboard"))


@app.route("/withdraw", methods=["POST"])
@login_required
def withdraw():
    if current_user.role != "tutor":
        flash("Only tutors can request withdrawals.", "danger")
        return redirect(url_for("dashboard"))

    amount = int(request.form["amount_pkr"])
    if amount > current_user.pending_payout_pkr:
        flash("Insufficient pending payout.", "danger")
        return redirect(url_for("student_wallet"))

    wr = WithdrawalRequest(
        tutor_id=current_user.id,
        amount_pkr=amount,
        payout_method=request.form.get("payout_method", current_user.payout_method),
        payout_account_title=request.form.get(
            "payout_account_title", current_user.payout_account_title
        ),
        payout_account_number=request.form.get(
            "payout_account_number", current_user.payout_account_number
        ),
        payout_iban=request.form.get("payout_iban", current_user.payout_iban),
    )
    current_user.pending_payout_pkr -= amount
    db.session.add(wr)

    send_notification_email(
        "TutorsOnline.pk Withdrawal Request",
        f"Tutor: {current_user.full_name} ({current_user.email})\n"
        f"Amount: PKR {amount}\n"
        f"Method: {wr.payout_method}",
    )
    db.session.commit()
    flash("Withdrawal request submitted.", "success")
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
        "pending_notices": PaymentNotice.query.filter_by(status="pending").count(),
        "live_sessions": LiveSessionLog.query.filter(LiveSessionLog.ended_at.is_(None)).count(),
        "withdrawals": WithdrawalRequest.query.filter_by(status="requested").count(),
        "visitors": app.config.get("VISITOR_COUNT", 0),
    }
    recent_notices = PaymentNotice.query.order_by(PaymentNotice.created_at.desc()).limit(10).all()
    live_sessions = LiveSessionLog.query.order_by(LiveSessionLog.started_at.desc()).limit(10).all()

    return render_template(
        "admin_dashboard.html",
        stats=stats,
        recent_notices=recent_notices,
        live_sessions=live_sessions,
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

    if action == "request_fee":
        if user.role == "tutor":
            user.is_verified_tutor = False
        flash("Tutor selected. Registration fee request email sent.", "success")

    elif action == "activate":
        if user.role == "tutor":
            user.is_verified_tutor = True
        flash("Tutor activated successfully.", "success")

    elif action == "reject":
        if user.role == "tutor":
            user.is_verified_tutor = False
        flash(f"User marked rejected.{f' Reason: {reason}' if reason else ''}", "warning")

    elif action == "pend":
        if user.role == "tutor":
            user.is_verified_tutor = False
        flash("User marked pending review.", "info")

    else:
        flash("Invalid review action.", "danger")
        return redirect(url_for("admin_user_detail", user_id=user.id))

    db.session.commit()
    send_tutor_review_email(user, action, reason)

    return redirect(url_for("admin_user_detail", user_id=user.id))

@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@login_required
def admin_delete_user(user_id):
    if current_user.role != "admin":
        return redirect(url_for("dashboard"))

    user = User.query.get_or_404(user_id)

    if user.id == current_user.id:
        flash("You cannot delete your own admin account.", "danger")
        return redirect(url_for("admin_users"))

    try:
        db.session.delete(user)
        db.session.commit()
        flash("User deleted successfully.", "success")
    except Exception as e:
        db.session.rollback()
        print("❌ Delete error:", str(e))
        flash("Failed to delete user.", "danger")

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


@app.route("/admin/payment-notices")

@login_required
def admin_payment_notices():
    if current_user.role != "admin":
        return redirect(url_for("dashboard"))

    notices = PaymentNotice.query.order_by(PaymentNotice.created_at.desc()).all()
    return render_template("admin_payment_notices.html", notices=notices)


@app.route("/admin/payment-notices/<int:notice_id>/approve", methods=["POST"])
@login_required
def admin_approve_notice(notice_id):
    if current_user.role != "admin":
        return redirect(url_for("dashboard"))

    notice = PaymentNotice.query.get_or_404(notice_id)
    if notice.status != "pending":
        flash("Notice already reviewed.", "warning")
        return redirect(url_for("admin_payment_notices"))

    notice.status = "approved"
    notice.admin_note = request.form.get("admin_note", "")
    notice.reviewed_at = datetime.utcnow()
    add_credits(
        notice.student,
        notice.claimed_credits,
        "topup",
        f"Approved payment notice #{notice.id}",
        notice.amount_sent_pkr,
    )
    db.session.commit()
    flash("Payment approved and credits added.", "success")
    return redirect(url_for("admin_payment_notices"))


@app.route("/admin/payment-notices/<int:notice_id>/reject", methods=["POST"])
@login_required
def admin_reject_notice(notice_id):
    if current_user.role != "admin":
        return redirect(url_for("dashboard"))

    notice = PaymentNotice.query.get_or_404(notice_id)
    notice.status = "rejected"
    notice.admin_note = request.form.get("admin_note", "")
    notice.reviewed_at = datetime.utcnow()
    db.session.commit()
    flash("Payment notice rejected.", "warning")
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
    wr.status = "paid"
    wr.admin_note = request.form.get("admin_note", "")
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
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


@app.route("/seed")
def seed():
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
    student.set_password("password123")
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
        tutor.set_password("password123")
        db.session.add(tutor)

    db.session.commit()
    flash("Database seeded successfully.", "success")
    return redirect(url_for("index"))


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=5000, debug=True)