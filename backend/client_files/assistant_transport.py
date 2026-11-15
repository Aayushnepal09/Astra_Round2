"""Single fixed-endpoint inference call; parent enforces the total process deadline."""

import json
import socket
import sys
import urllib.error
import urllib.request
import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    try:
        data = json.load(sys.stdin)
        request = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions",
            data=json.dumps(data["body"], ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": "Bearer " + data["key"],
                     "Content-Type": "application/json", "Accept": "application/json",
                     "User-Agent": "Astra-Agreement-Assistant/1.0"}, method="POST",
        )
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=data["timeout"]) as response:
            raw = response.read(data["max_bytes"] + 1)
        if len(raw) > data["max_bytes"]:
            raise ValueError
        result = {"status": 200, "body": json.loads(raw)}
    except urllib.error.HTTPError as error:
        result = {"status": error.code}
        retry = error.headers.get("Retry-After") if error.headers else None
        if retry:
            try:
                seconds = float(retry)
            except ValueError:
                try:
                    seconds = (parsedate_to_datetime(retry) - datetime.now(timezone.utc)).total_seconds()
                except (TypeError, ValueError, OverflowError):
                    seconds = 0
            if math.isfinite(seconds) and 0 < seconds <= 86400:
                result["retryAfter"] = math.ceil(seconds)
    except (TimeoutError, socket.timeout):
        result = {"status": 504}
    except urllib.error.URLError as error:
        result = {"status": 504 if isinstance(error.reason, (TimeoutError, socket.timeout)) else 503}
    except Exception:
        result = {"status": 503}
    sys.stdout.write(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
