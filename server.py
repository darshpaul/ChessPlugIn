from __future__ import annotations
import asyncio
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import urlparse
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from coach import CoachError, fetch_recent_games, summarize_game, analyze_records, analyze_pasted_pgn, validate_username
from board import BoardArrow, BoardHighlight, BoardHistory, BoardVariation, prepare_board, position_analysis
from auth import BASE, router as oauth_router, challenge, verify_access

# FastMCP's default localhost allowlist rejects authenticated Railway requests
# with 421, before initialize/tools/list can run. Keep host/origin checks enabled.
mcp = FastMCP(
    "Chess Coach", stateless_http=True, json_response=True, streamable_http_path="/mcp",
    transport_security=TransportSecuritySettings(
        allowed_hosts=[urlparse(BASE).netloc, "127.0.0.1:*", "localhost:*", "[::1]:*"],
        allowed_origins=[BASE, "https://chatgpt.com", "http://127.0.0.1:*", "http://localhost:*"],
    ),
)
TOOL_AUTH={"securitySchemes":[{"type":"oauth2","scopes":["chess:analyze"]}]}
READ_ONLY=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)
LOCAL_ANALYSIS=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
gate = asyncio.Semaphore(1)
BOARD_URI = "ui://chess-coach/board-v1.html"
BOARD_HTML = Path(__file__).parent / "web" / "dist" / "board.html"
BOARD_META = {
    "ui": {"prefersBorder": True, "csp": {"connectDomains": [], "resourceDomains": []}},
    "openai/widgetDescription": "Interactive chess analysis board with legal moves, replay, colored arrows, highlighted squares and Stockfish lines.",
    "openai/widgetPrefersBorder": True,
    "openai/widgetCSP": {"connect_domains": [], "resource_domains": []},
    "openai/ui": {"availableDisplayModes": ["inline", "fullscreen"], "preferredDisplayMode": "inline"},
}


def tool_error(error: Exception) -> CallToolResult:
    return CallToolResult(isError=True, content=[TextContent(type="text", text=str(error))],
                          structuredContent={"error": str(error)})


@mcp.resource(BOARD_URI, name="chess_analysis_board", title="Chess analysis board",
              mime_type="text/html;profile=mcp-app", meta=BOARD_META)
def chess_board_resource() -> str:
    return BOARD_HTML.read_text(encoding="utf-8")


@mcp.tool(title="Chess analysis board", annotations=LOCAL_ANALYSIS,
          meta={**TOOL_AUTH, "ui": {"resourceUri": BOARD_URI, "visibility": ["model", "app"]},
                "openai/outputTemplate": BOARD_URI, "openai/widgetAccessible": True,
                "openai/ui": {"entrypoints": [{"type": "thread"}]},
                "openai/toolInvocation/invoking": "Opening the chessboard…",
                "openai/toolInvocation/invoked": "Chessboard ready"})
async def show_chess_board(
    fen: str = "", pgn: str = "", ply: int = 0,
    orientation: Literal["white", "black"] = "white", title: str = "Analysis board",
    note: str = "", arrows: list[BoardArrow] | None = None,
    highlights: list[BoardHighlight] | None = None,
    variations: list[BoardVariation] | None = None,
) -> CallToolResult:
    """Show an interactive chessboard in chat. Accept one FEN or PGN; no input opens the start position.
    For a real game first call get_board_games, then pass its PGN. ply is the number of
    half-moves already played (before White move 37 use 72). Draw coaching arrows with
    from_square/to_square/color, highlight squares, add a note and legal SAN/UCI variations.
    A variation starts at the selected ply unless at_ply is specified. Users can move pieces,
    replay, draw, and share the current position. In an expanded app prefer its WebMCP tools
    to update the existing board; otherwise call this tool with the updated FEN and annotations.
    This tool renders prepared data; use analyze_board_position for Stockfish evaluations.
    """
    try:
        payload = prepare_board(fen=fen, pgn=pgn, ply=ply, orientation=orientation,
                                title=title, note=note, arrows=arrows,
                                highlights=highlights, variations=variations)
    except (CoachError, ValueError) as exc:
        return tool_error(exc)
    summary = {"title": title, "fen": payload["frames"][ply]["fen"], "ply": ply,
               "total_plies": len(payload["frames"]) - 1, "orientation": orientation,
               "note": note, "arrows": payload["arrows"], "highlights": payload["highlights"],
               "variations": payload["variations"]}
    return CallToolResult(content=[TextContent(type="text", text=f"{title}: {summary['fen']}\n{note}")],
                          structuredContent=summary, _meta={"board": payload})


