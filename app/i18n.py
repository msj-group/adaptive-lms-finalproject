"""Owned UI localization. Learner content, identifiers and stored values stay intact."""
import ast
import html
import json
import re
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path

from flask import has_request_context, request
from flask_wtf import FlaskForm
from jinja2 import Environment, nodes
from jinja2.ext import Extension

LANGUAGE_COOKIE = "aelms.language"
THEME_COOKIE = "aelms.theme"
LANGUAGES = ("en", "ar")
THEMES = ("system", "light", "dark")
_CATALOG_PATH = Path(__file__).parent / "locales" / "ar.json"
_UI_ATTRIBUTES = {"alt", "title", "placeholder", "aria-label", "data-confirm", "data-default-label", "data-singular", "data-plural"}
_SKIP_TAGS = {"script", "style", "code", "pre", "kbd", "samp", "textarea"}
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
_UI_NAMES = {"label", "role", "error", "message", "form_error", "body_error", "edit_error", "reply_error", "heading", "eyebrow", "description"}
_UI_PROPERTIES = {"status", "role", "method", "kind", "state", "status_label", "role_label", "kind_label"}


def ui_language():
    value = request.cookies.get(LANGUAGE_COOKIE) if has_request_context() else None
    return value if value in LANGUAGES else "en"


def ui_theme():
    value = request.cookies.get(THEME_COOKIE) if has_request_context() else None
    return value if value in THEMES else "system"


def normalize(text):
    return re.sub(r"\s+", " ", str(text)).strip()


@lru_cache(maxsize=1)
def arabic_catalog():
    with _CATALOG_PATH.open(encoding="utf-8") as source:
        catalog = json.load(source)
    if not isinstance(catalog, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in catalog.items()):
        raise ValueError("Invalid UI translation catalog")
    return catalog


def gettext(text, **values):
    if not isinstance(text, str):
        return text
    translated = text
    if ui_language() == "ar":
        key = normalize(text)
        translated = arabic_catalog().get(key, _label_aliases().get(key.lower().replace("_", " "), text))
        if translated == text and " · " in text:
            role, suffix = text.split(" · ", 1)
            if role.lower() in {"student", "teacher", "administrator", "researcher"}:
                translated = gettext(role) + " · " + suffix
        if translated == text:
            translated = _formatted_label(text)
    return translated % values if values else translated


def ngettext(singular, plural, count, **values):
    """Translate a complete count phrase, avoiding English plural suffixes in Arabic."""
    values.setdefault("count", count)
    return gettext(singular if count == 1 else plural, **values)


@lru_cache(maxsize=1)
def _label_aliases():
    # Only explicitly owned UI expressions call gettext; stored enums, route
    # parameters and user-authored content are never rewritten.
    return {key.lower().replace("_", " "): value for key, value in arabic_catalog().items()}


@lru_cache(maxsize=1)
def _label_patterns():
    patterns = []
    placeholder = re.compile(r"%\((\w+)\)[sd]")
    for phrase, translation in arabic_catalog().items():
        parts = []
        cursor = 0
        fields = []
        for match in placeholder.finditer(phrase):
            parts.append(re.escape(phrase[cursor:match.start()].replace("%%", "%")))
            parts.append("(.+?)")
            fields.append(match[1])
            cursor = match.end()
        if fields:
            parts.append(re.escape(phrase[cursor:].replace("%%", "%")))
            patterns.append((re.compile("".join(parts), re.S), fields, translation))
    return patterns


def _formatted_label(text):
    for pattern, fields, translation in _label_patterns():
        matched = pattern.fullmatch(text)
        if matched:
            values = dict(zip(fields, matched.groups()))
            # Captured names and authored titles stay exactly as supplied.
            return re.sub(r"%\((\w+)\)[sd]", lambda match: values[match[1]], translation).replace("%%", "%")
    return text


def ui_date(text):
    """Translate Gregorian month/day presentation, retaining dates and timezones."""
    if ui_language() != "ar" or not isinstance(text, str):
        return text
    months = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
    arabic = ("يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر")
    names = dict(zip(months, arabic))
    names.update({name[:3]: value for name, value in zip(months, arabic)})
    for day in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"):
        names[day] = names[day[:3]] = gettext(day)
    return re.sub(r"\b[A-Za-z]+\b", lambda match: names.get(match[0], match[0]), text)


class _FormTranslations:
    def gettext(self, text):
        return gettext(text)

    def ngettext(self, singular, plural, count):
        return gettext(singular if count == 1 else plural)


