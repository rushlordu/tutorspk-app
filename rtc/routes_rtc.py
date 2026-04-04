from datetime import datetime
from pathlib import Path
from uuid import uuid4

from flask import Blueprint, jsonify, request, current_app
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from rtc.agora_service import AgoraConfigError, get_join_payload_for_user
from rtc.chat_guard import message_contains_blocked_contact_info
from rtc.models_rtc import SessionMessage

rtc_bp = Blueprint("rtc", __name__)

import sys

def _get_app_objects():
    # When app is started with "python app.py", the loaded module is "__main__".
    # Importing "app" again creates a second module and duplicates SQLAlchemy models.
    source = sys.modules.get("__main__") or sys.modules.get("app")

    if source is None:
        raise RuntimeError("Could not locate the running app module.")

    db = getattr(source, "db", None)
    Booking = getattr(source, "Booking", None)
    LiveSessionLog = getattr(source, "LiveSessionLog", None)

    if not all([db, Booking, LiveSessionLog]):
        raise RuntimeError(
            "App objects not found. Make sure db, Booking, and LiveSessionLog are defined before rtc blueprint is imported."
        )

    return db, Booking, LiveSessionLog


def _get_booking_or_404(booking_id: int):
    db, Booking, LiveSessionLog = _get_app_objects()
    booking = db.session.get(Booking, booking_id)
    if not booking:
        return None, jsonify({"ok": False, "error": "Booking not found"}), 404
    return booking, None, None


def _get_or_create_live_log(booking):
    db, Booking, LiveSessionLog = _get_app_objects()
    log = LiveSessionLog.query.filter_by(booking_id=booking.id).first()
    if not log:
        log = LiveSessionLog(
            booking_id=booking.id,
            room_code=booking.room_code,
            started_at=datetime.utcnow(),
            last_activity_note="Session initialized",
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


def _update_live_log_join_state(log, booking):
    if current_user.role == "admin":
        log.admin_joined = True
        log.last_activity_note = f"Admin joined at {datetime.utcnow().isoformat()} UTC"
    elif current_user.id == booking.student_id:
        log.student_joined = True
        log.last_activity_note = f"Student joined at {datetime.utcnow().isoformat()} UTC"
    elif current_user.id == booking.tutor_id:
        log.tutor_joined = True
        log.last_activity_note = f"Tutor joined at {datetime.utcnow().isoformat()} UTC"


def _update_live_log_leave_state(log, booking):
    if current_user.role == "admin":
        log.admin_joined = False
        log.last_activity_note = f"Admin left at {datetime.utcnow().isoformat()} UTC"
    elif current_user.id == booking.student_id:
        log.student_joined = False
        log.last_activity_note = f"Student left at {datetime.utcnow().isoformat()} UTC"
    elif current_user.id == booking.tutor_id:
        log.tutor_joined = False
        log.last_activity_note = f"Tutor left at {datetime.utcnow().isoformat()} UTC"

    if not log.student_joined and not log.tutor_joined and not log.admin_joined:
        log.ended_at = datetime.utcnow()


@rtc_bp.route("/join/<int:booking_id>", methods=["POST"])
@login_required
def rtc_join(booking_id):
    db, Booking, LiveSessionLog = _get_app_objects()

    booking, error_response, status_code = _get_booking_or_404(booking_id)
    if error_response:
        return error_response, status_code

    if not _user_can_access_booking(booking):
        return jsonify({"ok": False, "error": "Not authorized for this session"}), 403

    try:
        payload = get_join_payload_for_user(booking=booking, user=current_user)
    except PermissionError as e:
        return jsonify({"ok": False, "error": str(e)}), 403
    except AgoraConfigError as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": f"Failed to generate token: {str(e)}"}), 500

    log = _get_or_create_live_log(booking)
    _update_live_log_join_state(log, booking)

    if booking.status == "scheduled":
        booking.status = "live"

    db.session.commit()

    return jsonify(
        {
            "ok": True,
            "booking_id": booking.id,
            "room_code": booking.room_code,
            "booking_status": booking.status,
            "rtc": {
                "appId": payload.app_id,
                "channel": payload.channel,
                "token": payload.token,
                "uid": payload.uid,
                "role": payload.role,
                "expiresIn": payload.expires_in,
            },
            "admin_monitoring_active": bool(log.admin_joined),
        }
    )


@rtc_bp.route("/leave/<int:booking_id>", methods=["POST"])
@login_required
def rtc_leave(booking_id):
    db, Booking, LiveSessionLog = _get_app_objects()

    booking, error_response, status_code = _get_booking_or_404(booking_id)
    if error_response:
        return error_response, status_code

    if not _user_can_access_booking(booking):
        return jsonify({"ok": False, "error": "Not authorized for this session"}), 403

    log = LiveSessionLog.query.filter_by(booking_id=booking.id).first()
    if not log:
        return jsonify({"ok": True, "message": "No live log found; nothing to update"})

    _update_live_log_leave_state(log, booking)
    db.session.commit()

    return jsonify(
        {
            "ok": True,
            "booking_id": booking.id,
            "admin_monitoring_active": bool(log.admin_joined),
            "message": "Leave state recorded",
        }
    )


@rtc_bp.route("/session-status/<int:booking_id>", methods=["GET"])
@login_required
def rtc_session_status(booking_id):
    db, Booking, LiveSessionLog = _get_app_objects()

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
    db, Booking, LiveSessionLog = _get_app_objects()

    if current_user.role != "admin":
        return jsonify({"ok": False, "error": "Admin access required"}), 403

    live_logs = LiveSessionLog.query.order_by(LiveSessionLog.started_at.desc()).all()

    results = []
    for log in live_logs:
        booking = log.booking
        if not booking:
            continue

        is_live = bool(log.student_joined or log.tutor_joined or log.admin_joined)

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
                "is_live": is_live,
                "started_at": log.started_at.isoformat() if log.started_at else None,
                "ended_at": log.ended_at.isoformat() if log.ended_at else None,
                "last_activity_note": log.last_activity_note or "",
            }
        )

    return jsonify({"ok": True, "sessions": results})


