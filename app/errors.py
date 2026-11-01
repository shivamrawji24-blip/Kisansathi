"""Domain errors that the API/agent layers turn into friendly messages."""
from __future__ import annotations


class MissingInput(ValueError):
    """A required input (location, state, crop...) was not provided."""

    def __init__(self, field: str, hint_en: str, hint_hi: str):
        self.field = field
        self.hint_en = hint_en
        self.hint_hi = hint_hi
        super().__init__(f"{field}: {hint_en}")
