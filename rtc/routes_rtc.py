from datetime import datetime, timedelta

from pathlib import Path
from uuid import uuid4
import sys


from flask import Blueprint, jsonify, request, current_app
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from rtc.agora_service import AgoraConfigError, get_join_payload_for_user
from rtc.chat_guard import message_contains_blocked_contact_info
from rtc.models_rtc import SessionMessage

rtc_bp = Blueprint("rtc", __name__, url_prefix="/rtc")


def _get_app_objects():
    rtc_models = current_app.extensions.get("rtc_models")
    if not rtc_models:
        raise RuntimeError("RTC models are not registered on current_app.extensions['rtc_models'].")

    db = rtc_models.get("db")
    Booking = rtc_models.get("Booking")
    LiveSessionLog = rtc_models.get("LiveSessionLog")

    if not all([db, Booking, LiveSessionLog]):
        raise RuntimeError("RTC model registry is incomplete.")

    return db, Booking, LiveSessionLog

def _get_booking_or_404(booking_id: int):
    db, Booking, _ = _get_app_objects()
    booking = db.session.get(Booking, booking_id)
    if not booking:
        return None, jsonify({"ok": False, "error": "Booking not found"}), 404
    return booking, None, None


def _get_or_create_live_log(booking):
    db, _, LiveSessionLog = _get_app_objects()
    log = LiveSessionLog.query.filter_by(booking_id=booking.id).first()
    if not log:
        log = LiveSessionLog(
            booking_id=booking.id,
            room_code=booking.room_code,
            started_at=None,
            ended_at=None,
            student_joined=False,
            tutor_joined=False,
            admin_joined=False,
            last_activity_note="Session created",
        )
        db.session.add(log)
        db.session.flush()
    return log


def _user_can_access_booking(booking) -> bool:
    if not current_user.is_authenticated:
        return False

    if current_user.role == "admin":
        return True

    return current_user.id in {booking.student_id, booking.tutor_id}


def _mark_connected(log, booking):
    now = datetime.utcnow()

    if current_user.role == "admin":
        log.admin_joined = True
        log.last_activity_note = f"Admin connected at {now.isoformat()} UTC"
    elif current_user.id == booking.student_id:
        log.student_joined = True
        log.last_activity_note = f"Student connected at {now.isoformat()} UTC"
    elif current_user.id == booking.tutor_id:
        log.tutor_joined = True
        log.last_activity_note = f"Tutor connected at {now.isoformat()} UTC"

    if not log.started_at:
        log.started_at = now

    log.ended_at = None


def _mark_left(log, booking):
    now = datetime.utcnow()

    if current_user.role == "admin":
        log.admin_joined = False
        log.last_activity_note = f"Admin left at {now.isoformat()} UTC"
    elif current_user.id == booking.student_id:
        log.student_joined = False
        log.last_activity_note = f"Student left at {now.isoformat()} UTC"
    elif current_user.id == booking.tutor_id:
        log.tutor_joined = False
        log.last_activity_note = f"Tutor left at {now.isoformat()} UTC"

    if not log.student_joined and not log.tutor_joined and not log.admin_joined:
        log.ended_at = now


def _sync_booking_status_from_log(booking, log):
    anyone_present = bool(log.student_joined or log.tutor_joined or log.admin_joined)

    if anyone_present and booking.status == "scheduled":
        booking.status = "live"

    if not anyone_present and booking.status == "live":
        booking.status = "scheduled"

def _session_window_status(booking, early_minutes=10, late_minutes=10):
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

@rtc_bp.route("/join/<int:booking_id>", methods=["POST"])
@login_required
def rtc_join(booking_id):
    booking, error_response, status_code = _get_booking_or_404(booking_id)
    if error_response:
        return error_response, status_code

    if not _user_can_access_booking(booking):
        return jsonify({"ok": False, "error": "Not authorized for this session"}), 403

    window_status, allowed_start, allowed_end = _session_window_status(booking)

    if current_user.role != "admin" and window_status == "too_early":
        return jsonify({
            "ok": False,
            "error": f"Session is not open yet. It opens at {allowed_start.strftime('%d %b %Y, %I:%M %p')}.",
            "window_status": window_status,
        }), 403

    if current_user.role != "admin" and window_status == "expired":
        return jsonify({
            "ok": False,
            "error": "This session time has expired.",
            "window_status": window_status,
        }), 403

    try:
        payload = get_join_payload_for_user(booking=booking, user=current_user)
    except AgoraConfigError as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": f"Failed to generate token: {str(e)}"}), 500

    return jsonify(
        {
            "ok": True,
            "booking_id": booking.id,
            "room_code": booking.room_code,
            "booking_status": booking.status,
            "rtc": {
                "appId": payload["app_id"],
                "channel": payload["channel"],
                "token": payload["token"],
                "uid": payload["uid"],
                "role": payload["role"],
                "expiresIn": payload["expires_in"],
            },
        }
        
    )

