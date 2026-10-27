"""/wipebot key: reading what an admin pastes, and the OpenRouter sign-in link."""
import base64
import hashlib
from urllib.parse import parse_qs, urlparse

from wipebot.discord import keycmd


def test_the_code_comes_from_the_pasted_address_or_alone():
    assert keycmd.code_from("http://localhost:3000/wipebot?code=abc123DEF_-x") == "abc123DEF_-x"
    assert keycmd.code_from("  abc123DEF_-x ") == "abc123DEF_-x"
    assert keycmd.code_from("http://localhost:3000/wipebot") is None
    assert keycmd.code_from("not a code") is None


def test_a_pasted_key_names_its_provider():
    assert keycmd.provider_of("sk-or-v1-abc") == "openrouter"
    assert keycmd.provider_of("sk-ant-api03-abc") == "anthropic"
    assert keycmd.provider_of("sk-proj-abc") is None  # OpenAI's: with #32


def test_the_sign_in_link_carries_the_challenge_of_the_verifier(monkeypatch):
    monkeypatch.setenv("WIPEBOT_OPENROUTER_CALLBACK", "https://wipebot.example/openrouter")
    query = parse_qs(urlparse(keycmd.login_url("v" * 64)).query)
    expected = base64.urlsafe_b64encode(hashlib.sha256(b"v" * 64).digest()).rstrip(b"=").decode()
    assert query == {"callback_url": ["https://wipebot.example/openrouter"], "code_challenge": [expected],
                     "code_challenge_method": ["S256"]}
