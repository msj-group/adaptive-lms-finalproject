"""Shared display policy; stored files, outcomes and notifications stay native."""
import re

from flask import get_flashed_messages


def visible_flash_messages(with_categories=False, category_filter=()):
    """Consume routine acknowledgements without rendering banners or popups."""
    def redundant(category, message):
        text = str(message).lower()
        return category == "success" or (category == "info" and
            any(word in text for word in ("already", "unchanged", "no changes")) and
            not any(word in text for word in ("final", "cannot", "must", "required")))

    messages = [(category, message) for category, message in
                get_flashed_messages(with_categories=True, category_filter=category_filter)
                if not redundant(category, message)]
    return messages if with_categories else [message for _, message in messages]


def media_label(value):
    """Strip only a terminal media suffix from a display label, never a path."""
    return re.sub(r"\.(?:mp3|mp4|m4a|m4v|wav|ogg|oga|ogv|opus|webm|mov|aac|flac)\s*$", "",
                  str(value or ""), flags=re.IGNORECASE)
