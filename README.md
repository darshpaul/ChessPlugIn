# Chess Coach — protected ChatGPT MCP plugin

Fetch Chess.com public games, analyze them with Stockfish, and review positions on an interactive board inside chat.

## Interactive analysis board

Ask ChessRailway: **"Open my latest rapid game on a chessboard."** The model fetches game data with `get_board_games` and renders it with `show_chess_board`. The board can also open from a thread's app entrypoint on compatible hosts.

- Replay the recorded moves with buttons, arrow keys, or autoplay.
- Drag pieces or click a source and destination to explore legal variations. Castling, en passant, and all four promotions are supported. **Back to game** restores the original line.
- Right-drag to draw an arrow, or right-click to highlight a square. The Arrow and Highlight buttons support touch and trackpads without a right button. Choose green, red, blue, or orange; marks belong to each position.
- Ask the coach to draw arrows, highlight threats, or add a playable line. Expanded desktop views expose `chess_board_read`, `chess_board_go_to`, `chess_board_annotate`, `chess_board_play_line`, and `chess_board_load` through WebMCP. Hosts without WebMCP can render a new board with the requested annotations.
- **Ask coach** sends the precise FEN, move history, markings, and current engine result to the conversation. Normal navigation also updates model context without sending a message.
- **Engine & lines** runs the server's actual Stockfish for 1, 3, or 8 seconds and shows up to three playable candidate lines. Scores are from White's perspective; mate counts are kept separate from pawn scores. History is validated and retained for repetition detection.
- Load recent rapid games by username, paste one PGN/FEN, or try the included mating puzzle. PGN side variations are preserved when imported through the connected server; the standalone preview imports the main line.

The board uses [react-chessboard](https://github.com/Clariity/react-chessboard) (MIT), [chess.js](https://github.com/jhlywa/chess.js) (BSD-2-Clause), and the [MCP Apps SDK](https://github.com/modelcontextprotocol/ext-apps). It bundles all code and piece artwork into one HTML resource; no CDN, external asset requests, or third-party chess service is needed to move or draw. Bundled dependency license notices are included in the UI and `web/dist/THIRD_PARTY_NOTICES.txt`.

The static preview is available at [the board URL](https://chess-coach-mcp-production-6a7c.up.railway.app/board). It supports local replay and annotations. Fetching games, Stockfish, and sending questions require the authenticated chat connection. The public preview contains no account data or credentials.

After updating an existing connection, refresh its available tools in the host if needed. The new tool is `show_chess_board`; keep the same MCP URL and OAuth connection. Live rendering in ChatGPT must be verified there separately from automated browser tests.

### Build the board

```sh
cd web
npm ci --ignore-scripts
npm run check
npm run build
cd ..
```

Railway's multi-stage Docker build performs this automatically. `web/dist` and `node_modules` are generated, not committed. When publishing an incompatible UI change, bump `BOARD_URI` in `server.py` to avoid stale host resource caches.

Board interactions are temporary analysis state. The widget can restore its view when the host supports widget state; it does not save a permanent study or change any Chess.com game.

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
# Build web/ first as above. Install Stockfish for the engine integration tests.
python -m pytest -q
```

Tests use dummy secrets and stub ChatGPT client metadata. Chromium tests intercept every request locally, including the ChatGPT callback, and verify that submitting the password really leaves the login page. API tests cover code exchange, refresh, replay prevention, resource/client/redirect/PKCE binding, authenticated `initialize`, `tools/list`, and `tools/call` on the production hostname. They also verify that invalid tokens, hosts, and origins are rejected. Live ChatGPT verification still requires the owner's password on the authorization page.

Board tests load the production bundle in a sandboxed iframe and use the real MCP Apps bridge against the local OAuth-protected server. They cover model-driven annotations, context sharing, actual drag-and-drop, legal move rules, promotions, replay, engine lines, invalid input, and narrow/wide layouts. This test host is not a substitute for a live ChatGPT check.

### Diagnosing a stalled connection

- `POST /authorize` returns 303, with no subsequent `POST /token`: check the browser console. The login page's `form-action` must allow `https://chatgpt.com`, because Chromium applies it to the form's redirect too.
- Token exchange succeeds but `/mcp` returns 421: check the MCP transport's allowed hosts. The configured public hostname must be allowed; keep DNS rebinding protection enabled.
- Successful connection: `/authorize` returns 303, `/token` returns 200, then authenticated `/mcp` requests return 200 and list the tools below.

Keep one replica and one worker while authorization codes are stored in memory. Access and refresh tokens use the persistent Railway signing key, so keep that key stable across deployments. The MCP SDK version is pinned to the version exercised by the regression tests.

## Available tools

- `list_recent_games`: fetch recent public games.
- `analyze_recent_games`: run Stockfish on recent games and identify recurring mistakes.
- `analyze_pgn`: analyze one supplied PGN.
- `get_board_games`: fetch public PGNs for the interactive board.
- `show_chess_board`: display a validated game/position, arrows, highlights, notes, and variations.
- `analyze_board_position`: analyze one position with Stockfish, retaining supplied move history.

`show_chess_board` links to `ui://chess-coach/board-v1.html` using the standard `_meta.ui.resourceUri` and the ChatGPT compatibility alias. Other tools return data without remounting the board. Resources and tools remain protected by the existing OAuth middleware.

The chess analyses use short Stockfish searches; move evaluations are approximate. Hosting may incur Railway charges.
