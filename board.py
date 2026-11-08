"""Validated, stateless analysis-board data and bounded Stockfish searches."""
from __future__ import annotations

import hashlib
import json
import math
from io import StringIO
from typing import Literal

import chess
import chess.engine
import chess.pgn
from pydantic import BaseModel, Field

from coach import CoachError, engine_path

Color = Literal["green", "red", "blue", "orange"]
Square = str
MAX_PLIES = 1000


class BoardArrow(BaseModel):
    from_square: Square = Field(pattern=r"^[a-h][1-8]$")
    to_square: Square = Field(pattern=r"^[a-h][1-8]$")
    color: Color = "green"


class BoardHighlight(BaseModel):
    square: Square = Field(pattern=r"^[a-h][1-8]$")
    color: Color = "orange"


class BoardVariation(BaseModel):
    label: str = Field(default="Coach's line", max_length=100)
    moves: list[str] = Field(min_length=1, max_length=100)
    at_ply: int | None = Field(default=None, ge=0, le=MAX_PLIES)
    note: str = Field(default="", max_length=2000)


class BoardHistory(BaseModel):
    initial_fen: str = Field(max_length=150)
    moves: list[str] = Field(max_length=MAX_PLIES)


def valid_board(fen: str) -> chess.Board:
    if len(fen) > 150:
        raise CoachError("FEN is too long.")
    try:
        board = chess.Board(fen)
    except ValueError as exc:
        raise CoachError("Invalid FEN. Paste a complete chess position.") from exc
    if not board.is_valid():
        raise CoachError("This is not a valid standard chess position.")
    return board


def move_from_text(board: chess.Board, text: str) -> chess.Move:
    try:
        # python-chess accepts SAN and coordinate notation, including promotions.
        move = board.parse_san(text)
        if not move or move not in board.legal_moves:
            raise ValueError("Not a legal move")
        return move
    except ValueError as exc:
        raise CoachError(f"Illegal move in line: {text[:30]}") from exc


def frame(board: chess.Board, move: chess.Move | None = None,
          comment: str = "", clock: float | None = None) -> dict:
    if move is None:
        return {"fen": board.fen(), "san": "", "uci": "", "label": "Start",
                "comment": comment[:2000], "clock": clock}
    san = board.san(move)
    label = f"{board.fullmove_number}{'.' if board.turn else '...'}{san}"
    board.push(move)
    return {"fen": board.fen(), "san": san, "uci": move.uci(), "label": label,
            "comment": comment[:2000], "clock": clock}


