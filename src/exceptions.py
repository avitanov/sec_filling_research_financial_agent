"""Custom exceptions used by the project scripts."""


class FilingNotFoundError(Exception):
    """Raised when a company has no matching SEC filing."""
