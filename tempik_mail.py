"""Tempik mail adapter for the ProxyScrape harvester.

Replaces the pack's Cloudflare Email Worker + Node receiver (:3333) with the
Tempik-compatible disposable-mail API. Same two
operations the harvester needs: mint an address, then wait for the code.

ProxyScrape sends a hex code ("verification code: <hex>"), not 6 digits, so the
extractor accepts both.
"""
import json
import random
import re
import string
import time
import urllib.error
import urllib.request

DEFAULT_BASE = "https://mail.example.com"
DEFAULT_DOMAIN = "example.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36")

CODE_PATTERNS = [
    re.compile(r"verification\s*code[:\s]*([a-f0-9]{6,12})", re.I),
    re.compile(r"\b([a-f0-9]{10})\b", re.I),
    re.compile(r"\b(\d{6})\b"),
]


class TempikMail:
    def __init__(self, base=DEFAULT_BASE, domain=DEFAULT_DOMAIN, timeout=15):
        self.base = base.rstrip("/")
        self.domain = domain
        self.timeout = timeout
        self.session_id = None
        self._init_session()

    def _req(self, path, method="GET", data=None):
        headers = {"User-Agent": UA}
        if self.session_id:
            headers["x-session-id"] = self.session_id
        payload = None
        if data is not None:
            headers["Content-Type"] = "application/json"
            payload = json.dumps(data).encode()
        req = urllib.request.Request(f"{self.base}{path}", data=payload,
                                     headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode())

    def _init_session(self):
        try:
            self.session_id = self._req("/api/session").get("sessionId")
        except Exception:
            self.session_id = None

    def new_address(self, local_part=None):
        local_part = local_part or ("px_" + "".join(
            random.choices(string.ascii_lowercase + string.digits, k=10)))
        res = self._req("/api/inboxes", method="POST",
                        data={"domain": self.domain, "localPart": local_part})
        return res["address"]

    def messages(self, address):
        try:
            msgs = self._req(f"/api/inboxes/{address}/messages")
            return msgs if isinstance(msgs, list) else []
        except Exception:
            return []

    @staticmethod
    def extract_code(msgs):
        for m in msgs:
            code = m.get("code")
            if code:
                return str(code)
            blob = " ".join(str(m.get(k) or "") for k in
                            ("subject", "preview", "body", "text", "html"))
            for pat in CODE_PATTERNS:
                hit = pat.search(blob)
                if hit:
                    return hit.group(1)
        return None

    def wait_otp(self, address, timeout=150, interval=3):
        start = time.time()
        while time.time() - start < timeout:
            msgs = self.messages(address)
            if msgs:
                code = self.extract_code(msgs)
                if code:
                    return code
            time.sleep(interval)
        return None


if __name__ == "__main__":
    mail = TempikMail()
    addr = mail.new_address()
    print("address:", addr)
    print("waiting 20s for any mail...")
    print("otp:", mail.wait_otp(addr, timeout=20))