def prepare_board(*, fen: str = "", pgn: str = "", ply: int = 0,
                  orientation: str = "white", title: str = "Analysis board",
                  note: str = "", arrows: list[BoardArrow] | None = None,
                  highlights: list[BoardHighlight] | None = None,
                  variations: list[BoardVariation] | None = None) -> dict:
    if fen and pgn:
        raise CoachError("Supply either a PGN or a FEN, not both.")
    if len(pgn) > 25000 or len(note) > 4000 or len(title) > 160:
        raise CoachError("PGN, title or note exceeds the board size limit.")
    if orientation not in ("white", "black"):
        raise CoachError("Orientation must be white or black.")
    if len(arrows or []) > 64 or len(highlights or []) > 64 or len(variations or []) > 20:
        raise CoachError("Too many board annotations or variations.")

    headers, lines = {}, []
    if pgn:
        stream = StringIO(pgn)
        try:
            game = chess.pgn.read_game(stream)
            if game is None or game.errors or chess.pgn.read_game(stream) is not None:
                raise ValueError("Expected one legal game")
            board = game.board()
        except (ValueError, KeyError) as exc:
            raise CoachError("Invalid PGN. Load one standard chess game at a time.") from exc
        if type(board) is not chess.Board or board.chess960 or not board.is_valid():
            raise CoachError("The board currently supports standard chess games.")
        headers = dict(game.headers)
        frames = [frame(board, comment=game.comment)]
        nodes = [game, *game.mainline()]
        if len(nodes) > MAX_PLIES + 1:
            raise CoachError("The game is too long.")
        for node in nodes[1:]:
            if node.move not in board.legal_moves:
                raise CoachError("The PGN contains an illegal move.")
            frames.append(frame(board, node.move, node.comment, node.clock()))
        for index, node in enumerate(nodes):
            for alternative in node.variations[1:]:
                moves, cursor = [], alternative
                while cursor and len(moves) < 100:
                    moves.append(cursor.move.uci())
                    cursor = cursor.variations[0] if cursor.variations else None
                if len(lines) < 20:
                    lines.append({"id": f"pgn-{len(lines)}", "at_ply": index,
                                  "label": f"Alternative {len(lines) + 1}",
                                  "moves": moves, "note": alternative.comment[:2000]})
    else:
        board = valid_board(fen or chess.STARTING_FEN)
        frames = [frame(board)]

    if type(ply) is not int or not 0 <= ply < len(frames):
        raise CoachError(f"Ply must be between 0 and {len(frames) - 1} (half-moves from the start).")
    for variation in variations or []:
        start = ply if variation.at_ply is None else variation.at_ply
        if start >= len(frames):
            raise CoachError("Variation starts outside the game.")
        b = chess.Board(frames[start]["fen"])
        moves = []
        for text in variation.moves:
            move = move_from_text(b, text)
            moves.append(move.uci())
            b.push(move)
        lines.append({"id": f"coach-{len(lines)}", "at_ply": start,
                      "label": variation.label, "moves": moves, "note": variation.note})

    payload = {
        "schema_version": 1, "title": title, "note": note,
        "headers": headers, "frames": frames, "ply": ply,
        "orientation": orientation, "variations": lines,
        "arrows": [a.model_dump() for a in arrows or []],
        "highlights": [h.model_dump() for h in highlights or []],
    }
    # A stable, content-derived id prevents restoring a different game's UI state.
    payload["id"] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:24]
    return payload


def position_analysis(fen: str, seconds: float = 3, lines: int = 3,
                      history: BoardHistory | None = None) -> dict:
    if not math.isfinite(seconds) or not .1 <= seconds <= 8:
        raise CoachError("Search time must be between 0.1 and 8 seconds.")
    if type(lines) is not int or not 1 <= lines <= 3:
        raise CoachError("Request 1-3 engine lines.")
    requested = valid_board(fen)
    board = requested
    if history is not None:
        board = valid_board(history.initial_fen)
        for text in history.moves:
            board.push(move_from_text(board, text))
        if board.fen() != requested.fen():
            raise CoachError("Move history does not match the requested position.")
    outcome = board.outcome()
    if outcome:
        return {"fen": board.fen(), "terminal": True, "result": outcome.result(),
                "reason": outcome.termination.name.lower(), "lines": [],
                "score_perspective": "white"}
    engine = chess.engine.SimpleEngine.popen_uci(engine_path(), timeout=15)
    try:
        engine.configure({"Threads": 1, "Hash": 64})
        infos = engine.analyse(board, chess.engine.Limit(time=seconds),
                               multipv=min(lines, board.legal_moves.count()))
        choices = []
        for info in infos:
            score = info["score"].white()
            b, sans, ucis = board.copy(), [], []
            for move in info.get("pv", [])[:20]:
                sans.append(b.san(move))
                ucis.append(move.uci())
                b.push(move)
            choices.append({"score_cp": score.score(), "mate": score.mate(),
                            "depth": info.get("depth"), "san": sans, "uci": ucis})
        return {"fen": board.fen(), "engine": engine.id.get("name", "Stockfish"),
                "seconds": seconds, "lines": choices, "score_perspective": "white",
                "can_claim_draw": board.can_claim_draw(),
                "history_supplied": history is not None,
                "caveat": "Finite searches are estimates. Mate scores are separate from pawn evaluations."}
    finally:
        engine.quit()
