import os
import time
from dataclasses import dataclass
from typing import Literal

try:
    from agora_token_builder import RtcTokenBuilder
except ImportError:
    RtcTokenBuilder = None


AgoraRole = Literal["host", "student", "admin_observer", "audience"]


@dataclass
class AgoraJoinPayload:
    app_id: str
    channel: str
    token: str
    uid: int
    role: str
    expires_in: int


class AgoraConfigError(RuntimeError):
    pass


def _get_agora_env() -> tuple[str, str]:
    app_id = os.getenv("AGORA_APP_ID", "").strip()
    app_certificate = os.getenv("AGORA_APP_CERTIFICATE", "").strip()

    if not app_id:
        raise AgoraConfigError("AGORA_APP_ID is missing in environment.")
    if not app_certificate:
        raise AgoraConfigError("AGORA_APP_CERTIFICATE is missing in environment.")
    if RtcTokenBuilder is None:
        raise AgoraConfigError(
            "agora-token-builder package is not installed. "
            "Run: pip install agora-token-builder"
        )

    return app_id, app_certificate


def normalize_channel_name(room_code: str) -> str:
    """
    Agora channel names should be predictable and safe.
    We keep your booking.room_code as base, but normalize it.
    """
    if not room_code:
        raise ValueError("room_code is required")

    safe = "".join(ch for ch in room_code if ch.isalnum() or ch in ("_", "-", ":"))
    safe = safe[:64]

    if not safe:
        raise ValueError("room_code produced an empty channel name")

    return safe


def map_platform_role_to_agora_role(role: AgoraRole) -> int:
    """
    Agora RTC roles:
    1 = publisher
    2 = subscriber

    host / student in private or interactive rooms can publish.
    admin_observer / audience should subscribe only.
    """
    role = (role or "").strip().lower()

    if role in ("host", "student"):
        return 1  # publisher
    return 2  # subscriber


def can_publish(role: AgoraRole) -> bool:
    role = (role or "").strip().lower()
    return role in ("host", "student")


def build_rtc_token(
    *,
    room_code: str,
    uid: int,
    role: AgoraRole,
    expire_seconds: int = 3600,
) -> AgoraJoinPayload:
    """
    Build Agora RTC token using your platform-controlled identity.

    room_code -> Agora channel
    uid       -> your platform user id
    role      -> mapped to publisher/subscriber
    """
    if not isinstance(uid, int) or uid <= 0:
        raise ValueError("uid must be a positive integer")

    app_id, app_certificate = _get_agora_env()
    channel = normalize_channel_name(room_code)
    agora_role = map_platform_role_to_agora_role(role)

    current_ts = int(time.time())
    privilege_expire_ts = current_ts + int(expire_seconds)

    token = RtcTokenBuilder.buildTokenWithUid(
        app_id,
        app_certificate,
        channel,
        uid,
        agora_role,
        privilege_expire_ts,
    )

    return AgoraJoinPayload(
        app_id=app_id,
        channel=channel,
        token=token,
        uid=uid,
        role=role,
        expires_in=expire_seconds,
    )


def get_join_payload_for_user(
    *,
    booking,
    user,
) -> AgoraJoinPayload:
    """
    Decides Agora role from your platform objects.

    Rules:
    - tutor on this booking -> host
    - student on this booking -> student
    - admin -> admin_observer
    - otherwise denied
    """
    if not booking:
        raise ValueError("booking is required")
    if not user:
        raise ValueError("user is required")

    if user.role == "admin":
        role: AgoraRole = "admin_observer"
    elif booking.tutor_id == user.id:
        role = "host"
    elif booking.student_id == user.id:
        role = "student"
    else:
        raise PermissionError("User is not allowed to join this session")

    return build_rtc_token(
        room_code=booking.room_code,
        uid=int(user.id),
        role=role,
        expire_seconds=3600,
    )