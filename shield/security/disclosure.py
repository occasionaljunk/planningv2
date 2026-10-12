"""Statistical disclosure control for aggregate outputs."""
from __future__ import annotations

from ..config import get_settings


def suppress(count: int | float | None, role: str, min_cell: int | None = None):
    """Return the count, or a "<k" marker for small non-zero cells.

    Applied to aggregate figures shown to roles that do not need record-level
    detail. Analysts with line-list permission see exact counts because the
    surveillance purpose requires it; every such view is audited.
    """
    if count is None:
        return None
    k = min_cell or get_settings().min_cell_size
    if role in ("epidemiologist", "data_steward"):
        return count
    if 0 < count < k:
        return f"<{k}"
    return count
