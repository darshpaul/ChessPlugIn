import { Chess, type Square } from "chess.js";

export const COLORS = { green: "#2f9e69", red: "#e55353", blue: "#4889db", orange: "#e4a327" };
export type Color = keyof typeof COLORS;
export type MarkArrow = { from_square: string; to_square: string; color: Color };
export type Highlight = { square: string; color: Color };
export type Marks = { arrows: MarkArrow[]; highlights: Highlight[] };
export type Frame = { fen: string; san: string; uci: string; label: string; comment: string; clock?: number | null };
export type Variation = { id: string; label: string; at_ply: number; moves: string[]; note: string };
export type BoardData = {
  id: string; schema_version: number; title: string; note: string; headers: Record<string, string>;
  frames: Frame[]; ply: number; orientation: "white" | "black";
  variations: Variation[]; arrows: MarkArrow[]; highlights: Highlight[];
};
export type EngineLine = { score_cp: number | null; mate: number | null; depth: number; san: string[]; uci: string[] };
export type Analysis = { fen: string; engine?: string; lines: EngineLine[]; terminal?: boolean; result?: string; reason?: string; can_claim_draw?: boolean };
export type BoardGame = { pgn: string; url: string; white: string; black: string; color: "white" | "black"; result: string; date: string };

export function moveFrame(chess: Chess, text: string): Frame {
  const before = chess.fen().split(" ");
  const move = /^[a-h][1-8][a-h][1-8][qrbn]?$/.test(text)
    ? chess.move({ from: text.slice(0, 2), to: text.slice(2, 4), promotion: text[4] })
    : chess.move(text);
  return { fen: chess.fen(), san: move.san, uci: move.from + move.to + (move.promotion || ""),
    label: `${before[5]}${before[1] === "w" ? "." : "..."}${move.san}`, comment: "" };
}

export function localBoard(input = "", orientation: "white" | "black" = "white"): BoardData {
  if (input.length > 25000) throw new Error("Please load one game of at most 25,000 characters.");
  const chess = new Chess();
  let frames: Frame[];
  let headers: Record<string, string> = {};
  if (input.trim() && input.trim().split(/\s+/)[0].split("/").length === 8) {
    chess.load(input.trim());
    frames = [{ fen: chess.fen(), san: "", uci: "", label: "Start", comment: "" }];
  } else if (input.trim()) {
    chess.loadPgn(input.trim());
    headers = chess.getHeaders();
    const history = chess.history({ verbose: true });
    if (history.length > 1000) throw new Error("This game is too long.");
    const comments = new Map(chess.getComments().map(c => [c.fen, c.comment]));
    const initial = history[0]?.before || chess.fen();
    frames = [{ fen: initial, san: "", uci: "", label: "Start", comment: comments.get(initial) || "" }];
    for (const m of history) {
      const parts = m.before.split(" ");
      frames.push({ fen: m.after, san: m.san, uci: m.from + m.to + (m.promotion || ""),
        label: `${parts[5]}${parts[1] === "w" ? "." : "..."}${m.san}`, comment: comments.get(m.after) || "" });
    }
  } else {
    frames = [{ fen: chess.fen(), san: "", uci: "", label: "Start", comment: "" }];
  }
  let hash = 2166136261;
  for (const c of input) hash = Math.imul(hash ^ c.charCodeAt(0), 16777619);
  return { id: `local-${hash >>> 0}-${orientation}`, schema_version: 1,
    title: headers.White && headers.Black ? `${headers.White} vs ${headers.Black}` : "Analysis board",
    note: "", headers, frames, ply: 0, orientation, variations: [], arrows: [], highlights: [] };
}

export function variationFrames(frames: Frame[], at: number, moves: string[]): Frame[] {
  if (!Number.isInteger(at) || at < 0 || at >= frames.length || moves.length > 100) throw new Error("Invalid variation.");
  const result = frames.slice(0, at + 1);
  const chess = chessAt(result, at);
  for (const move of moves) result.push(moveFrame(chess, move));
  return result;
}

export function chessAt(frames: Frame[], ply: number): Chess {
  const chess = new Chess(frames[0].fen);
  for (const frame of frames.slice(1, ply + 1)) {
    moveFrame(chess, frame.uci);
    if (chess.fen() !== new Chess(frame.fen).fen()) throw new Error("A position does not match its recorded moves.");
  }
  return chess;
}

export function checkBoard(value: unknown): BoardData {
  const data = value as BoardData;
  if (!data || data.schema_version !== 1 || !Array.isArray(data.frames) || !data.frames.length || data.frames.length > 1001) throw new Error("The board data could not be loaded.");
  if (!Number.isInteger(data.ply) || data.ply < 0 || data.ply >= data.frames.length) throw new Error("Invalid starting move.");
  const replay = chessAt(data.frames, data.frames.length - 1);
  if (replay.fen() !== new Chess(data.frames.at(-1)!.fen).fen()) throw new Error("The game position does not match its moves.");
  if (data.orientation !== "white" && data.orientation !== "black") throw new Error("Invalid board orientation.");
  checkMarks(data);
  if (data.variations.length > 40) throw new Error("Too many variations.");
  for (const v of data.variations) variationFrames(data.frames, v.at_ply, v.moves);
  return data;
}

export function checkMarks(value: { arrows?: MarkArrow[]; highlights?: Highlight[] }): Marks {
  const arrows = value.arrows || [], highlights = value.highlights || [];
  const square = (s: string) => typeof s === "string" && /^[a-h][1-8]$/.test(s);
  if (arrows.length > 64 || highlights.length > 64 ||
      arrows.some(a => !square(a.from_square) || !square(a.to_square) || !Object.hasOwn(COLORS, a.color)) ||
      highlights.some(h => !square(h.square) || !Object.hasOwn(COLORS, h.color))) throw new Error("Invalid board markings.");
  return { arrows, highlights };
}

export function scoreLabel(line: EngineLine): string {
  if (line.mate !== null) return `${line.mate > 0 ? "White" : "Black"} mates in ${Math.abs(line.mate)}`;
  const score = (line.score_cp || 0) / 100;
  return `${score >= 0 ? "+" : ""}${score.toFixed(2)}`;
}

export function clockLabel(clock?: number | null): string {
  if (clock == null) return "";
  return `${Math.floor(clock / 60)}:${Math.floor(clock % 60).toString().padStart(2, "0")}`;
}

export function destinations(chess: Chess, square: string): string[] {
  return chess.moves({ square: square as Square, verbose: true }).map(m => m.to);
}

export function sampleBoard(): BoardData {
  const data = localBoard("r4r1k/1p1b1ppB/3P1q2/pp6/8/6Q1/n1P5/3K3R w - - 4 37");
  data.id = "sample-mate-six";
  data.title = "Find the mating attack";
  data.note = "37.Bg6+! blocks the queen's defense and controls h7. Follow the coach's line to see the rook sacrifice.";
  data.arrows = [{ from_square: "h7", to_square: "g6", color: "green" }];
  data.highlights = [{ square: "h8", color: "red" }];
  data.variations = [{ id: "mate", label: "Mate in six", at_ply: 0,
    moves: ["Bg6+", "Kg8", "Rh8+", "Kxh8", "Qh2+", "Bh3", "Qxh3+", "Qh4", "Qxh4+", "Kg8", "Qh7#"],
    note: "Keep checking. The bishop on g6 covers h7 for the final mate." }];
  return data;
}
