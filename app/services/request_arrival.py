"""Trusted receipt time of the complete request body, before Flask/DB work.

The browser supplies no clock. A request begun before a deadline but whose
body finishes after it is late. Only a fully received bounded form body can
carry the earlier receipt time through authorization and lock waits.
"""
from datetime import datetime, timezone

from flask import request

_KEY = "aelms.body_received_at"


def _utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class _ReceiptInput:
    def __init__(self, stream, environ, length):
        self.stream, self.environ, self.length = stream, environ, length
        self.received = 0

    def _note(self, count):
        self.received += count
        if self.received >= self.length and _KEY not in self.environ:
            self.environ[_KEY] = _utc()

    def read(self, size=-1):
        data = self.stream.read(size)
        self._note(len(data))
        return data

    def readline(self, size=-1):
        data = self.stream.readline(size)
        self._note(len(data))
        return data

    def readinto(self, buffer):
        reader = getattr(self.stream, "readinto", None)
        if reader is None:
            data = self.read(len(buffer))
            buffer[:len(data)] = data
            return len(data)
        count = reader(buffer)
        if count:
            self._note(count)
        return count

    def __getattr__(self, name):
        return getattr(self.stream, name)


class RequestArrivalMiddleware:
    def __init__(self, application, max_length):
        self.application, self.max_length = application, max_length

    def __call__(self, environ, start_response):
        # Remove any upstream/user-supplied value; only this wrapper sets it.
        environ.pop(_KEY, None)
        try:
            length = int(environ.get("CONTENT_LENGTH", ""))
        except (TypeError, ValueError):
            length = 0
        if 0 < length <= self.max_length:
            environ["wsgi.input"] = _ReceiptInput(environ["wsgi.input"], environ, length)
        return self.application(environ, start_response)


def submission_received_at():
    # Force the complete form to be read before taking the wrapper's fact.
    request.form
    value = request.environ.get(_KEY)
    if not isinstance(value, datetime) or value.tzinfo is not None:
        # Unknown-length/unsupported inputs receive no early-time exception.
        return _utc()
    return value
