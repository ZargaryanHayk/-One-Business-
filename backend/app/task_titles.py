"""Short task headlines for messages that supply only a description."""

MAX_TITLE_LENGTH = 120


def derive_title(text: str) -> str:
    headline = " ".join(text.split())
    if not headline:
        return "Untitled task"
    if len(headline) > MAX_TITLE_LENGTH:
        return headline[: MAX_TITLE_LENGTH - 1].rstrip() + "…"
    return headline


def default_title(context) -> str:
    return derive_title(context.get_current_parameters().get("text", ""))
