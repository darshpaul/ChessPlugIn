import base64
import hashlib
import time

import pytest
from fastapi.testclient import TestClient

import auth
from server import app

CLIENT = "https://chatgpt.com/oauth/client.json"
CALLBACK = "https://chatgpt.com/connector_platform_oauth_redirect"
LEGACY_CLIENT = "https://chatgpt.com/oauth/test-callback/client.json"
LEGACY_CALLBACK = "https://chatgpt.com/connector/oauth/test-callback"
PASSWORD = "test-only-password"
VERIFIER = "v" * 64


@pytest.fixture(autouse=True)
def configured_auth(monkeypatch):
    monkeypatch.setenv("CHESS_COACH_LOGIN_PASSWORD", PASSWORD)
    monkeypatch.setenv("CHESS_COACH_OAUTH_SIGNING_KEY", "test-only-signing-key-" * 3)
    auth.codes.clear()
    auth.failures.clear()
    auth.client_metadata.clear()
    for client, callback in [(CLIENT, CALLBACK), (LEGACY_CLIENT, LEGACY_CALLBACK)]:
        auth.client_metadata[client] = (time.monotonic() + 300, {
            "client_id": client,
            "redirect_uris": [callback],
            "token_endpoint_auth_method": "private_key_jwt",
            "token_endpoint_auth_methods_supported": ["none", "private_key_jwt"],
        })
    yield
    auth.codes.clear()
    auth.failures.clear()
    auth.client_metadata.clear()


@pytest.fixture(scope="session")
def client():
    with TestClient(app, base_url=auth.BASE, follow_redirects=False) as session:
        yield session


@pytest.fixture
def params():
    return {
        "response_type": "code",
        "client_id": CLIENT,
        "redirect_uri": CALLBACK,
        "code_challenge": base64.urlsafe_b64encode(
            hashlib.sha256(VERIFIER.encode()).digest()
        ).decode().rstrip("="),
        "code_challenge_method": "S256",
        "scope": "chess:analyze offline_access",
        "state": "preserve-this-state+&=",
        "resource": auth.RESOURCE,
    }