@rtc_bp.route("/complete/<int:booking_id>", methods=["POST"])
@login_required
def rtc_mark_complete(booking_id):
    db, Booking, _ = _get_app_objects()

    booking, error_response, status_code = _get_booking_or_404(booking_id)
    if error_response:
        return error_response, status_code

    if not _user_can_access_booking(booking):
        return jsonify({"ok": False, "error": "Not authorized"}), 403

    if current_user.id == booking.student_id:
        booking.student_marked_complete = True

    elif current_user.id == booking.tutor_id:
        booking.tutor_marked_complete = True

    else:
        return jsonify({"ok": False, "error": "Invalid role"}), 403

    # If BOTH complete → mark booking completed
    if booking.student_marked_complete and booking.tutor_marked_complete:
        booking.status = "completed"

    db.session.commit()

    return jsonify({
        "ok": True,
        "student_done": booking.student_marked_complete,
        "tutor_done": booking.tutor_marked_complete,
        "status": booking.status
    })

@rtc_bp.route("/connected/<int:booking_id>", methods=["POST"])
@login_required
def rtc_connected(booking_id):
    db, Booking, LiveSessionLog = _get_app_objects()

    booking, error_response, status_code = _get_booking_or_404(booking_id)
    if error_response:
        return error_response, status_code

    if not _user_can_access_booking(booking):
        return jsonify({"ok": False, "error": "Not authorized for this session"}), 403

    log = LiveSessionLog.query.filter_by(booking_id=booking.id).first()
    if not log:
        log = LiveSessionLog(
            booking_id=booking.id,
            room_code=booking.room_code,
            started_at=datetime.utcnow(),
            last_activity_note="Session initialized",
        )
        db.session.add(log)

    if current_user.role == "admin":
        log.admin_joined = True
        log.last_activity_note = f"Admin connected at {datetime.utcnow().isoformat()} UTC"
    elif current_user.id == booking.student_id:
        log.student_joined = True
        log.last_activity_note = f"Student connected at {datetime.utcnow().isoformat()} UTC"
    elif current_user.id == booking.tutor_id:
        log.tutor_joined = True
        log.last_activity_note = f"Tutor connected at {datetime.utcnow().isoformat()} UTC"

    if booking.status == "scheduled":
        booking.status = "live"

    db.session.commit()

    return jsonify(
        {
            "ok": True,
            "booking_id": booking.id,
            "booking_status": booking.status,
            "student_joined": bool(log.student_joined),
            "tutor_joined": bool(log.tutor_joined),
            "admin_joined": bool(log.admin_joined),
        }
    )

@rtc_bp.route("/leave/<int:booking_id>", methods=["POST"])
@login_required
def rtc_leave(booking_id):
    db, _, LiveSessionLog = _get_app_objects()

    booking, error_response, status_code = _get_booking_or_404(booking_id)
    if error_response:
        return error_response, status_code

    if not _user_can_access_booking(booking):
        return jsonify({"ok": False, "error": "Not authorized for this session"}), 403

    log = LiveSessionLog.query.filter_by(booking_id=booking.id).first()
    if not log:
        return jsonify({"ok": True, "message": "No live log found; nothing to update"})

    _mark_left(log, booking)
    _sync_booking_status_from_log(booking, log)
    db.session.commit()

    return jsonify(
        {
            "ok": True,
            "booking_id": booking.id,
            "booking_status": booking.status,
            "admin_monitoring_active": bool(log.admin_joined),
            "message": "Leave state recorded",
        }
    )


@rtc_bp.route("/session-status/<int:booking_id>", methods=["GET"])
@login_required
def rtc_session_status(booking_id):
    _, _, LiveSessionLog = _get_app_objects()

    booking, error_response, status_code = _get_booking_or_404(booking_id)
    if error_response:
        return error_response, status_code

    if not _user_can_access_booking(booking):
        return jsonify({"ok": False, "error": "Not authorized for this session"}), 403

    log = LiveSessionLog.query.filter_by(booking_id=booking.id).first()

    if not log:
        return jsonify(
            {
                "ok": True,
                "booking_id": booking.id,
                "room_code": booking.room_code,
                "booking_status": booking.status,
                "is_live": False,
                "student_joined": False,
                "tutor_joined": False,
                "admin_joined": False,
                "last_activity_note": "No live session log yet",
            }
        )

    return jsonify(
        {
            "ok": True,
            "booking_id": booking.id,
            "room_code": log.room_code,
            "booking_status": booking.status,
            "is_live": bool(log.student_joined or log.tutor_joined or log.admin_joined),
            "student_joined": bool(log.student_joined),
            "tutor_joined": bool(log.tutor_joined),
            "admin_joined": bool(log.admin_joined),
            "started_at": log.started_at.isoformat() if log.started_at else None,
            "ended_at": log.ended_at.isoformat() if log.ended_at else None,
            "last_activity_note": log.last_activity_note or "",
        }
    )


