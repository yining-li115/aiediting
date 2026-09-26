from __future__ import annotations

from pydantic import JsonValue


class PatchApplicationError(ValueError):
    """Expected, user-actionable rejection while applying one Patch operation."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        operation_id: str | None = None,
        details: dict[str, JsonValue] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.operation_id = operation_id
        self.details = details or {}

    def for_operation(self, operation_id: str) -> PatchApplicationError:
        if self.operation_id is not None:
            return self
        return PatchApplicationError(
            self.code,
            self.message,
            operation_id=operation_id,
            details=self.details,
        )
