"""Credential-safe client for the official detector's loopback API."""
import json
import urllib.error
import urllib.request


class ImportIssue(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class LocalClient:
    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}"
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect()
        )
        self.token = self.request("/api/bootstrap")["token"]

    def request(self, path, body=None):
        headers = {"Origin": self.base}
        if getattr(self, "token", None):
            headers["X-Meow-Token"] = self.token
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers=headers,
        )
        try:
            with self.opener.open(request, timeout=15) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            # Response bodies and exception strings may contain submitted data.
            raise ImportIssue(f"Local detector returned HTTP {exc.code}.") from None
        except (OSError, ValueError):
            raise ImportIssue("Cannot reach local detector; reconnect from Relay Desk.") from None