class LocalizedFlaskForm(FlaskForm):
    """Localize field presentation without changing names, choice values or validation."""
    class Meta(FlaskForm.Meta):
        def get_translations(self, form):
            return _FormTranslations()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self:
            field.label.text = gettext(field.label.text)
            if field.type == "SubmitField" and isinstance(field.data, str):
                field.data = gettext(field.data)
            if field.description:
                field.description = gettext(field.description)
            # Only declared choices are present here; routes add owned object
            # names afterwards. Keep submitted values and dynamic names intact.
            declared = getattr(field, "choices", None)
            if isinstance(declared, (list, tuple)):
                field.choices = [self._choice(choice) for choice in declared]

    @staticmethod
    def _choice(choice):
        if isinstance(choice, (list, tuple)) and len(choice) >= 2 and isinstance(choice[1], str):
            return (choice[0], gettext(choice[1]), *choice[2:])
        return choice


def _template_ranges(source):
    """Mask Jinja without interpreting or rewriting its control/identity expressions."""
    result = []
    index = 0
    while index < len(source):
        start = re.search(r"\{[\{%#]", source[index:])
        if start is None:
            break
        begin = index + start.start()
        closing = {"{{": "}}", "{%": "%}", "{#": "#}"}[source[begin:begin + 2]]
        cursor = begin + 2
        quote = None
        while cursor < len(source):
            char = source[cursor]
            if closing != "#}" and quote:
                if char == "\\":
                    cursor += 2
                    continue
                if char == quote:
                    quote = None
            elif closing != "#}" and char in "\"'":
                quote = char
            elif source[cursor:cursor + 2] == closing:
                cursor += 2
                break
            cursor += 1
        result.append((begin, cursor))
        index = cursor
    return result


class _OwnedMarkup(HTMLParser):
    def __init__(self, source, ranges):
        super().__init__(convert_charrefs=False)
        self.source = source
        self.ranges = ranges
        self.stack = []
        self.text = []
        self.attributes = []
        self.offsets = [0] + [m.end() for m in re.finditer("\n", source)]

    def position(self):
        line, column = self.getpos()
        return self.offsets[line - 1] + column

    def handle_starttag(self, tag, attrs):
        start = self.position()
        raw = self.get_starttag_text()
        if not any(item in _SKIP_TAGS for item in self.stack):
            for match in re.finditer(r'''([\w:-]+)\s*=\s*(["'])(.*?)\2''', raw, re.S):
                if match[1].lower() in _UI_ATTRIBUTES:
                    self.attributes.append((start + match.start(3), start + match.end(3)))
        if tag not in _VOID_TAGS:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in self.stack:
            self.stack = self.stack[:len(self.stack) - 1 - self.stack[::-1].index(tag)]

    def handle_data(self, data):
        if not any(item in _SKIP_TAGS for item in self.stack):
            self.text.append((self.position(), self.position() + len(data)))

    def handle_entityref(self, name):
        if not any(item in _SKIP_TAGS for item in self.stack):
            start = self.position()
            end = start + len(name) + 1
            self.text.append((start, end + (self.source[end:end + 1] == ";")))

    def handle_charref(self, name):
        if not any(item in _SKIP_TAGS for item in self.stack):
            start = self.position()
            end = start + len(name) + 2
            self.text.append((start, end + (self.source[end:end + 1] == ";")))


def owned_markup(source):
    ranges = _template_ranges(source)
    masked = list(source)
    for start, end in ranges:
        masked[start:end] = ["\n" if char == "\n" else " " for char in source[start:end]]
    parser = _OwnedMarkup(source, ranges)
    parser.feed("".join(masked))
    merged = []
    for start, end in parser.text:
        if merged and merged[-1][1] == start:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged + parser.attributes, ranges


def owned_phrases(source):
    slots, ranges = owned_markup(source)
    phrases = set()
    for start, end in slots:
        cursor = start
        for begin, finish in ranges:
            if finish <= cursor or begin >= end:
                continue
            phrases.update(_phrase(source[cursor:min(begin, end)]))
            cursor = max(cursor, finish)
        phrases.update(_phrase(source[cursor:end]))
    return phrases


def _phrase(raw):
    text = normalize(html.unescape(raw))
    return {text} if re.search(r"[A-Za-z]", text) and "<" not in text and ">" not in text and not text.startswith(("#", "http://", "https://", "/")) else set()


