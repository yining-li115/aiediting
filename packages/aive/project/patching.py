from __future__ import annotations

from ..contracts.patch import PATCH_V1_OPERATION_NAMES, PatchOperation
from ..contracts.project_doc import ProjectDoc
from .errors import PatchApplicationError
from .patch_project import PROJECT_OPERATION_HANDLERS
from .patch_timeline import TIMELINE_OPERATION_HANDLERS


OPERATION_HANDLERS = {
    **PROJECT_OPERATION_HANDLERS,
    **TIMELINE_OPERATION_HANDLERS,
}

if tuple(OPERATION_HANDLERS) != PATCH_V1_OPERATION_NAMES:
    raise RuntimeError("Patch v1 operation handlers are incomplete or out of order")


def apply_operation(document: ProjectDoc, operation: PatchOperation) -> ProjectDoc:
    """Apply one already-authorized operation to an in-memory working copy."""

    try:
        handler = OPERATION_HANDLERS[operation.op]
    except KeyError as exc:  # Defensive: Patch's discriminated union should prevent this.
        raise PatchApplicationError(
            "unsupported_operation",
            f"Patch operation {operation.op!r} is not supported",
            operation_id=operation.operation_id,
        ) from exc
    try:
        return handler(document, operation)
    except PatchApplicationError as exc:
        raise exc.for_operation(operation.operation_id) from exc
