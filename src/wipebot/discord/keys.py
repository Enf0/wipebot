"""Servers' own model keys (/wipebot setup) and raiders' Warcraft Logs tokens (/login), kept encrypted: the database
alone never gives them away.

The encryption key comes from the host's settings, never the database: WIPEBOT_KEY_SECRET, or else the Warcraft
Logs client secret the bot runs with anyway. Changing it makes the saved keys unreadable, and servers add theirs
again. A key is used to answer that server's questions, never shown or logged.
"""
import base64
import hashlib
import os
from dataclasses import dataclass, field

from cryptography.fernet import Fernet, InvalidToken

from .config import OPENROUTER_URL

PROVIDERS = ("openrouter", "anthropic")
MODEL_ENV = "ANTHROPIC_MODEL"  # Claude Code's own name for the model a session runs on


@dataclass(frozen=True)
class ServerKey:
    provider: str  # one of PROVIDERS
    key: str = field(repr=False)  # never in a repr, which a log line or a traceback may print

    def env(self, model: str | None = None) -> dict[str, str]:
        """What Claude Code needs in its environment to answer on this key (as config.model_env for the bot's), and
        on `model`, the one the server chose for its answers (MODEL_ENV), when it chose one."""
        chosen = {MODEL_ENV: model} if model else {}
        if self.provider == "openrouter":
            return {"ANTHROPIC_BASE_URL": OPENROUTER_URL, "ANTHROPIC_AUTH_TOKEN": self.key, "ANTHROPIC_API_KEY": "",
                    **chosen}
        return {"ANTHROPIC_API_KEY": self.key, "ANTHROPIC_BASE_URL": "", "ANTHROPIC_AUTH_TOKEN": "", **chosen}


def _fernet() -> Fernet:
    secret = os.environ.get("WIPEBOT_KEY_SECRET") or os.environ.get("WCL_CLIENT_SECRET") or ""
    if not secret:
        raise RuntimeError("no WIPEBOT_KEY_SECRET or WCL_CLIENT_SECRET to encrypt servers' keys with")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"wipebot server keys: " + secret.encode()).digest()))


def seal(key: str) -> str:
    return _fernet().encrypt(key.encode()).decode()


def unseal(sealed: str) -> str | None:
    """The key, or None when it can't be read (the secret changed)."""
    try:
        return _fernet().decrypt(sealed.encode()).decode()
    except InvalidToken:
        return None
