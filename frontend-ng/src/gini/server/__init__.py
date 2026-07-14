"""GINI server — an authenticated broker in front of Docker/Kata.

Students never get a Docker socket or SSH. gBuilder sends a *topology* (the device/link
model, not a compose file) to this server, which runs on the Kata host; the server compiles
it with GINI's own trusted compiler, enforces policy (allowed images, Kata/runc runtimes
only, no privileged or host mounts, per-student resource caps + namespacing), and runs it.
The constrained API is the only surface students can reach — Docker never sees a
student-supplied command.

Pieces:
  * auth.py    — flat user file (PBKDF2 hashes) + HMAC-signed session tokens.
  * policy.py  — validate/sanitize a compiled RuntimeConfig before it can run.
  * session.py — per-student project name + workdir (multi-tenant isolation).
  * app.py     — the request router (GiniServer.handle) + a stdlib HTTP wrapper.
"""
from .app import GiniServer
from .auth import Tokens, UserStore, hash_password
from .policy import PolicyError, enforce
from .session import SessionManager

__all__ = ["GiniServer", "UserStore", "Tokens", "hash_password",
           "PolicyError", "enforce", "SessionManager"]
