"""Real Chromium navigation regression; all browser traffic is intercepted locally."""
import base64
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
from playwright.sync_api import sync_playwright

import auth
from conftest import LEGACY_CALLBACK, LEGACY_CLIENT, PASSWORD
from test_oauth import token_form


@pytest.mark.parametrize("legacy", [False, True])
def test_submit_reaches_chatgpt_and_code_exchanges(client, params, legacy):
    if legacy:
        params.update(client_id=LEGACY_CLIENT, redirect_uri=LEGACY_CALLBACK)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            callbacks = []
            csp_errors = []
            page.on("console", lambda message: csp_errors.append(message.text)
                    if "form-action" in message.text else None)
            # CDP intercepts every redirect hop, so no dummy authorization code
            # is ever sent to the real ChatGPT callback during this local test.
            session = page.context.new_cdp_session(page)
            def intercept(event):
                request = event["request"]
                if request["url"].startswith(auth.BASE + "/"):
                    response = client.request(request["method"], request["url"],
                                              headers=request["headers"], content=request.get("postData"))
                    status, headers, body = response.status_code, dict(response.headers), response.content
                elif request["url"].startswith(params["redirect_uri"] + "?"):
                    callbacks.append(request["url"])
                    status, headers, body = 200, {"content-type": "text/html"}, b"<h1>Callback received</h1>"
                else:
                    session.send("Fetch.failRequest", {"requestId": event["requestId"], "errorReason": "BlockedByClient"})
                    return
                session.send("Fetch.fulfillRequest", {
                    "requestId": event["requestId"], "responseCode": status,
                    "responseHeaders": [{"name": k, "value": v} for k, v in headers.items()],
                    "body": base64.b64encode(body).decode(),
                })
            session.on("Fetch.requestPaused", intercept)
            session.send("Fetch.enable", {"patterns": [{"urlPattern": "*"}]})
            page.goto(auth.BASE + "/authorize?" + urlencode(params))
            page.get_by_label("Password").fill(PASSWORD)
            page.get_by_role("button", name="Authorize Chess Coach").click()
            page.wait_for_url(params["redirect_uri"] + "?**", timeout=5000)
            assert page.get_by_role("heading", name="Callback received").is_visible()
            assert len(callbacks) == 1
            assert not csp_errors
            query = parse_qs(urlparse(callbacks[0]).query)
            assert query["state"] == [params["state"]]
            assert query["iss"] == [auth.BASE]
            response = client.post("/token", data=token_form(query["code"][0], params))
            assert response.status_code == 200
            assert auth.verify_access(response.json()["access_token"])
        finally:
            browser.close()
