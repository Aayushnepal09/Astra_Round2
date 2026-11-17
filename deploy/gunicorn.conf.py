"""Small, bounded process pool for the Oracle evaluation VM."""

bind = "127.0.0.1:8000"
workers = 2
worker_class = "sync"
threads = 1
# DOCX conversion, validation and cleanup can run sequentially in one request.
timeout = 150
graceful_timeout = 150
max_requests = 200
max_requests_jitter = 20
forwarded_allow_ips = "127.0.0.1"
secure_scheme_headers = {"X-FORWARDED-PROTO": "https"}
# Invitation URLs and query strings must not enter routine access logs.
accesslog = None
errorlog = "-"
loglevel = "warning"
capture_output = False
