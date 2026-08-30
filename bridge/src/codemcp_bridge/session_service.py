"""Persistent session lifecycle and ownership checks."""

from __future__ import annotations

import uuid

from .db import Database, SessionRecord
from .errors import BridgeError
from .resource_auth import (
    auth_audit_details,
    auth_context_identity,
    auth_identity,
    current_auth_context,
)

LOCAL_OWNER_ID = "local-policy"


class SessionService:
    def __init__(self, database: Database, *, owner_id: str = LOCAL_OWNER_ID):
        self._database = database
        self.owner_id = owner_id

    def create(self, project_id: str) -> SessionRecord:
        principal = current_auth_context()
        auth_details = auth_audit_details(principal) if principal is not None else None
        return self._database.create_session(
            uuid.uuid4().hex,
            project_id,
            self.owner_id,
            auth_details=auth_details,
        )

    def auth_contexts_match(self, first_session_id: str, second_session_id: str) -> bool:
        """Compare persisted session security identities without exposing them."""

        first_details = self._database.get_session_auth_context(first_session_id)
        second_details = self._database.get_session_auth_context(second_session_id)
        if first_details is None or second_details is None:
            return first_details is None and second_details is None
        first_identity = auth_context_identity(first_details)
        second_identity = auth_context_identity(second_details)
        return first_identity is not None and first_identity == second_identity

    def _current_context_matches(self, session_id: str) -> bool:
        stored_details = self._database.get_session_auth_context(session_id)
        principal = current_auth_context()
        if stored_details is None:
            return principal is None
        if principal is None:
            return False
        stored_identity = auth_context_identity(stored_details)
        current_identity = auth_identity(principal)
        return stored_identity is not None and stored_identity == current_identity

    def require_active(self, project_id: str, session_id: str | None) -> SessionRecord:
        if not session_id:
            raise BridgeError("SESSION_REQUIRED", "session_id is required for this operation")
        session = self._database.get_session(session_id)
        if session is None or session.project_id != project_id:
            raise BridgeError(
                "SESSION_NOT_FOUND",
                "session_id is not active for this project",
                {"project_id": project_id},
            )
        if session.owner_id != self.owner_id or session.status != "active":
            raise BridgeError(
                "SESSION_NOT_FOUND",
                "session_id is not active",
                {"project_id": project_id, "status": session.status},
            )
        if not self._current_context_matches(session.session_id):
            raise BridgeError(
                "SESSION_NOT_FOUND",
                "session_id is not available to this security context",
                {"project_id": project_id},
            )
        return session

    def close_all(self, reason: str) -> None:
        self._database.close_active_sessions(reason)

    def revoke_project(self, project_id: str, reason: str = "project_removed") -> list[str]:
        return self._database.block_active_sessions_for_project(project_id, reason)

    def recover_after_restart(self) -> dict[str, list[str]]:
        return self._database.recover_after_restart()
