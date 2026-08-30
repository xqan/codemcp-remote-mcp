from __future__ import annotations

from codemcp_bridge.errors import BridgeError


def test_bridge_error_initializes_exception_base_without_slots_super_failure() -> None:
    error = BridgeError(
        "APPROVAL_REQUIRED",
        "explicit approval is required before this operation can run",
        {"operation_id": "op-1"},
        status="awaiting_approval",
    )

    assert str(error) == "explicit approval is required before this operation can run"
    assert error.args == ("explicit approval is required before this operation can run",)
    assert error.as_payload() == {
        "code": "APPROVAL_REQUIRED",
        "message": "explicit approval is required before this operation can run",
        "details": {"operation_id": "op-1"},
        "retryable": False,
    }
