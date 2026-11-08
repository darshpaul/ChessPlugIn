import asyncio
import json
import time
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest

import auth
import server
from conftest import CALLBACK, CLIENT, LEGACY_CALLBACK, LEGACY_CLIENT, PASSWORD, VERIFIER


def issue_code(client, params):
    response = client.post("/authorize", data={**params, "password": PASSWORD})
    assert response.status_code == 303
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["state"] == [params["state"]]
    assert query["iss"] == [auth.BASE]
    return query["code"][0]


def token_form(code, params):
    return {
        "grant_type": "authorization_code", "code": code,
        "client_id": params["client_id"], "redirect_uri": params["redirect_uri"],
        "code_verifier": VERIFIER, "resource": auth.RESOURCE,
    }


def rpc(client, token, method, params=None, **headers):
    return client.post("/mcp", headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-06-18",
        **headers,
    }, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})


@pytest.mark.parametrize("legacy", [False, True])
def test_oauth_discovery_and_tool_call(client, params, monkeypatch, legacy):
    if legacy:
        params.update(client_id=LEGACY_CLIENT, redirect_uri=LEGACY_CALLBACK)
    metadata = client.get("/.well-known/oauth-authorization-server").json()
    assert metadata["authorization_response_iss_parameter_supported"] is True
    assert metadata["token_endpoint_auth_methods_supported"] == ["none"]
    assert metadata["code_challenge_methods_supported"] == ["S256"]
    for path in ["/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"]:
        assert client.get(path).json()["resource"] == auth.RESOURCE
    page = client.get("/authorize", params=params)
    assert page.status_code == 200
    assert "form-action 'self' https://chatgpt.com" in page.headers["content-security-policy"]
    response = client.post("/token", data=token_form(issue_code(client, params), params))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    tokens = response.json()
    access = tokens["access_token"]
    assert auth.verify_access(access)
    assert not auth.verify_access(tokens["refresh_token"])
    init = rpc(client, access, "initialize", {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "regression", "version": "1"},
    }, Origin="https://chatgpt.com")
    assert init.status_code == 200
    assert "tools" in init.json()["result"]["capabilities"]
    discovery = rpc(client, access, "tools/list")
    assert discovery.status_code == 200
    tools = discovery.json()["result"]["tools"]
    assert {t["name"] for t in tools} == {"list_recent_games", "analyze_recent_games", "analyze_pgn",
                                            "show_chess_board", "get_board_games", "analyze_board_position"}
    for tool in tools:
        assert tool["_meta"]["securitySchemes"] == [{"type": "oauth2", "scopes": ["chess:analyze"]}]
        assert tool["annotations"]["readOnlyHint"] is True
    monkeypatch.setattr(server, "fetch_recent_games", lambda *args: [])
    result = rpc(client, access, "tools/call", {
        "name": "list_recent_games", "arguments": {"username": "DarshPaul", "count": 3},
    })
    assert result.status_code == 200
    assert result.json()["result"]["isError"] is False
    assert json.loads(result.json()["result"]["content"][0]["text"]) == {"username": "DarshPaul", "games": []}
    refresh = client.post("/token", data={
        "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
        "client_id": params["client_id"], "resource": auth.RESOURCE,
    })
    assert refresh.status_code == 200
    assert rpc(client, refresh.json()["access_token"], "tools/list").status_code == 200


@pytest.mark.parametrize("updates,error", [
    ({"code_verifier": "wrong" * 13}, "invalid_grant"),
    ({"code_verifier": "v" * 42}, "invalid_grant"),
    ({"client_id": LEGACY_CLIENT}, "invalid_grant"),
    ({"redirect_uri": LEGACY_CALLBACK}, "invalid_grant"),
    ({"resource": "https://wrong.example/mcp"}, "invalid_target"),
    ({"resource": auth.BASE}, "invalid_target"),
])
def test_exchange_is_bound_and_bad_exchange_does_not_burn_code(client, params, updates, error):
    data = token_form(issue_code(client, params), params)
    failed = client.post("/token", data={**data, **updates})
    assert failed.status_code == 400
    assert failed.json()["error"] == error
    assert client.post("/token", data=data).status_code == 200
    assert client.post("/token", data=data).json()["error"] == "invalid_grant"


def test_expired_code_is_rejected(client, params):
    code = issue_code(client, params)
    auth.codes[code]["exp"] = time.time() - 1
    assert client.post("/token", data=token_form(code, params)).json()["error"] == "invalid_grant"