@rtc_bp.route("/chat/<int:booking_id>", methods=["GET"])
@login_required
def rtc_chat_messages(booking_id):
    db, Booking, LiveSessionLog = _get_app_objects()

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
    db, Booking, LiveSessionLog = _get_app_objects()

    booking, error_response, status_code = _get_booking_or_404(booking_id)
    if error_response:
        return error_response, status_code

    if not _user_can_access_booking(booking):
        return jsonify({"ok": False, "error": "Not authorized for this chat"}), 403

    data = request.get_json(silent=True) or {}
    message_text = (data.get("message") or "").strip()

    if not message_text:
        return jsonify({"ok": False, "error": "Message is required"}), 400

    blocked, reason = message_contains_blocked_contact_info(message_text)

    msg = SessionMessage(
        booking_id=booking.id,
        sender_id=current_user.id,
        sender_role=current_user.role,
        message_text=message_text,
        is_blocked=blocked,
        blocked_reason=reason if blocked else "",
    )
    db.session.add(msg)

    if blocked:
        db.session.commit()
        return jsonify({"ok": False, "error": reason}), 400

    db.session.commit()

    return jsonify(
        {
            "ok": True,
            "message": {
                "id": msg.id,
                "sender_id": msg.sender_id,
                "sender_role": msg.sender_role,
                "message_text": msg.message_text,
                "file_url": msg.file_url,
                "file_name": msg.file_name,
                "created_at": msg.created_at.isoformat() if msg.created_at else None,
            }
        }
    )


@rtc_bp.route("/chat/<int:booking_id>/upload", methods=["POST"])
@login_required
def rtc_chat_upload(booking_id):
    db, Booking, LiveSessionLog = _get_app_objects()

    booking, error_response, status_code = _get_booking_or_404(booking_id)
    if error_response:
        return error_response, status_code

    if not _user_can_access_booking(booking):
        return jsonify({"ok": False, "error": "Not authorized for this chat"}), 403

    upload = request.files.get("file")
    if not upload or not upload.filename:
        return jsonify({"ok": False, "error": "No file uploaded"}), 400

    filename = f"rtc_{uuid4().hex}_{secure_filename(upload.filename)}"
    save_path = Path(current_app.config["UPLOAD_FOLDER"]) / filename
    upload.save(save_path)

    msg = SessionMessage(
        booking_id=booking.id,
        sender_id=current_user.id,
        sender_role=current_user.role,
        file_url=f"/uploads/{filename}",
        file_name=upload.filename,
    )
    db.session.add(msg)
    db.session.commit()

    return jsonify(
        {
            "ok": True,
            "message": {
                "id": msg.id,
                "sender_id": msg.sender_id,
                "sender_role": msg.sender_role,
                "message_text": msg.message_text,
                "file_url": msg.file_url,
                "file_name": msg.file_name,
                "created_at": msg.created_at.isoformat() if msg.created_at else None,
            }
        }
    )