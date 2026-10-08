"""Single-process Linux baseline: process-local rate limiting stays coherent.

Put behind HTTPS on a private listener. Provider runtime limits must be sized
for the configured uploads. More workers require shared limiter storage first.
"""
import os

bind = os.environ.get("WSGI_BIND", "127.0.0.1:8000")
workers = 1
worker_class = "gthread"
threads = 4
timeout = 120
graceful_timeout = 120
keepalive = 5
max_requests = 0
preload_app = False
accesslog = "-"
errorlog = "-"
loglevel = "warning"
# No client IP, query string, user agent, request body or credential headers.
access_log_format = '%(m)s %(U)s %(s)s %(L)s'
capture_output = False
