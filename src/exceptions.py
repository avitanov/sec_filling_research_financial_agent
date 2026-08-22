"""Custom exceptions used by the project scripts."""


class FilingNotFoundError(Exception):
    """Raised when a company has no matching SEC filing."""


class ChunkValidationError(Exception):
    """Raised when generated filing chunks fail deterministic validation."""


class FilingIndexError(Exception):
    """Raised when the local Qdrant filing index is unavailable or invalid."""
