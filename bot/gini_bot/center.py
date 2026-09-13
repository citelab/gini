"""Talking to the Teaching Center. Two calls out, one call back.

The bot holds no intelligence, and this file is where that stops being a slogan: it pushes what was
said and collects what to post, and every decision in between — what groups with what, what a
teacher is shown, what words go back to Discord — happens behind the Center. That is what lets the
bot be restarted, rate-limited, banned or rewritten without touching any of it.

**TLS is verified, like everything else that talks to the Center.** `services/tc_submit` refuses
plain HTTP and verifies properly, and a bot that shrugged at a certificate would be the weak point
in a system where the same server holds staff password hashes. For a trial Center with a self-signed
certificate, point `SSL_CERT_FILE` at it — the same thing `run.sh` tells you to do for gBuilder —
rather than turning verification off.

Failure is never fatal here. The Center being down means Discord traffic is buffered in the bot's
own log and pushed when it comes back; it does not mean the bot stops listening.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger("gini_bot")

TIMEOUT = 15.0


class Center:
    """The Teaching Center, or nothing at all.

    `configured` is False when no URL is set, and every method then does nothing successfully. That
    is deliberate: step 1 runs the bot with no Center at all, and it should keep working exactly as
    it did rather than filling the log with connection errors.
    """

    def __init__(self, url: str = "", key: str = "") -> None:
        self.url = (url or os.environ.get("GINI_TC_URL", "")).rstrip("/")
        self.key = key or os.environ.get("GINI_BOT_KEY", "")
        self.configured = bool(self.url and self.key)
        if self.url and not self.url.lower().startswith("https://"):
            # The same refusal tc_submit makes, for the same reason: this carries a shared key.
            raise ValueError(f"GINI_TC_URL must be https:// — got {self.url}")

    def _call(self, path: str, body=None):
        req = urllib.request.Request(
            self.url + path,
            data=json.dumps(body).encode() if body is not None else None,
            method="POST" if body is not None else "GET",
            headers={"X-GINI-Bot-Key": self.key,
                     **({"Content-Type": "application/json"} if body is not None else {})})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read() or b"null")

    def push(self, observations: list) -> int:
        """Hand over what was said. Returns how many the Center took, 0 if it could not be reached.

        Batched by the caller. The Center ignores a message it already has, so re-pushing after a
        failure is safe and needs no bookkeeping here.
        """
        if not self.configured or not observations:
            return 0
        try:
            return int(self._call("/api/ai/observe", {"observations": observations})
                       .get("stored", 0))
        except Exception as e:                       # noqa: BLE001 — never stop listening
            log.warning("could not reach the Teaching Center (%s); keeping it local", e)
            return 0

    def outbox(self) -> list:
        """Replies a teacher has written, already worded and addressed. Empty on any failure."""
        if not self.configured:
            return []
        try:
            return self._call("/api/ai/outbox") or []
        except Exception as e:                       # noqa: BLE001
            log.warning("could not collect replies (%s)", e)
            return []

    def settle(self, reply_id: int, error: str = "") -> None:
        """Say what happened to one reply. An error is recorded, not retried for ever — a missing
        permission or a deleted message will fail identically every time, and a queue that keeps
        trying is a queue nobody can read."""
        if not self.configured:
            return
        try:
            self._call("/api/ai/sent", {"id": reply_id, "error": error})
        except Exception as e:                       # noqa: BLE001
            log.warning("could not confirm a reply was sent (%s)", e)
