from __future__ import annotations

from enum import Enum


class DataMigrationConflictPolicy(str, Enum):
    OVERWRITE = "overwrite"
    RENAME = "rename"
    SKIP = "skip"
    FAIL = "fail"

    def __str__(self) -> str:
        return self.value


__all__ = ["DataMigrationConflictPolicy"]