def _ui_expression(expression):
    """Recognize owned labels/enums; never broadly translate dynamic content."""
    if isinstance(expression, nodes.Const):
        return isinstance(expression.value, str)
    if isinstance(expression, nodes.Name):
        return expression.name in _UI_NAMES or expression.name in {"column", "badge_description"}
    if isinstance(expression, nodes.Getattr):
        if expression.attr == "label":
            return isinstance(expression.node, nodes.Name) and expression.node.name in {"section", "tab", "link", "column"}
        return expression.attr in _UI_PROPERTIES or expression.attr.endswith("_label") or expression.attr == "badge"
    if isinstance(expression, nodes.Getitem):
        return isinstance(expression.node, nodes.Name) and expression.node.name in {"titles", "kinds", "labels", "source_labels", "type_headings", "status_labels", "policy_labels", "provenance_labels", "kind_labels", "choice", "_WEEKDAYS"}
    if isinstance(expression, nodes.CondExpr):
        return _ui_expression(expression.expr1) and expression.expr2 is not None and _ui_expression(expression.expr2)
    if isinstance(expression, nodes.Or):
        return _ui_expression(expression.left) and _ui_expression(expression.right)
    if isinstance(expression, nodes.Filter) and expression.name in {"capitalize", "title", "replace", "upper", "lower"}:
        return _ui_expression(expression.node)
    if isinstance(expression, nodes.Call) and isinstance(expression.node, nodes.Getattr):
        if expression.node.attr == "strftime":
            return True
        return expression.node.attr in {"replace", "title", "capitalize"} and _ui_expression(expression.node.node)
    if isinstance(expression, nodes.Call) and isinstance(expression.node, nodes.Name):
        return expression.node.name == "weekday_name"
    return False


def _owned_fallbacks(expression, output):
    """Translate a literal branch while retaining an authored name/value branch."""
    if isinstance(output, nodes.CondExpr):
        branches = (output.expr1, output.expr2)
    elif isinstance(output, nodes.Or):
        branches = (output.left, output.right)
    else:
        return expression
    replacements = []
    for index, branch in enumerate(branches):
        if not isinstance(branch, nodes.Const) or not isinstance(branch.value, str) or not _phrase(branch.value):
            continue
        matches = []
        for match in re.finditer(r'''("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')''', expression):
            try:
                if ast.literal_eval(match[0]) == branch.value:
                    matches.append(match)
            except (SyntaxError, ValueError):
                continue
        if matches:
            match = matches[0] if index == 0 else matches[-1]
            replacements.append((match.start(), match.end(), "_(" + match[0] + ")"))
    for start, end, replacement in sorted(replacements, reverse=True):
        expression = expression[:start] + replacement + expression[end:]
    return expression


def localize_template_source(source):
    slots, ranges = owned_markup(source)
    replacements = []
    for start, end in slots:
        cursor = start
        for begin, finish in ranges:
            if finish <= cursor or begin >= end:
                continue
            _static_replacement(source, cursor, min(begin, end), replacements)
            if source[begin:begin + 2] == "{{" and finish <= end:
                expression = source[begin + 2:finish - 2].strip().strip("-").strip()
                try:
                    parsed = Environment().parse("{{ " + expression + " }}")
                    output = parsed.body[0].nodes[0]
                    if _ui_expression(output):
                        translator = "ui_date" if isinstance(output, nodes.Call) and isinstance(output.node, nodes.Getattr) and output.node.attr == "strftime" else "_"
                        replacements.append((begin, finish, "{{ " + translator + "(" + expression + ") }}"))
                    else:
                        localized = _owned_fallbacks(expression, output)
                        if localized != expression:
                            replacements.append((begin, finish, "{{ " + localized + " }}"))
                except Exception:
                    # The ordinary Jinja compiler remains authoritative.
                    pass
            cursor = max(cursor, finish)
        _static_replacement(source, cursor, end, replacements)
    # WTForms widget arguments live in Jinja rather than HTML attributes.
    for begin, finish in ranges:
        if source[begin:begin + 2] != "{{":
            continue
        expression = source[begin:finish]
        for match in re.finditer(r'''\b(?:placeholder|title|aria_label|alt)\s*=\s*((?:"(?:\\.|[^"\\])*")|(?:'(?:\\.|[^'\\])*'))''', expression):
            replacements.append((begin + match.start(1), begin + match.end(1), "_(" + match[1] + ")"))
    previous = len(source)
    for start, end, value in sorted(set(replacements), reverse=True):
        if end > previous:
            continue
        source = source[:start] + value + source[end:]
        previous = start
    return source


def _static_replacement(source, start, end, replacements):
    raw = source[start:end]
    if not _phrase(raw):
        return
    leading = len(raw) - len(raw.lstrip())
    trailing = len(raw.rstrip())
    text = html.unescape(raw[leading:trailing])
    replacements.append((start + leading, start + trailing, "{{ _(" + json.dumps(text, ensure_ascii=False) + ") }}"))


class OwnedUIExtension(Extension):
    def preprocess(self, source, name, filename=None):
        return localize_template_source(source)


def init_ui(app):
    app.jinja_env.add_extension(OwnedUIExtension)
    app.jinja_env.globals.update(_=gettext, _n=ngettext, ui_language=ui_language, ui_theme=ui_theme, ui_date=ui_date)
