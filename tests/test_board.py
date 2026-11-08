import asyncio
import shutil

import chess
import pytest

import auth
import server
from board import BoardArrow, BoardHighlight, BoardHistory, BoardVariation, position_analysis, prepare_board
from coach import CoachError
from conftest import CLIENT
from test_oauth import rpc

MATE_FEN = "r4r1k/1p1b1ppB/3P1q2/pp6/8/6Q1/n1P5/3K3R w - - 4 37"
MATE_LINE = ["Bg6+", "Kg8", "Rh8+", "Kxh8", "Qh2+", "Bh3", "Qxh3+", "Qh4", "Qxh4+", "Kg8", "Qh7#"]
PGN = '[White "Player"]\n[Black "Opponent"]\n[Result "*"]\n\n1. e4 {[%clk 0:09:59]} e5 2. Nf3 Nc6 *'


def test_replay_and_coach_variations_are_legal_and_original_preserved():
    data = prepare_board(pgn=PGN, ply=2, orientation="black",
                         arrows=[BoardArrow(from_square="g1", to_square="f3")],
                         highlights=[BoardHighlight(square="e4")],
                         variations=[BoardVariation(moves=["Bc4", "Nf6"])])
    assert len(data["frames"]) == 5
    assert data["frames"][1]["clock"] == 599
    assert data["frames"][2]["label"] == "1...e5"
    assert data["frames"][3]["uci"] == "g1f3"
    assert data["variations"][0]["moves"] == ["f1c4", "g8f6"]
    assert data["variations"][0]["at_ply"] == 2
    assert data["orientation"] == "black"


def test_mate_variation_and_pgn_variation_import():
    data = prepare_board(fen=MATE_FEN, variations=[BoardVariation(moves=MATE_LINE)])
    board = chess.Board(MATE_FEN)
    for uci in data["variations"][0]["moves"]:
        board.push_uci(uci)
    assert board.is_checkmate()
    pgn = prepare_board(pgn='1. e4 (1. d4 d5) e5 2. Nf3 *')
    assert pgn["variations"][0]["at_ply"] == 0
    assert pgn["variations"][0]["moves"] == ["d2d4", "d7d5"]


@pytest.mark.parametrize("args", [
    {"fen": "not a fen"}, {"fen": "8/8/8/8/8/8/8/8 w - - 0 1"},
    {"pgn": PGN, "fen": chess.STARTING_FEN}, {"pgn": "1. e5 *"},
    {"pgn": PGN, "ply": 100}, {"pgn": PGN + "\n\n" + PGN},
    {"variations": [BoardVariation(moves=["e5"])]},
    {"variations": [BoardVariation(moves=["--"])]},
    {"orientation": "sideways"}, {"note": "x" * 4001},
])
def test_invalid_inputs_do_not_become_board_states(args):
    with pytest.raises(CoachError):
        prepare_board(**args)


def test_resource_and_render_tool_are_discoverable_and_authenticated(client):
    token = auth.encode("access", CLIENT, 3600)
    listing = rpc(client, token, "tools/list").json()["result"]["tools"]
    show = next(t for t in listing if t["name"] == "show_chess_board")
    assert show["_meta"]["ui"]["resourceUri"] == server.BOARD_URI
    assert show["_meta"]["openai/outputTemplate"] == server.BOARD_URI
    for tool in listing:
        if tool["name"] != "show_chess_board":
            assert "resourceUri" not in tool["_meta"].get("ui", {})
    resource = rpc(client, token, "resources/read", {"uri": server.BOARD_URI}).json()["result"]["contents"][0]
    assert resource["mimeType"] == "text/html;profile=mcp-app"
    assert resource["_meta"]["ui"]["csp"]["connectDomains"] == []
    assert "<!doctype html>" in resource["text"]
    assert "react-chessboard" in resource["text"]
    result = rpc(client, token, "tools/call", {"name": "show_chess_board", "arguments": {"fen": MATE_FEN}}).json()["result"]
    assert not result["isError"]
    assert result["structuredContent"]["fen"] == MATE_FEN
    assert result["_meta"]["board"]["frames"][0]["fen"] == MATE_FEN
    assert rpc(client, "", "resources/read", {"uri": server.BOARD_URI}).status_code == 401
    bad = rpc(client, token, "tools/call", {"name": "show_chess_board", "arguments": {"fen": "wrong"}}).json()["result"]
    assert bad["isError"]


def test_static_preview_does_not_expose_user_or_engine_data(client):
    response = client.get("/board")
    assert response.status_code == 200
    assert "connect-src 'none'" in response.headers["content-security-policy"]
    assert "test-only-password" not in response.text
    assert 'src="http' not in response.text
    assert client.get("/health").json()["board_available"]


def test_game_data_remains_separate_from_rendering(monkeypatch):
    monkeypatch.setattr(server, "fetch_recent_games", lambda *args: [{
        "pgn": PGN, "url": "https://www.chess.com/game/live/1", "end_time": 1,
        "time_class": "rapid", "white": {"username": "Player", "rating": 800, "result": "win"},
        "black": {"username": "Opponent", "rating": 800, "result": "checkmated"},
    }])
    result = asyncio.run(server.get_board_games("Player", 1))
    assert result.structuredContent["games"][0]["pgn"] == PGN
    assert result.meta is None


@pytest.fixture
def stockfish(monkeypatch):
    path = shutil.which("stockfish") or ("/usr/games/stockfish" if __import__("os").path.isfile("/usr/games/stockfish") else None)
    if not path:
        pytest.skip("Install Stockfish for engine integration tests")
    monkeypatch.setenv("STOCKFISH_PATH", path)


def test_real_stockfish_finds_forced_mate_and_keeps_mate_separate(stockfish):
    result = position_analysis(MATE_FEN, seconds=.25, lines=1)
    best = result["lines"][0]
    assert "Stockfish" in result["engine"]
    assert best["mate"] == 6 and best["score_cp"] is None
    assert best["uci"][0] == "h7g6"
    board = chess.Board(MATE_FEN)
    for move in best["uci"]:
        board.push_uci(move)
    assert board.is_checkmate()


def test_history_is_checked_and_repetition_survives(stockfish):
    moves = ["g1f3", "g8f6", "f3g1", "f6g8"] * 2
    board = chess.Board()
    for uci in moves:
        board.push_uci(uci)
    history = BoardHistory(initial_fen=chess.STARTING_FEN, moves=moves)
    assert position_analysis(board.fen(), .1, 1, history)["can_claim_draw"]
    with pytest.raises(CoachError, match="history does not match"):
        position_analysis(MATE_FEN, .1, 1, history)
    for seconds in [float("nan"), float("inf"), 9, 0]:
        with pytest.raises(CoachError):
            position_analysis(MATE_FEN, seconds)


def test_terminal_position_does_not_launch_engine(monkeypatch):
    monkeypatch.setenv("STOCKFISH_PATH", "/not/an/engine")
    board = chess.Board(MATE_FEN)
    for move in MATE_LINE:
        board.push_san(move)
    result = position_analysis(board.fen())
    assert result["terminal"] and result["result"] == "1-0"
    assert result["reason"] == "checkmate"
