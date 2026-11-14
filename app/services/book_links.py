"""Place reviewed references only on the exact PDF editions they describe."""
from collections import defaultdict
from functools import lru_cache
import json
from pathlib import Path


@lru_cache(maxsize=1)
def _catalogue():
    return json.loads(Path(__file__).with_name("empower_book_links.json").read_text(encoding="utf-8"))


def book_references(document, numbers):
    if document is None:
        return {}
    pages = _catalogue().get(document.uploaded_file.sha256, {})
    return {number: pages[str(number)] for number in numbers if str(number) in pages}


def book_links(document, numbers, resolve):
    """Resolve destinations through the caller's role and publication scope."""
    result = defaultdict(list)
    for number, references in book_references(document, numbers).items():
        for index, (kind, checksum, target, printed, x, y, width, height) in enumerate(references, 1):
            href = resolve(kind, checksum, target)
            if href:
                result[number].append(dict(
                    anchor=f"book-reference-{number}-{index}", href=href, kind=kind,
                    printed=printed, x=x, y=y, width=width, height=height,
                ))
    return result
