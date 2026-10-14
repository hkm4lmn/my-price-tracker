"""Why a fetch failed. `kind` is stored in products.last_error and check_results."""


class FetchError(Exception):
    kind = "error"

    def __init__(self, message: str = "", *, kind: str | None = None):
        super().__init__(message)
        if kind is not None:
            self.kind = kind


class Blocked(FetchError):
    kind = "blocked"


class NotFound(FetchError):
    kind = "not_found"


class ParseError(FetchError):
    """The page loaded but had no usable product data (or its layout changed)."""

    kind = "parse_error"


class FetchTimeout(FetchError):
    kind = "timeout"


class UnsafeUrl(FetchError):
    kind = "unsafe_url"
