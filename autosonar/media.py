"""Detect whether Windows has media playing."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def media_is_playing() -> bool | None:
    """True when a Windows media session is playing.

    Returns None when the check itself fails, so a brief error does not look
    like the music stopped.
    """
    try:
        import winrt.windows.foundation  # noqa: F401  (required by the async result)
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager
    except ImportError:
        log.debug("Windows media status is unavailable", exc_info=True)
        return None
    try:
        manager = GlobalSystemMediaTransportControlsSessionManager.request_async().get()
        sessions = list(manager.get_sessions())
        if not sessions:
            current = manager.get_current_session()
            sessions = [current] if current is not None else []
    except Exception:
        log.debug("Could not read Windows playback status", exc_info=True)
        return None
    return any(_is_playing(session) for session in sessions)


def _is_playing(session) -> bool:
    try:
        status = session.get_playback_info().playback_status
    except Exception:
        log.debug("Could not read a media session", exc_info=True)
        return False
    if getattr(status, "name", "") == "PLAYING":
        return True
    value = getattr(status, "value", status)
    try:
        return int(value) == 4
    except (TypeError, ValueError):
        return False