@mcp.tool(title="Fetch games for the board", annotations=READ_ONLY,
          meta={**TOOL_AUTH, "ui": {"visibility": ["model", "app"]}, "openai/widgetAccessible": True})
async def get_board_games(username: str, count: int = 3, time_class: str = "rapid") -> CallToolResult:
    """Fetch recent public Chess.com PGNs for replay. Pass one returned PGN to show_chess_board.
    Returns game data only; does not open a new board. The board UI can use this to switch games.
    """
    try:
        games = await asyncio.to_thread(fetch_recent_games, username, count, time_class)
        data = {"username": username, "games": [
            {**summarize_game(g, username), "pgn": g["pgn"],
             "white": g["white"]["username"], "black": g["black"]["username"]} for g in games]}
        return CallToolResult(content=[TextContent(type="text", text=f"Found {len(games)} games for {username}.")],
                              structuredContent=data)
    except (CoachError, ValueError, KeyError) as exc:
        return tool_error(exc)


@mcp.tool(title="Analyze board with Stockfish", annotations=LOCAL_ANALYSIS,
          meta={**TOOL_AUTH, "ui": {"visibility": ["model", "app"]}, "openai/widgetAccessible": True})
async def analyze_board_position(fen: str, seconds: float = 3, lines: int = 3,
                                 history: BoardHistory | None = None) -> CallToolResult:
    """Analyze the current board with real Stockfish (0.1-8 seconds, 1-3 candidate lines).
    Send history.initial_fen and history.moves when available to preserve repetition context.
    Scores are from White's perspective: positive favors White. mate is a signed move count,
    never a pawn score. Finite-search evaluations are estimates. Does not render a new widget.
    """
    try:
        async with gate:
            data = await asyncio.to_thread(position_analysis, fen, seconds, lines, history)
        return CallToolResult(content=[TextContent(type="text", text="Stockfish position analysis; scores favor White when positive.")],
                              structuredContent=data)
    except (CoachError, ValueError, OSError) as exc:
        return tool_error(exc)

@mcp.tool(meta=TOOL_AUTH, annotations=READ_ONLY)
async def list_recent_games(username: str, count: int = 5, time_class: str = "rapid") -> dict:
    """List public Chess.com games by username with ratings and links."""
    try:
        games = await asyncio.to_thread(fetch_recent_games, username, count, time_class)
        return {"username": username, "games": [summarize_game(g, username) for g in games]}
    except CoachError as e:
        return {"error": str(e)}

@mcp.tool(meta=TOOL_AUTH, annotations=READ_ONLY)
async def analyze_recent_games(username: str, count: int = 5, time_class: str = "rapid", seconds_per_position: float = .15) -> dict:
    """Fetch recent games, run Stockfish, highlight opening mistakes (moves 5-15), and suggest training priorities."""
    try:
        validate_username(username)
        async with gate:
            games = await asyncio.to_thread(fetch_recent_games, username, count, time_class)
            return await asyncio.to_thread(analyze_records, username, games, seconds_per_position)
    except (CoachError, OSError) as e:
        return {"error": str(e)}

@mcp.tool(meta=TOOL_AUTH, annotations=LOCAL_ANALYSIS)
async def analyze_pgn(username: str, pgn: str, seconds_per_position: float = .15) -> dict:
    """Run Stockfish analysis on one PGN and generate individualized coaching notes."""
    try:
        async with gate:
            return await asyncio.to_thread(analyze_pasted_pgn, pgn, username, seconds_per_position)
    except (CoachError, OSError) as e:
        return {"error": str(e)}

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp.session_manager.run():
        yield

app = FastAPI(title="Chess Coach", lifespan=lifespan, docs_url=None, redoc_url=None)
app.include_router(oauth_router)

@app.get("/health")
async def health():
    return {"status": "ok", "mcp_endpoint": "/mcp", "auth": "oauth",
            "board_available": BOARD_HTML.is_file(), "board_resource": BOARD_URI}


@app.get("/board", response_class=HTMLResponse)
async def board_preview():
    # Public static UI only. Games and Stockfish remain behind the OAuth MCP endpoint.
    return HTMLResponse(chess_board_resource(), headers={
        "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    })

@app.middleware("http")
async def protect(request: Request, call_next):
    if request.url.path.startswith("/mcp"):
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer ") or not verify_access(authorization[7:]):
            return challenge()
    return await call_next(request)

app.mount("/", mcp.streamable_http_app())
