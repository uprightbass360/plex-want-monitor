import re
import unicodedata

_STRIP = re.compile(r"[:'\"/\\?!,.;]")


_FOLD = str.maketrans({"ø": "o", "æ": "ae", "œ": "oe", "đ": "d", "ł": "l"})


def normalize_title(title: str) -> str:
    folded = unicodedata.normalize("NFKD", title)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = folded.casefold().translate(_FOLD)  # casefold: ß -> ss
    return re.sub(r"[\W_]+", " ", folded).strip()


def search_string(item_type: str, title: str, year: int | None) -> str:
    text = _STRIP.sub("", title)
    if item_type == "movie" and year is not None:
        text = f"{text} {year}"
    return " ".join(text.split())
