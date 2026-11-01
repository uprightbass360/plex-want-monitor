import re
import unicodedata

_STRIP = re.compile(r"[:'\"/\\?!,.;]")


def normalize_title(title: str) -> str:
    folded = unicodedata.normalize("NFKD", title)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return re.sub(r"[^0-9a-z]+", " ", folded.lower()).strip()


def search_string(item_type: str, title: str, year: int | None) -> str:
    text = _STRIP.sub("", title)
    if item_type == "movie" and year is not None:
        text = f"{text} {year}"
    return " ".join(text.split())
