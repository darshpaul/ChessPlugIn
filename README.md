# Chess Coach — ChatGPT MCP integration

A personal Chess.com Stockfish coach, deployed as a Streamable HTTP MCP server.

## What it does

- Lists up to 10 recent public games for a Chess.com username.
- Analyzes recent rapid/blitz games with Stockfish (0.05–0.5 s per position).
- Identifies high-evaluation-loss decisions on moves **5–15**.
- Returns FENs, best moves, candidate variations, and a practical training plan.
- Analyzes a PGN supplied directly in chat.

## Local Docker

```bash
docker build -t chess-coach .
docker run --rm -p 8080:8080 chess-coach
curl http://localhost:8080/health
```

**Security:** The `/mcp` endpoint is intentionally disabled unless `CHESS_COACH_API_KEY` is configured. The health endpoint remains public.

## Railway deployment

1. Connect Railway to this repository: **darshpaul/ChessPlugIn**.
2. Choose **Deploy from GitHub Repo** and select this repository. Railway will use the Dockerfile.
3. Add the service environment variable `CHESS_COACH_API_KEY` with a long random value. Do **not** put this value in GitHub, in a chat, or in a query parameter.
4. Generate a public HTTPS domain in Railway Networking. Railway supplies `PORT`; the Dockerfile listens on it.
5. Test `https://YOUR-DOMAIN/health` (should return status ok).
6. MCP URL: `https://YOUR-DOMAIN/mcp`.

Important: This build uses a simple `Authorization: Bearer <key>` gate, **not OAuth**. Some ChatGPT custom MCP connection flows do not permit custom bearer authentication. Do not remove authentication to solve this; first confirm whether your connector supports custom headers. If it does not, replace the gate with an appropriate OAuth authorization server or a trusted access proxy before enabling the connector.

## Using the tool in ChatGPT

When a connection method compatible with the security setup is available, connect the URL ending in `/mcp`. Available custom connector controls depend on account/workspace.

Try: *Analyze the last 3 rapid games for DarshPaul, especially moves 5–15. Show my critical mistakes, opposing tactical responses, and three concrete drills.*

The server provides Stockfish data; ChatGPT generates the natural-language explanations.

## Caveats

- Uses Chess.com's public archives; no Chess.com password or API key required.
- Short Stockfish searches are approximate. A loss above 90 centipawns is a review heuristic.
- Searches the latest 12 monthly archives, up to 10 analyzed games.
- No OAuth, job queue, or persistent result caching yet. Long analysis requests may time out on low-cost hosting.
- This repository's code has not yet been verified end-to-end on Railway. Test before relying on it.

## API paths

- GET `/health`: public service check.
- MCP Streamable HTTP `/mcp`: access-controlled MCP tools (`list_recent_games`, `analyze_recent_games`, `analyze_pgn`).
