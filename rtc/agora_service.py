import os
from rtc.RtcTokenBuilder2 import RtcTokenBuilder, Role_Publisher, Role_Subscriber


class AgoraConfigError(RuntimeError):
    pass


def build_token(channel: str, uid: int, role: str) -> str:
    app_id = os.getenv("AGORA_APP_ID", "").strip()
    app_certificate = os.getenv("AGORA_APP_CERTIFICATE", "").strip()

    if not app_id:
        raise AgoraConfigError("AGORA_APP_ID is missing")
    if not app_certificate:
        raise AgoraConfigError("AGORA_APP_CERTIFICATE is missing")

    role_value = Role_Publisher if role == "host" else Role_Subscriber
    expiry_seconds = 3600

    return RtcTokenBuilder.build_token_with_uid_and_privilege(
        app_id,
        app_certificate,
        channel,
        uid,
        expiry_seconds,
        expiry_seconds,
        expiry_seconds if role_value == Role_Publisher else 0,
        expiry_seconds if role_value == Role_Publisher else 0,
        expiry_seconds if role_value == Role_Publisher else 0,
    )


def get_join_payload_for_user(booking, user):
    room_code = (getattr(booking, "room_code", "") or "").strip()
    if not room_code:
        raise AgoraConfigError("Booking room code is missing.")

    user_id = int(getattr(user, "id", 0) or 0)
    if user_id <= 0:
        raise AgoraConfigError("Current user ID is invalid for Agora.")

    role = "audience" if (getattr(user, "role", "") or "").strip().lower() == "admin" else "host"
    token = build_token(room_code, user_id, role)

    return {
        "app_id": os.getenv("AGORA_APP_ID", "").strip(),
        "channel": room_code,
        "token": token,
        "uid": user_id,
        "role": role,
        "expires_in": 3600,
    }