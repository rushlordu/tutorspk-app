from __future__ import annotations

from collections import Counter
from datetime import datetime
from email.message import EmailMessage
from functools import wraps
import json
import math
import os
import re
import smtplib
import ssl
import uuid

from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit, join_room
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

BANK_CONFIG_FILE = os.path.join(BASE_DIR, "bank_details.txt")
EMAIL_LOG_FILE = os.path.join(BASE_DIR, "email_outbox.log")

def ensure_bank_config_file():
    if os.path.exists(BANK_CONFIG_FILE):
        return
    with open(BANK_CONFIG_FILE, "w", encoding="utf-8") as f:
        f.write(
            "BANK_ACCOUNT_TITLE=TutorPK Collections\n"
            "BANK_IBAN=PK47ASCM0000111000196711\n"
            "BANK_NAME=Askari Bank / Replace if needed\n"
            "CREDITS_RATE_PKR_PER_CREDIT=10\n"
            "ADMIN_NOTIFICATION_EMAIL=jojopk44@gmail.com\n"
            "ADMIN_WHATSAPP=+923558500230\n"
        )


def load_site_config() -> dict[str, str]:
    ensure_bank_config_file()
    config = {
        "BANK_ACCOUNT_TITLE": "TutorPK Collections",
        "BANK_IBAN": "PK47ASCM0000111000196711",
        "BANK_NAME": "Your Bank",
        "CREDITS_RATE_PKR_PER_CREDIT": "10",
        "ADMIN_NOTIFICATION_EMAIL": "jojopk44@gmail.com",
        "ADMIN_WHATSAPP": "+923558500230",
    }
    with open(BANK_CONFIG_FILE, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            config[key.strip()] = value.strip()
    return config


SITE_CONFIG = load_site_config()
CREDIT_RATE = max(1, int(SITE_CONFIG.get("CREDITS_RATE_PKR_PER_CREDIT", "10")))

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-this-in-production")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(BASE_DIR, "tutorpk_live_admin.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")
db = SQLAlchemy(app)

ACTIVE_CONNECTIONS: dict[str, dict] = {}
ACTIVE_ROOM_USERS: dict[str, set[int]] = {}

STOPWORDS = {
    "the", "a", "an", "and", "or", "to", "of", "in", "for", "is", "it", "on", "we", "i", "you",
    "this", "that", "with", "be", "are", "was", "were", "can", "from", "at", "as", "our", "your",
    "have", "has", "had", "will", "should", "need", "about", "let", "lets", "okay", "ok", "today",
    "tomorrow", "class", "session", "sir", "maam", "madam", "teacher", "student", "tutor", "please",
}

PAKISTAN_SUBJECTS = [
    "Matric Maths", "Matric Science", "FSc Pre-Medical", "FSc Pre-Engineering", "O Level Maths",
    "O Level Physics", "O Level Chemistry", "A Level Maths", "A Level Physics", "A Level Chemistry",
    "English", "Spoken English", "CSS Prep", "MDCAT Prep", "ECAT Prep", "Computer Science", "Quran / Nazra",
]

CITIES = [
    "Islamabad", "Rawalpindi", "Lahore", "Karachi", "Peshawar", "Multan", "Faisalabad", "Abbottabad", "Bannu", "Online Only",
]

ALLOWED_UPLOADS = {"pdf", "png", "jpg", "jpeg", "doc", "docx", "txt", "zip", "mp4", "webm", "wav", "mp3"}
CONTACT_PATTERNS = [
    (re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I), "email address"),
    (re.compile(r"(?:\+92|0092|0)?\d{3}[\s\-]?\d{7}"), "phone number"),
    (re.compile(r"\bwhats?app\b|wa\.me|t\.me|telegram|instagram|facebook|snapchat|discord", re.I), "personal channel reference"),
    (re.compile(r"\b(?:contact me|call me|message me|dm me|reach me)\b", re.I), "contact solicitation"),
]


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    city = db.Column(db.String(80), nullable=False)
    phone = db.Column(db.String(30), nullable=True)
    subject = db.Column(db.String(120), nullable=True)
    rate_pkr = db.Column(db.Integer, nullable=True)
    bio = db.Column(db.Text, nullable=True)
    parent_email = db.Column(db.String(120), nullable=True)
    credits_balance = db.Column(db.Integer, nullable=False, default=0)
    credits_held = db.Column(db.Integer, nullable=False, default=0)
    tutor_earnings_credits = db.Column(db.Integer, nullable=False, default=0)
    payout_method = db.Column(db.String(30), nullable=True)
    payout_account_title = db.Column(db.String(120), nullable=True)
    payout_account_number = db.Column(db.String(80), nullable=True)
    payout_iban = db.Column(db.String(60), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    slots = db.relationship("TutorSlot", backref="tutor", lazy=True, cascade="all, delete-orphan")
    bookings_as_student = db.relationship(
        "Booking", foreign_keys="Booking.student_id", backref="student", lazy=True, cascade="all, delete-orphan"
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class TutorSlot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tutor_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    title = db.Column(db.String(120), nullable=False)
    subject = db.Column(db.String(120), nullable=False)
    session_type = db.Column(db.String(30), nullable=False)
    start_time = db.Column(db.DateTime, nullable=False)
    duration_minutes = db.Column(db.Integer, nullable=False)
    fee_pkr = db.Column(db.Integer, nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    bookings = db.relationship("Booking", backref="slot", lazy=True, cascade="all, delete-orphan")


class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    slot_id = db.Column(db.Integer, db.ForeignKey("tutor_slot.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    note = db.Column(db.Text, nullable=True)
    payment_method = db.Column(db.String(40), nullable=False, default="Credits")
    payment_status = db.Column(db.String(30), default="Held in Escrow")
    booking_status = db.Column(db.String(30), default="Requested")
    credits_cost = db.Column(db.Integer, nullable=False, default=0)
    credits_released = db.Column(db.Boolean, nullable=False, default=False)
    student_marked_complete = db.Column(db.Boolean, nullable=False, default=False)
    tutor_marked_complete = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    session_room = db.relationship("SessionRoom", backref="booking", uselist=False, cascade="all, delete-orphan")
    quizzes = db.relationship("Quiz", backref="booking", lazy=True, cascade="all, delete-orphan")


class SessionRoom(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("booking.id"), unique=True, nullable=False)
    room_code = db.Column(db.String(36), unique=True, nullable=False)
    status = db.Column(db.String(20), default="scheduled")
    started_at = db.Column(db.DateTime, nullable=True)
    ended_at = db.Column(db.DateTime, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    playback_filename = db.Column(db.String(255), nullable=True)
    parent_access_token = db.Column(db.String(64), unique=True, nullable=False)
    whiteboard_state = db.Column(db.Text, default="[]")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    messages = db.relationship("SessionMessage", backref="room", lazy=True, cascade="all, delete-orphan")
    files = db.relationship("SessionFile", backref="room", lazy=True, cascade="all, delete-orphan")
    attendance = db.relationship("AttendanceEvent", backref="room", lazy=True, cascade="all, delete-orphan")
    flags = db.relationship("ModerationEvent", backref="room", lazy=True, cascade="all, delete-orphan")


class SessionMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.Integer, db.ForeignKey("session_room.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    body = db.Column(db.Text, nullable=False)
    kind = db.Column(db.String(20), default="chat")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User")


class SessionFile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.Integer, db.ForeignKey("session_room.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    original_name = db.Column(db.String(255), nullable=False)
    stored_name = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(30), default="session_file")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User")


class AttendanceEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.Integer, db.ForeignKey("session_room.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)
    left_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship("User")


class Quiz(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("booking.id"), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    created_by = db.relationship("User")
    questions = db.relationship("QuizQuestion", backref="quiz", lazy=True, cascade="all, delete-orphan")
    submissions = db.relationship("QuizSubmission", backref="quiz", lazy=True, cascade="all, delete-orphan")


class QuizQuestion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    quiz_id = db.Column(db.Integer, db.ForeignKey("quiz.id"), nullable=False)
    question_text = db.Column(db.Text, nullable=False)
    options_json = db.Column(db.Text, nullable=False)
    correct_index = db.Column(db.Integer, nullable=False)

    @property
    def options(self):
        return json.loads(self.options_json)


class QuizSubmission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    quiz_id = db.Column(db.Integer, db.ForeignKey("quiz.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    answers_json = db.Column(db.Text, nullable=False)
    score = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    student = db.relationship("User")


class PaymentNotice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    amount_pkr = db.Column(db.Integer, nullable=False)
    credits_requested = db.Column(db.Integer, nullable=False)
    transfer_method = db.Column(db.String(40), nullable=False, default="Bank Transfer")
    sender_name = db.Column(db.String(120), nullable=True)
    sender_account = db.Column(db.String(120), nullable=True)
    transfer_reference = db.Column(db.String(120), nullable=True)
    note = db.Column(db.Text, nullable=True)
    screenshot_filename = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(20), nullable=False, default="Pending")
    admin_note = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    reviewed_by_admin_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)

    student = db.relationship("User", foreign_keys=[student_id])
    reviewed_by_admin = db.relationship("User", foreign_keys=[reviewed_by_admin_id])


class CreditTransaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    credits_delta = db.Column(db.Integer, nullable=False)
    rupee_amount = db.Column(db.Integer, nullable=True)
    kind = db.Column(db.String(40), nullable=False)
    note = db.Column(db.String(255), nullable=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("booking.id"), nullable=True)
    payment_notice_id = db.Column(db.Integer, db.ForeignKey("payment_notice.id"), nullable=True)
    created_by_admin_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", foreign_keys=[user_id])
    created_by_admin = db.relationship("User", foreign_keys=[created_by_admin_id])


class WithdrawalRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tutor_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    credits_amount = db.Column(db.Integer, nullable=False)
    payout_method = db.Column(db.String(30), nullable=False)
    account_title = db.Column(db.String(120), nullable=True)
    account_number = db.Column(db.String(120), nullable=True)
    iban = db.Column(db.String(60), nullable=True)
    status = db.Column(db.String(20), nullable=False, default="Pending")
    admin_note = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    reviewed_by_admin_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)

    tutor = db.relationship("User", foreign_keys=[tutor_id])
    reviewed_by_admin = db.relationship("User", foreign_keys=[reviewed_by_admin_id])


class ModerationEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.Integer, db.ForeignKey("session_room.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    event_type = db.Column(db.String(40), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User")


@app.template_filter("dt")
def format_dt(value):
    if not value:
        return "—"
    return value.strftime("%d %b %Y, %I:%M %p")


@app.template_filter("short_dt")
def short_dt(value):
    if not value:
        return "—"
    return value.strftime("%d %b, %I:%M %p")


@app.template_filter("duration_minutes")
def duration_minutes(start, end):
    if not start or not end:
        return 0
    return int((end - start).total_seconds() // 60)


@app.template_filter("pkr")
def pkr_from_credits(value):
    return (value or 0) * CREDIT_RATE


@app.template_filter("credits_for_slot")
def credits_for_slot(slot):
    return credits_from_pkr(slot.fee_pkr)


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return db.session.get(User, user_id)


@app.context_processor
def inject_globals():
    return {
        "current_user": current_user(),
        "subjects": PAKISTAN_SUBJECTS,
        "cities": CITIES,
        "site_config": SITE_CONFIG,
        "credit_rate": CREDIT_RATE,
        "now": datetime.utcnow(),
    }


def login_required(role=None):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            user = current_user()
            if not user:
                flash("Please log in first.", "warning")
                return redirect(url_for("login"))
            if role and user.role != role:
                flash("You are not allowed to view that page.", "danger")
                return redirect(url_for("dashboard"))
            return f(*args, **kwargs)

        return wrapped

    return decorator


def credits_from_pkr(pkr: int | None) -> int:
    return math.ceil((pkr or 0) / CREDIT_RATE)


def can_access_booking(user: User | None, booking: Booking) -> bool:
    return bool(user and (booking.student_id == user.id or booking.slot.tutor_id == user.id or user.role == "admin"))


def can_access_room(user: User | None, room: SessionRoom) -> bool:
    return bool(user and can_access_booking(user, room.booking))


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_UPLOADS


def store_upload(upload, prefix: str):
    if not upload or not upload.filename:
        return None, None
    if not allowed_file(upload.filename):
        return None, None
    original = secure_filename(upload.filename)
    stored = f"{prefix}_{uuid.uuid4().hex}_{original}"
    upload.save(os.path.join(UPLOAD_DIR, stored))
    return original, stored


def create_room_for_booking(booking: Booking) -> SessionRoom:
    if booking.session_room:
        return booking.session_room
    room = SessionRoom(
        booking_id=booking.id,
        room_code=uuid.uuid4().hex[:12],
        parent_access_token=uuid.uuid4().hex + uuid.uuid4().hex,
        status="scheduled",
    )
    db.session.add(room)
    db.session.commit()
    return room


def summarize_topics(messages: list[str]) -> list[str]:
    words = []
    for line in messages:
        words.extend(re.findall(r"[a-zA-Z]{4,}", line.lower()))
    counts = Counter(word for word in words if word not in STOPWORDS)
    return [word.title() for word, _ in counts.most_common(6)]


def build_ai_notes(room: SessionRoom) -> str:
    messages = [m.body.strip() for m in room.messages if m.body.strip() and m.kind == "chat"]
    topics = summarize_topics(messages)
    action_lines = [
        m for m in messages if any(tag in m.lower() for tag in ["homework", "assignment", "practice", "revise", "quiz", "test", "next class"])
    ]
    attendance = []
    for item in room.attendance:
        if item.joined_at and item.left_at:
            attendance.append(f"{item.user.full_name}: {int((item.left_at - item.joined_at).total_seconds() // 60)} min")
    notes = [
        f"Subject: {room.booking.slot.subject}",
        f"Tutor: {room.booking.slot.tutor.full_name}",
        f"Student: {room.booking.student.full_name}",
        f"Session window: {format_dt(room.started_at or room.booking.slot.start_time)} → {format_dt(room.ended_at)}",
        f"Topics discussed: {', '.join(topics) if topics else 'No clear topics captured'}",
        "Action items:",
    ]
    if action_lines:
        notes.extend([f"- {line}" for line in action_lines[:6]])
    else:
        notes.append("- No explicit action items captured. Use chat and homework uploads for follow-up.")
    notes.append("Attendance:")
    if attendance:
        notes.extend([f"- {row}" for row in attendance])
    else:
        notes.append("- Attendance will appear after both participants join and leave the room.")
    notes.append("Quality assurance: admin observation is transparent and visible when active.")
    return "\n".join(notes)


def log_credit_transaction(user: User, delta: int, kind: str, note: str = "", rupee_amount: int | None = None,
                           booking: Booking | None = None, payment_notice: PaymentNotice | None = None,
                           admin: User | None = None):
    tx = CreditTransaction(
        user_id=user.id,
        credits_delta=delta,
        rupee_amount=rupee_amount,
        kind=kind,
        note=note,
        booking_id=booking.id if booking else None,
        payment_notice_id=payment_notice.id if payment_notice else None,
        created_by_admin_id=admin.id if admin else None,
    )
    db.session.add(tx)


def scan_contact_info(text: str):
    for pattern, label in CONTACT_PATTERNS:
        if pattern.search(text or ""):
            return label
    return None


def refund_booking_credits(booking: Booking, reason: str, admin: User | None = None):
    if booking.credits_released or booking.credits_cost <= 0:
        return
    student = booking.student
    student.credits_held = max(0, student.credits_held - booking.credits_cost)
    student.credits_balance += booking.credits_cost
    booking.payment_status = "Refunded"
    booking.booking_status = "Cancelled"
    booking.credits_cost = 0
    log_credit_transaction(student, booking.credits_cost, "booking_refund", note=reason, booking=booking, admin=admin)


def release_booking_credits(booking: Booking):
    if booking.credits_released or booking.credits_cost <= 0:
        return False
    student = booking.student
    tutor = booking.slot.tutor
    student.credits_held = max(0, student.credits_held - booking.credits_cost)
    tutor.tutor_earnings_credits += booking.credits_cost
    booking.credits_released = True
    booking.payment_status = "Released to Tutor"
    booking.booking_status = "Completed"
    log_credit_transaction(student, 0, "session_completed", note=f"Credits released to tutor for booking #{booking.id}", booking=booking)
    log_credit_transaction(tutor, booking.credits_cost, "tutor_earning", note=f"Session earning for booking #{booking.id}", booking=booking)
    return True


def notify_admin(subject: str, body: str) -> tuple[bool, str]:
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    smtp_sender = os.environ.get("SMTP_SENDER", smtp_user or "noreply@tutorpk.local")
    admin_email = SITE_CONFIG.get("ADMIN_NOTIFICATION_EMAIL", "jojopk44@gmail.com")

    if not smtp_host or not admin_email:
        with open(EMAIL_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n--- {datetime.utcnow().isoformat()} ---\nTO: {admin_email}\nSUBJECT: {subject}\n{body}\n")
        return False, f"SMTP not configured; email logged to {os.path.basename(EMAIL_LOG_FILE)}"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp_sender
    msg["To"] = admin_email
    msg.set_content(body)

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
            server.ehlo()
            if os.environ.get("SMTP_USE_TLS", "1") == "1":
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)
            server.send_message(msg)
        return True, "Email sent"
    except Exception as exc:  # noqa: BLE001
        with open(EMAIL_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n--- {datetime.utcnow().isoformat()} EMAIL ERROR {exc} ---\nTO: {admin_email}\nSUBJECT: {subject}\n{body}\n")
        return False, f"Email failed: {exc}"


def system_room_message(room: SessionRoom, body: str):
    msg = SessionMessage(room_id=room.id, user_id=None, body=body, kind="system")
    db.session.add(msg)
    db.session.commit()
    socketio.emit(
        "system_message",
        {"message": body, "created_at": format_dt(msg.created_at)},
        room=room.room_code,
    )


def active_users_for_room(room_code: str):
    ids = list(ACTIVE_ROOM_USERS.get(room_code, set()))
    if not ids:
        return []
    return User.query.filter(User.id.in_(ids)).all()


def observer_snapshot(room_code: str):
    users = [u for u in active_users_for_room(room_code) if u.role == "admin"]
    return {"count": len(users), "names": [u.full_name for u in users]}


def broadcast_observer_state(room_code: str):
    socketio.emit("observer_state", observer_snapshot(room_code), room=room_code)


def active_sids_for_user(room_code: str, user_id: int):
    return [sid for sid, state in ACTIVE_CONNECTIONS.items() if state.get("room_code") == room_code and state.get("user_id") == user_id]


@app.route("/")
def home():
    featured_tutors = User.query.filter_by(role="tutor").order_by(User.created_at.desc()).limit(6).all()
    latest_slots = TutorSlot.query.filter_by(is_active=True).order_by(TutorSlot.start_time.asc()).limit(6).all()
    live_sessions = SessionRoom.query.filter_by(status="live").order_by(SessionRoom.started_at.desc()).limit(4).all()
    return render_template("home.html", featured_tutors=featured_tutors, latest_slots=latest_slots, live_sessions=live_sessions)


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "")
        city = request.form.get("city", "")
        phone = request.form.get("phone", "").strip()
        subject = request.form.get("subject", "").strip() or None
        bio = request.form.get("bio", "").strip() or None
        rate_pkr = request.form.get("rate_pkr", "").strip()
        parent_email = request.form.get("parent_email", "").strip() or None

        if not full_name or not email or not password or role not in {"student", "tutor"} or not city:
            flash("Please fill all required fields.", "danger")
            return redirect(url_for("register"))
        if User.query.filter_by(email=email).first():
            flash("An account with this email already exists.", "danger")
            return redirect(url_for("register"))

        parsed_rate = None
        if role == "tutor" and rate_pkr:
            try:
                parsed_rate = int(rate_pkr)
            except ValueError:
                flash("Hourly rate must be numeric.", "danger")
                return redirect(url_for("register"))

        user = User(
            full_name=full_name,
            email=email,
            role=role,
            city=city,
            phone=phone,
            subject=subject,
            bio=bio,
            rate_pkr=parsed_rate,
            parent_email=parent_email,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash("Account created. Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            session["user_id"] = user.id
            flash(f"Welcome back, {user.full_name}.", "success")
            return redirect(url_for("dashboard"))
        flash("Invalid email or password.", "danger")
        return redirect(url_for("login"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("home"))


@app.route("/tutors")
def tutors():
    subject = request.args.get("subject", "").strip()
    city = request.args.get("city", "").strip()
    q = request.args.get("q", "").strip()

    query = User.query.filter_by(role="tutor")
    if subject:
        query = query.filter((User.subject.ilike(f"%{subject}%")) | (User.bio.ilike(f"%{subject}%")))
    if city:
        query = query.filter(User.city == city)
    if q:
        like = f"%{q}%"
        query = query.filter((User.full_name.ilike(like)) | (User.subject.ilike(like)) | (User.bio.ilike(like)))
    tutors_list = query.order_by(User.created_at.desc()).all()
    return render_template("tutors.html", tutors_list=tutors_list)


@app.route("/tutor/<int:tutor_id>")
def tutor_profile(tutor_id):
    tutor = User.query.filter_by(id=tutor_id, role="tutor").first_or_404()
    available_slots = TutorSlot.query.filter_by(tutor_id=tutor.id, is_active=True).order_by(TutorSlot.start_time.asc()).all()
    return render_template("tutor_profile.html", tutor=tutor, available_slots=available_slots)


@app.route("/dashboard")
@login_required()
def dashboard():
    user = current_user()
    if user.role == "admin":
        users = User.query.order_by(User.created_at.desc()).all()
        payment_notices = PaymentNotice.query.order_by(PaymentNotice.created_at.desc()).limit(50).all()
        withdrawals = WithdrawalRequest.query.order_by(WithdrawalRequest.created_at.desc()).limit(50).all()
        live_rooms = SessionRoom.query.filter_by(status="live").order_by(SessionRoom.started_at.desc()).all()
        flags = ModerationEvent.query.order_by(ModerationEvent.created_at.desc()).limit(50).all()
        bookings = Booking.query.order_by(Booking.created_at.desc()).limit(50).all()
        return render_template(
            "dashboard_admin.html",
            users=users,
            payment_notices=payment_notices,
            withdrawals=withdrawals,
            live_rooms=live_rooms,
            flags=flags,
            bookings=bookings,
        )
    if user.role == "tutor":
        slots = TutorSlot.query.filter_by(tutor_id=user.id).order_by(TutorSlot.start_time.desc()).all()
        bookings = Booking.query.join(TutorSlot).filter(TutorSlot.tutor_id == user.id).order_by(Booking.created_at.desc()).all()
        withdrawals = WithdrawalRequest.query.filter_by(tutor_id=user.id).order_by(WithdrawalRequest.created_at.desc()).all()
        return render_template("dashboard_tutor.html", slots=slots, bookings=bookings, withdrawals=withdrawals)
    bookings = Booking.query.filter_by(student_id=user.id).order_by(Booking.created_at.desc()).all()
    notices = PaymentNotice.query.filter_by(student_id=user.id).order_by(PaymentNotice.created_at.desc()).all()
    txs = CreditTransaction.query.filter_by(user_id=user.id).order_by(CreditTransaction.created_at.desc()).limit(50).all()
    return render_template("dashboard_student.html", bookings=bookings, notices=notices, txs=txs)


@app.route("/credits/buy", methods=["GET", "POST"])
@login_required(role="student")
def buy_credits():
    user = current_user()
    if request.method == "POST":
        try:
            amount_pkr = int(request.form.get("amount_pkr", "0"))
        except ValueError:
            amount_pkr = 0
        if amount_pkr < CREDIT_RATE:
            flash(f"Minimum top-up is PKR {CREDIT_RATE}.", "danger")
            return redirect(url_for("buy_credits"))
        sender_name = request.form.get("sender_name", "").strip() or None
        sender_account = request.form.get("sender_account", "").strip() or None
        transfer_reference = request.form.get("transfer_reference", "").strip() or None
        note = request.form.get("note", "").strip() or None
        credits_requested = amount_pkr // CREDIT_RATE
        original, stored = store_upload(request.files.get("screenshot"), f"payment_{user.id}")
        notice = PaymentNotice(
            student_id=user.id,
            amount_pkr=amount_pkr,
            credits_requested=credits_requested,
            transfer_method="Bank Transfer",
            sender_name=sender_name,
            sender_account=sender_account,
            transfer_reference=transfer_reference,
            note=note,
            screenshot_filename=stored,
        )
        db.session.add(notice)
        db.session.commit()

        email_body = (
            f"New TutorPK credit request\n\n"
            f"Student: {user.full_name} ({user.email})\n"
            f"Amount sent: PKR {amount_pkr}\n"
            f"Credits requested: {credits_requested}\n"
            f"Sender name: {sender_name or '-'}\n"
            f"Sender account: {sender_account or '-'}\n"
            f"Transfer reference: {transfer_reference or '-'}\n"
            f"Note: {note or '-'}\n"
            f"Payment notice ID: {notice.id}\n\n"
            f"Student has also been instructed to send screenshot on WhatsApp {SITE_CONFIG.get('ADMIN_WHATSAPP')}"
        )
        ok, detail = notify_admin(f"TutorPK payment notice #{notice.id}", email_body)
        flash("Payment notice submitted. Admin will verify and add credits manually.", "success")
        flash(detail if ok else f"Notice saved. {detail}", "info")
        return redirect(url_for("dashboard"))

    return render_template("buy_credits.html")


@app.route("/uploads/public/<filename>")
@login_required(role="admin")
def admin_uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename, as_attachment=False)


@app.route("/slots/new", methods=["POST"])
@login_required(role="tutor")
def create_slot():
    user = current_user()
    title = request.form.get("title", "").strip()
    subject = request.form.get("subject", "").strip()
    session_type = request.form.get("session_type", "").strip()
    start_time = request.form.get("start_time", "").strip()
    duration_minutes = request.form.get("duration_minutes", "").strip()
    fee_pkr = request.form.get("fee_pkr", "").strip()

    if not all([title, subject, session_type, start_time, duration_minutes, fee_pkr]):
        flash("Please complete all slot fields.", "danger")
        return redirect(url_for("dashboard"))
    try:
        parsed_start = datetime.strptime(start_time, "%Y-%m-%dT%H:%M")
        parsed_duration = int(duration_minutes)
        parsed_fee = int(fee_pkr)
    except ValueError:
        flash("Invalid slot values.", "danger")
        return redirect(url_for("dashboard"))

    slot = TutorSlot(
        tutor_id=user.id,
        title=title,
        subject=subject,
        session_type=session_type,
        start_time=parsed_start,
        duration_minutes=parsed_duration,
        fee_pkr=parsed_fee,
    )
    db.session.add(slot)
    db.session.commit()
    flash("Slot created successfully.", "success")
    return redirect(url_for("dashboard"))


@app.route("/slots/<int:slot_id>/toggle")
@login_required(role="tutor")
def toggle_slot(slot_id):
    slot = TutorSlot.query.get_or_404(slot_id)
    if slot.tutor_id != current_user().id:
        flash("Unauthorized action.", "danger")
        return redirect(url_for("dashboard"))
    slot.is_active = not slot.is_active
    db.session.commit()
    flash("Slot status updated.", "info")
    return redirect(url_for("dashboard"))


@app.route("/book/<int:slot_id>", methods=["POST"])
@login_required(role="student")
def book_slot(slot_id):
    slot = TutorSlot.query.get_or_404(slot_id)
    user = current_user()
    if not slot.is_active:
        flash("This slot is no longer available.", "warning")
        return redirect(url_for("tutor_profile", tutor_id=slot.tutor_id))
    existing = Booking.query.filter_by(slot_id=slot.id, student_id=user.id).first()
    if existing:
        flash("You already booked this slot.", "warning")
        return redirect(url_for("dashboard"))

    note = request.form.get("note", "").strip()
    violation = scan_contact_info(note)
    if violation:
        flash(f"Booking note blocked: {violation} sharing is not allowed.", "danger")
        return redirect(url_for("tutor_profile", tutor_id=slot.tutor_id))

    needed_credits = credits_from_pkr(slot.fee_pkr)
    if user.credits_balance < needed_credits:
        flash(f"You need {needed_credits} credits to book this slot. Please top up first.", "warning")
        return redirect(url_for("buy_credits"))

    user.credits_balance -= needed_credits
    user.credits_held += needed_credits
    booking = Booking(
        slot_id=slot.id,
        student_id=user.id,
        note=note,
        payment_method="Credits",
        payment_status="Held in Escrow",
        booking_status="Requested",
        credits_cost=needed_credits,
    )
    db.session.add(booking)
    db.session.flush()
    log_credit_transaction(user, -needed_credits, "booking_hold", note=f"Held for booking #{booking.id}", rupee_amount=slot.fee_pkr, booking=booking)
    db.session.commit()
    flash("Booking request submitted. Credits are held in escrow until the session is completed.", "success")
    return redirect(url_for("dashboard"))


@app.route("/bookings/<int:booking_id>/status", methods=["POST"])
@login_required()
def update_booking_status(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    user = current_user()
    allowed = booking.slot.tutor_id == user.id or user.role == "admin"
    if not allowed:
        flash("Unauthorized action.", "danger")
        return redirect(url_for("dashboard"))

    requested_status = request.form.get("booking_status", booking.booking_status)
    if requested_status in {"Approved", "Live", "Completed", "Cancelled"}:
        booking.booking_status = requested_status
    if booking.booking_status in {"Approved", "Live", "Completed"}:
        create_room_for_booking(booking)
    if booking.booking_status == "Cancelled" and booking.credits_cost > 0 and not booking.credits_released:
        credits = booking.credits_cost
        booking.student.credits_held = max(0, booking.student.credits_held - credits)
        booking.student.credits_balance += credits
        booking.payment_status = "Refunded"
        log_credit_transaction(booking.student, credits, "booking_refund", note=f"Booking #{booking.id} cancelled", booking=booking, admin=user if user.role == 'admin' else None)
        booking.credits_cost = 0
    if booking.booking_status == "Completed" and booking.session_room:
        room = booking.session_room
        room.status = "completed"
        room.ended_at = room.ended_at or datetime.utcnow()
        room.notes = room.notes or build_ai_notes(room)
    db.session.commit()
    flash("Booking updated.", "success")
    return redirect(url_for("dashboard"))


@app.route("/bookings/<int:booking_id>/complete/student", methods=["POST"])
@login_required(role="student")
def student_complete_booking(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    if booking.student_id != current_user().id:
        flash("Unauthorized action.", "danger")
        return redirect(url_for("dashboard"))
    booking.student_marked_complete = True
    booking.booking_status = "Completed"
    if booking.session_room:
        booking.session_room.status = "completed"
        booking.session_room.ended_at = booking.session_room.ended_at or datetime.utcnow()
        booking.session_room.notes = build_ai_notes(booking.session_room)
    if release_booking_credits(booking):
        flash("Session completed and tutor credited.", "success")
    else:
        flash("Session marked complete.", "success")
    db.session.commit()
    return redirect(url_for("dashboard"))


@app.route("/bookings/<int:booking_id>/complete/tutor", methods=["POST"])
@login_required(role="tutor")
def tutor_complete_booking(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    if booking.slot.tutor_id != current_user().id:
        flash("Unauthorized action.", "danger")
        return redirect(url_for("dashboard"))
    booking.tutor_marked_complete = True
    db.session.commit()
    flash("Tutor completion recorded.", "success")
    return redirect(url_for("dashboard"))


@app.route("/bookings/<int:booking_id>/start")
@login_required()
def start_session(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    user = current_user()
    if booking.slot.tutor_id != user.id and user.role != "admin":
        flash("Unauthorized action.", "danger")
        return redirect(url_for("dashboard"))
    room = create_room_for_booking(booking)
    booking.booking_status = "Live"
    room.status = "live"
    room.started_at = room.started_at or datetime.utcnow()
    db.session.commit()
    return redirect(url_for("join_session", room_code=room.room_code))


@app.route("/rooms/<room_code>")
@login_required()
def join_session(room_code):
    room = SessionRoom.query.filter_by(room_code=room_code).first_or_404()
    user = current_user()
    if not can_access_room(user, room):
        flash("You cannot access this room.", "danger")
        return redirect(url_for("dashboard"))
    files = SessionFile.query.filter_by(room_id=room.id).order_by(SessionFile.created_at.desc()).all()
    messages = SessionMessage.query.filter_by(room_id=room.id).order_by(SessionMessage.created_at.asc()).limit(200).all()
    quizzes = Quiz.query.filter_by(booking_id=room.booking_id).order_by(Quiz.created_at.desc()).all()
    return render_template("room.html", room=room, files=files, messages=messages, quizzes=quizzes, observer_state=observer_snapshot(room_code))


@app.route("/rooms/<room_code>/complete", methods=["POST"])
@login_required(role="tutor")
def complete_room(room_code):
    room = SessionRoom.query.filter_by(room_code=room_code).first_or_404()
    if room.booking.slot.tutor_id != current_user().id:
        flash("Unauthorized action.", "danger")
        return redirect(url_for("dashboard"))
    room.status = "completed"
    room.ended_at = datetime.utcnow()
    room.booking.booking_status = "Awaiting Student Confirmation"
    room.booking.tutor_marked_complete = True
    room.notes = build_ai_notes(room)
    for item in room.attendance:
        if not item.left_at:
            item.left_at = datetime.utcnow()
    db.session.commit()
    flash("Session ended. Waiting for student completion to release credits to tutor.", "success")
    return redirect(url_for("join_session", room_code=room.room_code))


@app.route("/rooms/<room_code>/files", methods=["POST"])
@login_required()
def upload_room_file(room_code):
    room = SessionRoom.query.filter_by(room_code=room_code).first_or_404()
    user = current_user()
    if not can_access_room(user, room):
        flash("Unauthorized.", "danger")
        return redirect(url_for("dashboard"))
    upload = request.files.get("file")
    original, stored = store_upload(upload, f"room_{room.id}")
    if not stored:
        flash("Unsupported file or no file selected.", "warning")
        return redirect(url_for("join_session", room_code=room_code))
    category = request.form.get("category", "session_file")
    item = SessionFile(room_id=room.id, user_id=user.id, original_name=original, stored_name=stored, category=category)
    db.session.add(item)
    db.session.commit()
    flash("File uploaded.", "success")
    return redirect(url_for("join_session", room_code=room_code))


@app.route("/rooms/<room_code>/recording", methods=["POST"])
@login_required()
def upload_recording(room_code):
    room = SessionRoom.query.filter_by(room_code=room_code).first_or_404()
    if not can_access_room(current_user(), room):
        flash("Unauthorized.", "danger")
        return redirect(url_for("dashboard"))
    upload = request.files.get("recording")
    original, stored = store_upload(upload, f"recording_{room.id}")
    if not stored:
        flash("Recording upload failed. Use webm/mp4.", "warning")
        return redirect(url_for("join_session", room_code=room_code))
    room.playback_filename = stored
    db.session.add(SessionFile(room_id=room.id, user_id=current_user().id, original_name=original, stored_name=stored, category="recording"))
    db.session.commit()
    flash("Recording saved for playback.", "success")
    return redirect(url_for("join_session", room_code=room_code))


@app.route("/bookings/<int:booking_id>/quiz", methods=["POST"])
@login_required(role="tutor")
def create_quiz(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    if booking.slot.tutor_id != current_user().id:
        flash("Unauthorized.", "danger")
        return redirect(url_for("dashboard"))
    title = request.form.get("title", "").strip() or f"{booking.slot.subject} Quiz"
    quiz = Quiz(booking_id=booking.id, title=title, created_by_id=current_user().id)
    db.session.add(quiz)
    db.session.flush()
    created = 0
    for idx in range(1, 4):
        question_text = request.form.get(f"question_{idx}", "").strip()
        options = [request.form.get(f"q{idx}_opt_{n}", "").strip() for n in range(1, 5)]
        correct_index = request.form.get(f"q{idx}_correct", "1")
        if question_text and all(options):
            db.session.add(
                QuizQuestion(
                    quiz_id=quiz.id,
                    question_text=question_text,
                    options_json=json.dumps(options),
                    correct_index=max(0, min(3, int(correct_index) - 1)),
                )
            )
            created += 1
    if not created:
        db.session.rollback()
        flash("Add at least one full MCQ.", "warning")
        return redirect(url_for("dashboard"))
    db.session.commit()
    flash("Quiz created.", "success")
    return redirect(url_for("dashboard"))


@app.route("/quiz/<int:quiz_id>/submit", methods=["POST"])
@login_required(role="student")
def submit_quiz(quiz_id):
    quiz = Quiz.query.get_or_404(quiz_id)
    if quiz.booking.student_id != current_user().id:
        flash("Unauthorized.", "danger")
        return redirect(url_for("dashboard"))
    answers = {}
    score = 0
    for question in quiz.questions:
        selected = request.form.get(f"question_{question.id}")
        answers[str(question.id)] = selected
        if selected is not None and int(selected) == question.correct_index:
            score += 1
    submission = QuizSubmission(
        quiz_id=quiz.id,
        student_id=current_user().id,
        answers_json=json.dumps(answers),
        score=score,
    )
    db.session.add(submission)
    db.session.commit()
    flash(f"Quiz submitted. Score: {score}/{len(quiz.questions)}", "success")
    room = quiz.booking.session_room
    if room:
        return redirect(url_for("join_session", room_code=room.room_code))
    return redirect(url_for("dashboard"))


@app.route("/playback/<room_code>")
@login_required()
def playback(room_code):
    room = SessionRoom.query.filter_by(room_code=room_code).first_or_404()
    if not can_access_room(current_user(), room):
        flash("Unauthorized.", "danger")
        return redirect(url_for("dashboard"))
    return render_template("playback.html", room=room)


@app.route("/parent/<token>")
def parent_view(token):
    room = SessionRoom.query.filter_by(parent_access_token=token).first_or_404()
    return render_template("parent_view.html", room=room)


@app.route("/uploads/<filename>")
@login_required()
def uploaded_file(filename):
    file_record = SessionFile.query.filter_by(stored_name=filename).first()
    room = SessionRoom.query.filter_by(playback_filename=filename).first()
    user = current_user()
    if file_record and can_access_room(user, file_record.room):
        return send_from_directory(UPLOAD_DIR, filename, as_attachment=False)
    if room and can_access_room(user, room):
        return send_from_directory(UPLOAD_DIR, filename, as_attachment=False)
    flash("Unauthorized.", "danger")
    return redirect(url_for("dashboard"))


@app.route("/tutor/payout-settings", methods=["POST"])
@login_required(role="tutor")
def update_payout_settings():
    user = current_user()
    user.payout_method = request.form.get("payout_method", "Bank")
    user.payout_account_title = request.form.get("payout_account_title", "").strip() or None
    user.payout_account_number = request.form.get("payout_account_number", "").strip() or None
    user.payout_iban = request.form.get("payout_iban", "").strip() or None
    db.session.commit()
    flash("Payout settings updated.", "success")
    return redirect(url_for("dashboard"))


@app.route("/tutor/withdraw", methods=["POST"])
@login_required(role="tutor")
def request_withdrawal():
    user = current_user()
    try:
        credits_amount = int(request.form.get("credits_amount", "0"))
    except ValueError:
        credits_amount = 0
    if credits_amount <= 0:
        flash("Invalid withdrawal amount.", "danger")
        return redirect(url_for("dashboard"))
    if credits_amount > user.tutor_earnings_credits:
        flash("Not enough tutor earnings.", "danger")
        return redirect(url_for("dashboard"))
    if not user.payout_method or not (user.payout_account_number or user.payout_iban):
        flash("Please save payout settings first.", "warning")
        return redirect(url_for("dashboard"))

    user.tutor_earnings_credits -= credits_amount
    withdrawal = WithdrawalRequest(
        tutor_id=user.id,
        credits_amount=credits_amount,
        payout_method=user.payout_method,
        account_title=user.payout_account_title,
        account_number=user.payout_account_number,
        iban=user.payout_iban,
        status="Pending",
    )
    db.session.add(withdrawal)
    log_credit_transaction(user, -credits_amount, "withdrawal_request", note="Withdrawal requested")
    db.session.commit()
    flash("Withdrawal request submitted. Admin will review and pay manually.", "success")
    return redirect(url_for("dashboard"))


@app.route("/admin/payment-notices/<int:notice_id>/approve", methods=["POST"])
@login_required(role="admin")
def approve_payment_notice(notice_id):
    notice = PaymentNotice.query.get_or_404(notice_id)
    admin = current_user()
    if notice.status == "Approved":
        flash("Payment notice already approved.", "info")
        return redirect(url_for("dashboard"))
    notice.status = "Approved"
    notice.admin_note = request.form.get("admin_note", "").strip() or None
    notice.reviewed_at = datetime.utcnow()
    notice.reviewed_by_admin_id = admin.id
    notice.student.credits_balance += notice.credits_requested
    log_credit_transaction(
        notice.student,
        notice.credits_requested,
        "manual_topup_approved",
        note=f"Payment notice #{notice.id} approved",
        rupee_amount=notice.amount_pkr,
        payment_notice=notice,
        admin=admin,
    )
    db.session.commit()
    flash("Credits added to student.", "success")
    return redirect(url_for("dashboard"))


@app.route("/admin/payment-notices/<int:notice_id>/reject", methods=["POST"])
@login_required(role="admin")
def reject_payment_notice(notice_id):
    notice = PaymentNotice.query.get_or_404(notice_id)
    notice.status = "Rejected"
    notice.admin_note = request.form.get("admin_note", "").strip() or None
    notice.reviewed_at = datetime.utcnow()
    notice.reviewed_by_admin_id = current_user().id
    db.session.commit()
    flash("Payment notice rejected.", "warning")
    return redirect(url_for("dashboard"))


@app.route("/admin/users/<int:user_id>/credits", methods=["POST"])
@login_required(role="admin")
def adjust_user_credits(user_id):
    target = User.query.get_or_404(user_id)
    admin = current_user()
    try:
        credits_delta = int(request.form.get("credits_delta", "0"))
    except ValueError:
        credits_delta = 0
    if credits_delta == 0:
        flash("Credit adjustment cannot be zero.", "danger")
        return redirect(url_for("dashboard"))
    target.credits_balance = max(0, target.credits_balance + credits_delta)
    log_credit_transaction(target, credits_delta, "admin_adjustment", note=request.form.get("note", "Manual credit adjustment"), admin=admin)
    db.session.commit()
    flash("User credits adjusted.", "success")
    return redirect(url_for("dashboard"))


@app.route("/admin/withdrawals/<int:withdrawal_id>/approve", methods=["POST"])
@login_required(role="admin")
def approve_withdrawal(withdrawal_id):
    withdrawal = WithdrawalRequest.query.get_or_404(withdrawal_id)
    withdrawal.status = "Paid"
    withdrawal.admin_note = request.form.get("admin_note", "").strip() or None
    withdrawal.reviewed_at = datetime.utcnow()
    withdrawal.reviewed_by_admin_id = current_user().id
    db.session.commit()
    flash("Withdrawal marked paid.", "success")
    return redirect(url_for("dashboard"))


@app.route("/admin/withdrawals/<int:withdrawal_id>/reject", methods=["POST"])
@login_required(role="admin")
def reject_withdrawal(withdrawal_id):
    withdrawal = WithdrawalRequest.query.get_or_404(withdrawal_id)
    if withdrawal.status != "Pending":
        flash("Only pending withdrawals can be rejected.", "warning")
        return redirect(url_for("dashboard"))
    tutor = withdrawal.tutor
    tutor.tutor_earnings_credits += withdrawal.credits_amount
    withdrawal.status = "Rejected"
    withdrawal.admin_note = request.form.get("admin_note", "").strip() or None
    withdrawal.reviewed_at = datetime.utcnow()
    withdrawal.reviewed_by_admin_id = current_user().id
    log_credit_transaction(tutor, withdrawal.credits_amount, "withdrawal_reversed", note=f"Withdrawal #{withdrawal.id} rejected", admin=current_user())
    db.session.commit()
    flash("Withdrawal rejected and credits returned to tutor earnings.", "warning")
    return redirect(url_for("dashboard"))


@app.route("/seed")
def seed():
    if User.query.first():
        flash("Database already has data.", "info")
        return redirect(url_for("home"))

    admin = User(
        full_name="TutorPK Admin",
        email=SITE_CONFIG.get("ADMIN_NOTIFICATION_EMAIL", "jojopk44@gmail.com"),
        role="admin",
        city="Islamabad",
        phone=SITE_CONFIG.get("ADMIN_WHATSAPP", "+923558500230"),
        bio="Platform admin account with moderation and payment approval access.",
    )
    admin.set_password("admin123")

    tutor_1 = User(
        full_name="Ayesha Khan",
        email="ayesha@example.com",
        role="tutor",
        city="Islamabad",
        phone="0300-1112233",
        subject="O Level Maths, A Level Maths",
        rate_pkr=3500,
        bio="Experienced online maths tutor for O/A Levels and entry test prep across Pakistan.",
        payout_method="Bank",
        payout_account_title="Ayesha Khan",
        payout_account_number="03001112233",
    )
    tutor_1.set_password("password123")

    tutor_2 = User(
        full_name="Usman Ali",
        email="usman@example.com",
        role="tutor",
        city="Lahore",
        phone="0312-9988776",
        subject="FSc Physics, ECAT Prep",
        rate_pkr=2500,
        bio="Physics specialist offering Urdu and English medium classes with weekly tests.",
        payout_method="Easypaisa",
        payout_account_title="Usman Ali",
        payout_account_number="03129988776",
    )
    tutor_2.set_password("password123")

    student = User(
        full_name="Hamza Student",
        email="hamza@example.com",
        role="student",
        city="Rawalpindi",
        phone="0333-9988776",
        bio="Looking for online tutoring support.",
        parent_email="parent@example.com",
        credits_balance=500,
    )
    student.set_password("password123")

    db.session.add_all([admin, tutor_1, tutor_2, student])
    db.session.commit()

    slot_1 = TutorSlot(
        tutor_id=tutor_1.id,
        title="A Level Maths Live Session",
        subject="A Level Maths",
        session_type="Online",
        start_time=datetime.utcnow().replace(hour=16, minute=0, second=0, microsecond=0),
        duration_minutes=60,
        fee_pkr=3000,
    )
    slot_2 = TutorSlot(
        tutor_id=tutor_2.id,
        title="ECAT Physics Crash Session",
        subject="ECAT Prep",
        session_type="Online",
        start_time=datetime.utcnow().replace(hour=18, minute=30, second=0, microsecond=0),
        duration_minutes=90,
        fee_pkr=2200,
    )
    db.session.add_all([slot_1, slot_2])
    db.session.commit()

    credits_needed = credits_from_pkr(slot_1.fee_pkr)
    student.credits_balance -= credits_needed
    student.credits_held += credits_needed
    booking = Booking(
        slot_id=slot_1.id,
        student_id=student.id,
        note="Need help with calculus and differentiation.",
        payment_method="Credits",
        payment_status="Held in Escrow",
        booking_status="Approved",
        credits_cost=credits_needed,
    )
    db.session.add(booking)
    db.session.flush()
    log_credit_transaction(student, -credits_needed, "booking_hold", note=f"Held for booking #{booking.id}", rupee_amount=slot_1.fee_pkr, booking=booking)
    db.session.commit()
    room = create_room_for_booking(booking)
    room.notes = "Demo room ready. Start session to test calling, whiteboard, chat moderation, admin observer mode, recording, homework and quizzes."
    db.session.commit()

    flash(
        "Demo data added. Admin: " + admin.email + " / admin123 | Student: hamza@example.com / password123 | Tutor: ayesha@example.com / password123",
        "success",
    )
    return redirect(url_for("home"))


@socketio.on("join_room")
def socket_join_room(data):
    user = current_user()
    if not user:
        return
    room_code = data.get("room_code")
    room = SessionRoom.query.filter_by(room_code=room_code).first()
    if not room or not can_access_room(user, room):
        return
    join_room(room_code)

    ACTIVE_ROOM_USERS.setdefault(room_code, set()).add(user.id)
    attendance = AttendanceEvent(room_id=room.id, user_id=user.id, joined_at=datetime.utcnow())
    db.session.add(attendance)
    db.session.commit()
    ACTIVE_CONNECTIONS[request.sid] = {"room_code": room_code, "attendance_id": attendance.id, "user_id": user.id}

    if room.status == "scheduled" and user.role != "admin":
        room.status = "live"
        room.started_at = room.started_at or datetime.utcnow()
        room.booking.booking_status = "Live"
        db.session.commit()

    others = [
        {"user_id": active.id, "name": active.full_name, "role": active.role}
        for active in active_users_for_room(room_code)
        if active.id != user.id
    ]
    emit("room_state", {"users": others, "whiteboard": json.loads(room.whiteboard_state or "[]"), "observers": observer_snapshot(room_code)})
    emit("user_joined", {"user_id": user.id, "name": user.full_name, "role": user.role}, room=room_code, include_self=False)

    if user.role == "admin":
        system_room_message(room, f"Admin observer {user.full_name} joined for quality assurance.")
        broadcast_observer_state(room_code)


@socketio.on("signal")
def socket_signal(data):
    user = current_user()
    if not user:
        return
    room_code = data.get("room_code")
    target_user_id = data.get("target_user_id")
    payload = data.get("payload")
    if not room_code or not target_user_id or payload is None:
        return
    for sid in active_sids_for_user(room_code, int(target_user_id)):
        socketio.emit("signal", {"sender": user.id, "sender_name": user.full_name, "sender_role": user.role, "payload": payload}, to=sid)


@socketio.on("chat_message")
def socket_chat(data):
    user = current_user()
    if not user:
        return
    room_code = data.get("room_code")
    room = SessionRoom.query.filter_by(room_code=room_code).first()
    if not room or not can_access_room(user, room):
        return
    body = (data.get("message") or "").strip()
    if not body:
        return
    violation = scan_contact_info(body)
    if violation:
        flag = ModerationEvent(room_id=room.id, user_id=user.id, event_type="blocked_chat", content=body)
        db.session.add(flag)
        db.session.commit()
        emit("policy_warning", {"message": f"Message blocked. Sharing {violation} is not allowed. Stay on-platform for calls and payments."})
        return
    message = SessionMessage(room_id=room.id, user_id=user.id, body=body, kind="chat")
    db.session.add(message)
    db.session.commit()
    emit(
        "chat_message",
        {
            "sender": user.full_name,
            "kind": "chat",
            "user_id": user.id,
            "message": body,
            "created_at": format_dt(message.created_at),
        },
        room=room_code,
    )


@socketio.on("whiteboard_draw")
def socket_whiteboard_draw(data):
    user = current_user()
    if not user:
        return
    room_code = data.get("room_code")
    room = SessionRoom.query.filter_by(room_code=room_code).first()
    if not room or not can_access_room(user, room):
        return
    event = data.get("event") or {}
    history = json.loads(room.whiteboard_state or "[]")
    history.append(event)
    room.whiteboard_state = json.dumps(history[-400:])
    db.session.commit()
    emit("whiteboard_draw", event, room=room_code, include_self=False)


@socketio.on("whiteboard_clear")
def socket_whiteboard_clear(data):
    user = current_user()
    if not user:
        return
    room_code = data.get("room_code")
    room = SessionRoom.query.filter_by(room_code=room_code).first()
    if not room or not can_access_room(user, room):
        return
    room.whiteboard_state = "[]"
    db.session.commit()
    emit("whiteboard_clear", room=room_code)


@socketio.on("disconnect")
def socket_disconnect():
    state = ACTIVE_CONNECTIONS.pop(request.sid, None)
    if not state:
        return
    room_code = state["room_code"]
    attendance = db.session.get(AttendanceEvent, state["attendance_id"])
    user = db.session.get(User, state["user_id"])
    room = SessionRoom.query.filter_by(room_code=room_code).first()
    if attendance and not attendance.left_at:
        attendance.left_at = datetime.utcnow()
        db.session.commit()
    ACTIVE_ROOM_USERS.get(room_code, set()).discard(state["user_id"])
    emit("user_left", {"user_id": state["user_id"]}, room=room_code)
    if user and user.role == "admin" and room:
        system_room_message(room, f"Admin observer {user.full_name} left the room.")
        broadcast_observer_state(room_code)


with app.app_context():
    db.create_all()


if __name__ == "__main__":
    socketio.run(app, debug=True)
