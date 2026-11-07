# Chess Coach — protected ChatGPT MCP plugin

Fetch Chess.com public games and use Stockfish to detect opening errors, particularly moves 5–15.

## Deployment

Deployed at https://chess-coach-mcp-production-6a7c.up.railway.app/mcp using Railway's Docker builder.

This plugin uses **OAuth 2.1 Authorization Code + PKCE**, with a private owner-password authorization screen. It is not anonymous. Do not choose "No authentication" when connecting.

## Configure OAuth secrets in Railway

Set these on the `chess-coach-mcp` production service:
- `CHESS_COACH_PUBLIC_URL=https://chess-coach-mcp-production-6a7c.up.railway.app`
- `CHESS_COACH_LOGIN_PASSWORD`: a strong password, used only on the Chess Coach login screen.
- `CHESS_COACH_OAUTH_SIGNING_KEY`: a random secret at least 32 characters.

The password and signing key must **never** be committed to GitHub or pasted into chats. Keep one Railway replica because authorization codes are temporarily stored in server memory.

## Connect in ChatGPT

1. Go to **Plugins** and add a custom MCP server.
2. Name: `Chess Coach`
3. Server URL: `https://chess-coach-mcp-production-6a7c.up.railway.app/mcp`
4. Authentication: choose **OAuth** or **Use server OAuth discovery**.
5. Allow the connection and authenticate on the Chess Coach owner-password page.
6. Request: "Analyze the last 3 rapid Chess.com games for DarshPaul and focus on moves 5–15."

If connection creation offers CIMD versus DCR, choose **CIMD** (client ID metadata document) for this implementation. The authentication server supports the official ChatGPT public CIMD clients and ChatGPT's standard redirect URIs. It does **not** implement DCR or third-party OAuth provider registration.

The server validates the exact redirect URI against ChatGPT's published client metadata and supports public-client token exchange (`none`) with S256 PKCE. Its authorization response includes the issuer (`iss`), allowing new connections to use ChatGPT's stable callback. Existing callback-specific connections remain supported.

After an authentication deployment, close any old authorization page and start **Connect** again from ChatGPT. An already-open page retains its previous browser security policy, and any pending authorization codes are lost on restart.

## Verification

- `GET /health`: confirms process running.
- `GET /.well-known/oauth-protected-resource`: announces OAuth authorization server.
- `GET /.well-known/oauth-authorization-server`: announces OAuth PKCE endpoints.
- `POST /mcp` without a token: returns HTTP 401 with `WWW-Authenticate` challenge.
- `GET /authorize` with valid ChatGPT client, PKCE, redirect and state: displays a login form.

A successful HTTP health check alone does not prove an actual ChatGPT OAuth handshake has completed. Test from ChatGPT's connector flow.

### Regression tests

```sh
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
python -m pytest -q
```

Tests use dummy secrets and stub ChatGPT client metadata. Chromium tests intercept every request locally, including the ChatGPT callback, and verify that submitting the password really leaves the login page. API tests cover code exchange, refresh, replay prevention, resource/client/redirect/PKCE binding, authenticated `initialize`, `tools/list`, and `tools/call` on the production hostname. They also verify that invalid tokens, hosts, and origins are rejected. Live ChatGPT verification still requires the owner's password on the authorization page.

### Diagnosing a stalled connection

- `POST /authorize` returns 303, with no subsequent `POST /token`: check the browser console. The login page's `form-action` must allow `https://chatgpt.com`, because Chromium applies it to the form's redirect too.
- Token exchange succeeds but `/mcp` returns 421: check the MCP transport's allowed hosts. The configured public hostname must be allowed; keep DNS rebinding protection enabled.
- Successful connection: `/authorize` returns 303, `/token` returns 200, then authenticated `/mcp` requests return 200 and list the three tools below.

Keep one replica and one worker while authorization codes are stored in memory. Access and refresh tokens use the persistent Railway signing key, so keep that key stable across deployments. The MCP SDK version is pinned to the version exercised by the regression tests.

## Available tools

- `list_recent_games`: fetch recent public games.
- `analyze_recent_games`: run Stockfish on recent games and identify recurring mistakes.
- `analyze_pgn`: analyze one supplied PGN.

The chess analyses use short Stockfish searches; move evaluations are approximate. Hosting may incur Railway charges.
