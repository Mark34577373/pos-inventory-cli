"""Small server-side validators shared by trusted clients and web services."""

import unicodedata


def visible_text(value, label, maximum, *, optional=False, multiline=False):
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text.")
    value = value.strip()
    if (not value and not optional) or len(value) > maximum:
        raise ValueError(f"{label} must contain 1–{maximum} characters.")
    if any(
        unicodedata.category(char) in ("Cc", "Cs") and not (multiline and char in "\n\r\t")
        for char in value
    ):
        raise ValueError(f"{label} contains unsupported control characters.")
    return value
