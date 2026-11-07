from __future__ import annotations
import asyncio, os, hmac
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from mcp.server.fastmcp import FastMCP
from coach import CoachError,fetch_recent_games,summarize_game,analyze_records,analyze_pasted_pgn,validate_username
mcp=FastMCP("Chess Coach",stateless_http=True,json_response=True,streamable_http_path="/")
gate=asyncio.Semaphore(1)
@mcp.tool()
async def list_recent_games(username:str,count:int=5,time_class:str="rapid")->dict:
    """List public Chess.com games by username with ratings and links."""
    try:
        games=await asyncio.to_thread(fetch_recent_games,username,count,time_class)
        return {"username":username,"games":[summarize_game(g,username) for g in games]}
    except CoachError as e:return {"error":str(e)}
@mcp.tool()
async def analyze_recent_games(username:str,count:int=5,time_class:str="rapid",seconds_per_position:float=.15)->dict:
    """Fetch recent games, run Stockfish, flag opening errors (moves 5-15), and provide critical FEN positions and training recommendations."""
    try:
        validate_username(username)
        async with gate:
            games=await asyncio.to_thread(fetch_recent_games,username,count,time_class)
            return await asyncio.to_thread(analyze_records,username,games,seconds_per_position)
    except (CoachError,OSError) as e:return {"error":str(e)}
@mcp.tool()
async def analyze_pgn(username:str,pgn:str,seconds_per_position:float=.15)->dict:
    """Stockfish analyze a single PGN for a specific player."""
    try:
        async with gate:return await asyncio.to_thread(analyze_pasted_pgn,pgn,username,seconds_per_position)
    except (CoachError,OSError) as e:return {"error":str(e)}
@asynccontextmanager
async def lifespan(app:FastAPI):
    async with mcp.session_manager.run():yield
app=FastAPI(title="Chess Coach",lifespan=lifespan,docs_url=None,redoc_url=None)
@app.get("/health")
async def health():return {"status":"ok","mcp_endpoint":"/mcp"}
@app.middleware("http")
async def protect(request:Request,call_next):
    secret=os.environ.get("CHESS_COACH_API_KEY","")
    if request.url.path.startswith("/mcp") and not secret:
        return JSONResponse({"error":"MCP disabled until CHESS_COACH_API_KEY is set"},status_code=503)
    if request.url.path.startswith("/mcp") and not hmac.compare_digest(request.headers.get("Authorization",""),"Bearer "+secret):
        return JSONResponse({"error":"Unauthorized"},status_code=401)
    return await call_next(request)
app.mount("/mcp",mcp.streamable_http_app())