@rtc_bp.route("/admin/live-sessions", methods=["GET"])
@login_required
def rtc_admin_live_sessions():
    _, _, LiveSessionLog = _get_app_objects()

    if current_user.role != "admin":
        return jsonify({"ok": False, "error": "Admin access required"}), 403

    live_logs = LiveSessionLog.query.order_by(LiveSessionLog.started_at.desc()).all()

    results = []
    for log in live_logs:
        booking = log.booking
        if not booking:
            continue

        results.append(
            {
                "booking_id": booking.id,
                "room_code": log.room_code,
                "subject": booking.subject,
                "class_level": booking.class_level,
                "scheduled_at": booking.scheduled_at.isoformat() if booking.scheduled_at else None,
                "status": booking.status,
                "student_name": booking.student.public_name if booking.student else "",
                "tutor_name": booking.tutor.public_name if booking.tutor else "",
                "student_joined": bool(log.student_joined),
                "tutor_joined": bool(log.tutor_joined),
                "admin_joined": bool(log.admin_joined),
                "is_live": bool(log.student_joined or log.tutor_joined or log.admin_joined),
                "started_at": log.started_at.isoformat() if log.started_at else None,
                "ended_at": log.ended_at.isoformat() if log.ended_at else None,
                "last_activity_note": log.last_activity_note or "",
            }
        )

    return jsonify({"ok": True, "sessions": results})


@rtc_bp.route("/chat/<int:booking_id>", methods=["GET"])
@login_required
def rtc_chat_messages(booking_id):
    booking, error_response, status_code = _get_booking_or_404(booking_id)
    if error_response:
        return error_response, status_code

    if not _user_can_access_booking(booking):
        return jsonify({"ok": False, "error": "Not authorized for this chat"}), 403

    messages = (
        SessionMessage.query.filter_by(booking_id=booking.id)
        .order_by(SessionMessage.created_at.asc())
        .all()
    )

    return jsonify(
        {
            "ok": True,
            "messages": [
                {
                    "id": m.id,
                    "sender_id": m.sender_id,
                    "sender_role": m.sender_role,
                    "message_text": m.message_text,
                    "file_url": m.file_url,
                    "file_name": m.file_name,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                }
                for m in messages
                if not m.is_blocked
            ],
        }
    )


@rtc_bp.route("/chat/<int:booking_id>/send", methods=["POST"])
@login_required
def rtc_chat_send(booking_id):
    db, _, _ = _get_app_objects()

    booking, error_response, status_code = _get_booking_or_404(booking_id)
    if error_response:
        return error_response, status_code

    if not _user_can_access_booking(booking):
        return jsonify({"ok": False, "error": "Not authorized for this chat"}), 403

    data = request.get_json(silent=True) or {}
    message_text = (data.get("message") or "").strip()

    if not message_text:
        return jsonify({"ok": False, "error": "Message is required"}), 400

    is_blocked, blocked_reason = message_contains_blocked_contact_info(message_text)

    msg = SessionMessage(
        booking_id=booking.id,
        sender_id=current_user.id,
        sender_role=current_user.role,
        message_text=message_text,
        is_blocked=is_blocked,
        blocked_reason=blocked_reason if is_blocked else "",
    )
    db.session.add(msg)
    db.session.commit()

    if is_blocked:
        return jsonify({"ok": False, "error": blocked_reason}), 400

    return jsonify({"ok": True, "message": "Sent"})


@rtc_bp.route("/chat/<int:booking_id>/upload", methods=["POST"])
@login_required
def rtc_chat_upload(booking_id):
    db, _, _ = _get_app_objects()

    booking, error_response, status_code = _get_booking_or_404(booking_id)
    if error_response:
        return error_response, status_code

    if not _user_can_access_booking(booking):
        return jsonify({"ok": False, "error": "Not authorized for this chat"}), 403

    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"ok": False, "error": "File is required"}), 400

    upload_dir = Path(current_app.config["UPLOAD_FOLDER"]) / "session_files"
    upload_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(file.filename).suffix.lower()
    safe_name = secure_filename(Path(file.filename).stem)
    final_name = f"{safe_name}_{uuid4().hex[:10]}{ext}"
    save_path = upload_dir / final_name
    file.save(save_path)

    file_url = f"/uploads/session_files/{final_name}"

    msg = SessionMessage(
        booking_id=booking.id,
        sender_id=current_user.id,
        sender_role=current_user.role,
        file_url=file_url,
        file_name=file.filename,
        message_text="",
        is_blocked=False,
        blocked_reason="",
    )
    db.session.add(msg)
    db.session.commit()

    return jsonify({"ok": True, "file_url": file_url, "file_name": file.filename})