@pytest.mark.parametrize("updates,error", [
    ({"response_type": "token"}, "unsupported_response_type"),
    ({"code_challenge_method": "plain"}, "invalid_request"),
    ({"code_challenge": "é" * 43}, "invalid_request"),
    ({"resource": auth.BASE}, "invalid_target"),
    ({"scope": "chess:analyze admin"}, "invalid_scope"),
    ({"state": ""}, "invalid_request"),
    ({"redirect_uri": "https://evil.example/callback"}, "invalid_client"),
    ({"redirect_uri": CALLBACK + "?next=evil"}, "invalid_client"),
    ({"redirect_uri": LEGACY_CALLBACK}, "invalid_client"),
    ({"client_id": "https://chatgpt.com:bad/oauth/client.json"}, "invalid_client"),
    ({"client_id": "https://chatgpt.com.evil.example/oauth/client.json"}, "invalid_client"),
])
def test_bad_authorization_requests_are_rejected(client, params, updates, error):
    response = client.get("/authorize", params={**params, **updates})
    assert response.status_code == 400
    assert response.json()["error"] == error
    assert not auth.codes


def test_password_errors_are_visible_and_no_code_is_issued(client, params):
    for password in ["wrong", "wrong-unicode-é"]:
        response = client.post("/authorize", data={**params, "password": password})
        assert response.status_code == 200
        assert "Incorrect password." in response.text
        assert not auth.codes


def test_refresh_resource_client_and_type_are_checked(client, params):
    tokens = client.post("/token", data=token_form(issue_code(client, params), params)).json()
    data = {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": CLIENT}
    assert client.post("/token", data={**data, "resource": auth.BASE}).json()["error"] == "invalid_target"
    assert client.post("/token", data={**data, "client_id": LEGACY_CLIENT}).json()["error"] == "invalid_grant"
    assert client.post("/token", data={**data, "refresh_token": tokens["access_token"]}).json()["error"] == "invalid_grant"


def test_invalid_tokens_and_hosts_stay_blocked(client):
    for token in ["", "invalid", auth.encode("access", CLIENT, -60), auth.encode("refresh", CLIENT, 3600)]:
        response = rpc(client, token, "tools/list")
        assert response.status_code == 401
        assert "resource_metadata=" in response.headers["www-authenticate"]
    token = auth.encode("access", CLIENT, 3600)
    assert rpc(client, token, "tools/list", Host="evil.example").status_code == 421
    assert rpc(client, token, "tools/list", Origin="https://evil.example").status_code == 403


@pytest.mark.parametrize("claim,value", [("aud", auth.BASE), ("iss", "https://wrong.example"), ("scope", "admin")])
def test_access_claims_enforced(client, claim, value):
    import os
    key = os.environ["CHESS_COACH_OAUTH_SIGNING_KEY"]
    payload = jwt.decode(auth.encode("access", CLIENT, 3600), key, algorithms=["HS256"], audience=auth.RESOURCE)
    payload[claim] = value
    token = jwt.encode(payload, key, algorithm="HS256")
    assert rpc(client, token, "tools/list").status_code == 401


def test_cimd_fetches_exact_document_and_accepts_plural_auth_methods(monkeypatch):
    auth.client_metadata.clear()
    calls = []
    class Session:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, **kwargs):
            calls.append(url)
            return httpx.Response(200, request=httpx.Request("GET", url), json={
                "client_id": CLIENT, "redirect_uris": [CALLBACK],
                "token_endpoint_auth_method": "private_key_jwt",
                "token_endpoint_auth_methods_supported": ["none", "private_key_jwt"],
            })
    monkeypatch.setattr(auth.httpx, "AsyncClient", Session)
    assert asyncio.run(auth.check_client_metadata(CLIENT, CALLBACK)) == ""
    assert asyncio.run(auth.check_client_metadata(CLIENT, CALLBACK)) == ""
    assert calls == [CLIENT]
    assert asyncio.run(auth.check_client_metadata(CLIENT, LEGACY_CALLBACK)) == "invalid_client"
    assert asyncio.run(auth.check_client_metadata("https://127.0.0.1/client.json", CALLBACK)) == "invalid_client"
    assert calls == [CLIENT]


def test_cimd_failure_fails_closed(monkeypatch):
    auth.client_metadata.clear()
    class Session:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, **kwargs): raise httpx.ConnectError("offline")
    monkeypatch.setattr(auth.httpx, "AsyncClient", Session)
    assert asyncio.run(auth.check_client_metadata(CLIENT, CALLBACK)) == "temporarily_unavailable"
