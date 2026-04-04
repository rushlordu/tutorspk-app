"""
agora_service.py
Working Agora RTC join payload service for TutorsOnline.pk

Supports:
- dev mode with no token when AGORA_TEMP_TOKEN_MODE=true
- production token generation with AccessToken2 if available
- role-aware payload for student / tutor / admin observer
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


class AgoraConfigError(RuntimeError):
    pass


@dataclass
class JoinPayload:
    app_id: str
    channel: str
    token: Optional[str]
    uid: int
    role: str
    expires_in: int


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _clean_channel_name(room_code: str) -> str:
    room_code = (room_code or "").strip()
    if not room_code:
        raise AgoraConfigError("Booking room code is missing.")
    return room_code[:64]


def _stable_uid_for_user(user) -> int:
    user_id = int(getattr(user, "id", 0) or 0)
    if user_id <= 0:
        raise AgoraConfigError("Current user ID is invalid for Agora.")
    return user_id


def _role_for_user(user) -> str:
    role = (getattr(user, "role", "") or "").strip().lower()
    if role == "admin":
        return "audience"
    return "host"


def _build_token_access_token_2(
    app_id: str,
    app_certificate: str,
    channel: str,
    uid: int,
    role: str,
    expiry_seconds: int,
) -> str:
    # Try vendored/local builder first
    try:
        from rtc.RtcTokenBuilder2 import (
            Role_Publisher,
            Role_Subscriber,
            build_token_with_uid_and_privilege,
        )
    except Exception:
        try:
            from RtcTokenBuilder2 import (
                Role_Publisher,
                Role_Subscriber,
                build_token_with_uid_and_privilege,
            )
        except Exception:
            # Try package-style builder if installed
            try:
                from agora_token_builder import RtcTokenBuilder2
            except Exception as exc:
                raise AgoraConfigError(
                    "Agora token builder not found. Use AGORA_TEMP_TOKEN_MODE=true for local testing, "
                    "or add RtcTokenBuilder2.py under rtc/, or ensure your installed builder exposes RtcTokenBuilder2."
                ) from exc

            rtc_role = (
                RtcTokenBuilder2.Role_Subscriber
                if role == "audience"
                else RtcTokenBuilder2.Role_Publisher
            )

            try:
                token = RtcTokenBuilder2.build_token_with_uid(
                    app_id,
                    app_certificate,
                    channel,
                    uid,
                    rtc_role,
                    expiry_seconds,
                )
                return token
            except Exception as exc:
                raise AgoraConfigError(f"Agora token generation failed: {str(exc)}") from exc

    rtc_role = Role_Subscriber if role == "audience" else Role_Publisher

    try:
        token = build_token_with_uid_and_privilege(
            app_id,
            app_certificate,
            channel,
            uid,
            expiry_seconds,
            expiry_seconds,
            expiry_seconds,
            expiry_seconds,
            expiry_seconds,
            rtc_role,
        )
        return token
    except Exception as exc:
        raise AgoraConfigError(f"Agora token generation failed: {str(exc)}") from exc


def get_join_payload_for_user(booking, user) -> JoinPayload:
    app_id = (os.getenv("AGORA_APP_ID") or "").strip()
    app_certificate = (os.getenv("AGORA_APP_CERTIFICATE") or "").strip()
    expiry_seconds = int(os.getenv("AGORA_TOKEN_EXPIRY_SECONDS", "3600"))

    # IMPORTANT:
    # default temp mode to TRUE for safer local testing
    temp_mode = _env_bool("AGORA_TEMP_TOKEN_MODE", default=True)

    if not app_id:
        raise AgoraConfigError("AGORA_APP_ID is not configured.")

    channel = _clean_channel_name(getattr(booking, "room_code", ""))
    uid = _stable_uid_for_user(user)
    role = _role_for_user(user)

    # Local/dev mode: do not try to build token
    if temp_mode:
        return JoinPayload(
            app_id=app_id,
            channel=channel,
            token=None,
            uid=uid,
            role=role,
            expires_in=expiry_seconds,
        )

    if not app_certificate:
        raise AgoraConfigError(
            "AGORA_APP_CERTIFICATE is not configured. "
            "Set AGORA_TEMP_TOKEN_MODE=true for local testing, or configure certificate for production."
        )

    token = _build_token_access_token_2(
        app_id=app_id,
        app_certificate=app_certificate,
        channel=channel,
        uid=uid,
        role=role,
        expiry_seconds=expiry_seconds,
    )

    return JoinPayload(
        app_id=app_id,
        channel=channel,
        token=token,
        uid=uid,
        role=role,
        expires_in=expiry_seconds,
    )