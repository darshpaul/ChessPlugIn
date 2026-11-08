import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Chess, type Square } from "chess.js";
import { Chessboard, defaultArrowOptions } from "react-chessboard";
import { askCoach, callTool, subscribeBoard, subscribeStatus, updateContext, type HostState } from "./bridge";
import { COLORS, checkBoard, checkMarks, chessAt, clockLabel, destinations, localBoard, moveFrame, sampleBoard, scoreLabel, variationFrames,
  type Analysis, type BoardData, type BoardGame, type Color, type Frame, type Marks, type Variation } from "./model";

type Line = { id: string; label: string; frames: Frame[]; at: number; note: string };
const EMPTY: Marks = { arrows: [], highlights: [] };
const squarePattern = { type: "string", pattern: "^[a-h][1-8]$" };
const colorSchema = { type: "string", enum: Object.keys(COLORS) };

function BoardApp() {
  const [data, setData] = useState<BoardData>(() => localBoard());
  const [hydrated, setHydrated] = useState(false);
  const [ply, setPly] = useState(0);
  const [active, setActive] = useState<Line | null>(null);
  const [orientation, setOrientation] = useState<"white" | "black">("white");
  const [annotations, setAnnotations] = useState<Record<string, Marks>>({});
  const [note, setNote] = useState("");
  const [mode, setMode] = useState<"move" | "arrow" | "square">("move");
  const [color, setColor] = useState<Color>("green");
  const [selected, setSelected] = useState<string | null>(null);
  const [arrowStart, setArrowStart] = useState<string | null>(null);
  const [promotion, setPromotion] = useState<{ from: string; to: string } | null>(null);
  const [playing, setPlaying] = useState(false);
  const [host, setHost] = useState<HostState>("connecting");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [seconds, setSeconds] = useState(3);
  const [input, setInput] = useState("");
  const [username, setUsername] = useState("");
  const [games, setGames] = useState<BoardGame[]>([]);
  const [question, setQuestion] = useState("");
  const [tab, setTab] = useState<"moves" | "lines" | "load">("moves");
  const rightStart = useRef<{ square: string; color: Color } | null>(null);
  const requestId = useRef(0);
  const latest = useRef<any>(null);
  const frames = active?.frames || data.frames;
  const current = frames[ply];
  const chess = useMemo(() => chessAt(frames, ply), [frames, ply]);
  const markKey = `${active?.id || "game"}:${ply}:${current.fen}`;
  const marks = annotations[markKey] || EMPTY;
  const canUseHost = host === "connected";
  const side = chess.turn() === "w" ? "White" : "Black";
  const status = chess.isCheckmate() ? `${side} is checkmated` : chess.isStalemate() ? "Stalemate"
    : chess.isDraw() ? "Drawn position" : `${side} to move${chess.isCheck() ? " · Check" : ""}`;
  const validTargets = selected ? destinations(chess, selected) : [];
  const currentAnalysis = analysis?.fen === current.fen ? analysis : null;

  function loadBoard(next: BoardData, restore = false) {
    const checked = checkBoard(next);
    ++requestId.current;
    setData(checked); setHydrated(true); setPly(checked.ply); setActive(null); setOrientation(checked.orientation);
    setAnnotations({ [`game:${checked.ply}:${checked.frames[checked.ply].fen}`]: { arrows: checked.arrows, highlights: checked.highlights } });
    setNote(checked.note); setSelected(null); setArrowStart(null); setPromotion(null);
    setAnalysis(null); setPlaying(false); setError(""); setNotice(""); setTab("moves");
    if (restore) {
      try {
        const saved = window.openai?.widgetState as any;
        if (saved?.boardId === checked.id && Number.isInteger(saved.ply)) {
          const restoredFrames = saved.line?.frames || checked.frames;
          if (saved.ply >= 0 && saved.ply < restoredFrames.length && restoredFrames.length <= 1101) {
            chessAt(restoredFrames, restoredFrames.length - 1);
            Object.values(saved.annotations || {}).forEach(m => checkMarks(m as Marks));
            setActive(saved.line || null); setPly(saved.ply); setAnnotations(saved.annotations || {});
            setOrientation(saved.orientation === "black" ? "black" : "white"); setNote(String(saved.note || "").slice(0, 4000));
          }
        }
      } catch { /* An obsolete saved view must not prevent loading the game. */ }
    }
  }

  function navigate(next: number) {
    if (!Number.isInteger(next) || next < 0 || next >= frames.length) throw new Error("That move is outside the current line.");
    setPly(next); setSelected(null); setArrowStart(null); setPromotion(null); setNotice(""); setError("");
  }

  function mark(next: Marks) { setAnnotations(prev => ({ ...prev, [markKey]: checkMarks(next) })); }
  function arrow(from: string, to: string, chosen = color) {
    if (from === to) { highlight(to, chosen); return; }
    const exists = marks.arrows.some(a => a.from_square === from && a.to_square === to && a.color === chosen);
    const rest = marks.arrows.filter(a => !(a.from_square === from && a.to_square === to));
    mark({ ...marks, arrows: exists ? rest : [...rest, { from_square: from, to_square: to, color: chosen }].slice(-64) });
  }
  function highlight(square: string, chosen = color) {
    const exists = marks.highlights.some(h => h.square === square && h.color === chosen);
    const rest = marks.highlights.filter(h => h.square !== square);
    mark({ ...marks, highlights: exists ? rest : [...rest, { square, color: chosen }] });
  }

  function addLine(moves: string[], label: string, lineNote = "") {
    const next = variationFrames(frames, ply, moves);
    const id = `line-${Date.now()}`;
    setActive({ id, label: label.slice(0, 100), frames: next, at: ply, note: lineNote });
    setAnnotations(prev => ({ ...prev, [`${id}:${ply}:${current.fen}`]: marks }));
    setPlaying(false); setSelected(null); setPromotion(null); setError(""); setTab("moves");
    return next;
  }

  function applyMove(from: string, to: string, promote?: string): boolean {
    try {
      const candidates = chess.moves({ verbose: true }).filter(m => m.from === from && m.to === to);
      if (!candidates.length) return false;
      if (candidates.some(m => m.promotion) && !promote) { setPromotion({ from, to }); return false; }
      const copy = chessAt(frames, ply);
      const nextFrame = moveFrame(copy, from + to + (promote || ""));
      setActive({ id: "your-line", label: "Your variation", frames: [...frames.slice(0, ply + 1), nextFrame],
        at: active?.at ?? ply, note: "Explore freely. Back to game restores the recorded moves." });
      setPly(ply + 1); setSelected(null); setPromotion(null); setError(""); setPlaying(false);
      return true;
    } catch { return false; }
  }

  function clickSquare(square: string) {
    setError("");
    if (mode === "square") { highlight(square); return; }
    if (mode === "arrow") {
      if (arrowStart) { arrow(arrowStart, square); setArrowStart(null); } else setArrowStart(square);
      return;
    }
    if (selected && square !== selected && validTargets.includes(square)) { applyMove(selected, square); return; }
    const piece = chess.get(square as Square);
    setSelected(piece?.color === chess.turn() && selected !== square ? square : null);
  }

  function chooseVariation(variation: Variation) {
    const next = variationFrames(data.frames, variation.at_ply, variation.moves);
    setActive({ id: variation.id, label: variation.label, frames: next, at: variation.at_ply, note: variation.note });
    const root = `${variation.at_ply}:${next[variation.at_ply].fen}`;
    setAnnotations(prev => ({ ...prev, [`${variation.id}:${root}`]: prev[`${variation.id}:${root}`] || prev[`game:${root}`] || EMPTY }));
    setPly(variation.at_ply); setSelected(null); setPromotion(null); setPlaying(false); setTab("moves");
  }

  function context() {
    return { app: "Chess Coach board", board_id: data.id, title: data.title, fen: current.fen,
      side_to_move: chess.turn() === "w" ? "white" : "black", ply, move: current.label,
      line: active?.label || "Recorded game", orientation, arrows: marks.arrows,
      highlights: marks.highlights, note, game: data.headers,
      history: { initial_fen: frames[0].fen, moves: frames.slice(1, ply + 1).map(f => f.uci) },
      engine: currentAnalysis,
      available_actions: "Read, navigate, annotate or play a line using this board's WebMCP tools when available. Otherwise use show_chess_board with this FEN and markings." };
  }

  async function run(action: string, fn: () => Promise<void>) {
    setBusy(action); setError(""); setNotice("");
    try { await fn(); } catch (e) { setError(e instanceof Error ? e.message : "Something went wrong. Please try again."); }
    finally { setBusy(""); }
  }

  async function analyze() {
    const id = ++requestId.current;
    await run("engine", async () => {
      const result = await callTool("analyze_board_position", { fen: current.fen, seconds, lines: 3,
        history: { initial_fen: frames[0].fen, moves: frames.slice(1, ply + 1).map(f => f.uci) } });
      if (id !== requestId.current) return;
      setAnalysis(result.structuredContent as unknown as Analysis); setTab("lines");
    });
  }

  async function loadInput() {
    await run("load", async () => {
      if (!input.trim()) throw new Error("Paste a PGN game or FEN position first.");
      if (canUseHost) {
        const isFen = input.trim().split(/\s+/)[0].split("/").length === 8;
        const result = await callTool("show_chess_board", { [isFen ? "fen" : "pgn"]: input.trim(), orientation });
        loadBoard(result._meta?.board as BoardData);
      } else loadBoard(localBoard(input, orientation));
      setInput("");
    });
  }

  latest.current = { context, navigate, mark, addLine, loadBoard, setNote, setOrientation };
  useEffect(() => subscribeStatus(setHost), []);
  useEffect(() => subscribeBoard(next => {
    try { latest.current.loadBoard(next, true); } catch (e) { setError((e as Error).message); }
  }), []);
  useEffect(() => {
    if (!hydrated) return;
    const timer = window.setTimeout(() => updateContext(context(), {
      boardId: data.id, ply, line: active, orientation, annotations, note,
    }), 180);
    return () => window.clearTimeout(timer);
  }, [data, ply, active, orientation, annotations, note, currentAnalysis, host, hydrated]);
  useEffect(() => {
    if (!playing) return;
    if (ply >= frames.length - 1) { setPlaying(false); return; }
    const timer = window.setTimeout(() => navigate(ply + 1), 1000);
    return () => window.clearTimeout(timer);
  }, [playing, ply, frames]);
  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      if ((event.target as HTMLElement)?.matches("input,textarea,select,[contenteditable=true]") || event.altKey || event.ctrlKey || event.metaKey) return;
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault(); setPlaying(false);
        const target = ply + (event.key === "ArrowRight" ? 1 : -1);
        if (target >= 0 && target < frames.length) navigate(target);
      }
      if (event.key === "Escape") { setSelected(null); setArrowStart(null); setPromotion(null); }
    };
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, [ply, frames]);
  useEffect(() => {
    const registry = document.modelContext;
    if (!registry) return;
    const tools = [
      { name: "chess_board_read", description: "Read the live chessboard, exact FEN, move history, arrows, highlights and current Stockfish result.",
        inputSchema: { type: "object", properties: {} }, annotations: { readOnlyHint: true },
        execute: async () => JSON.stringify(latest.current.context()) },
      { name: "chess_board_go_to", description: "Move the live board to a half-move index in its current game or variation.",
        inputSchema: { type: "object", properties: { ply: { type: "integer", minimum: 0 } }, required: ["ply"] },
        execute: async ({ ply }: { ply: number }) => { latest.current.navigate(ply); return JSON.stringify({ displayed_ply: ply }); } },
      { name: "chess_board_annotate", description: "Replace colored arrows/highlights on the current live position and optionally add a coaching note. No chess move is played.",
        inputSchema: { type: "object", properties: {
          arrows: { type: "array", maxItems: 64, items: { type: "object", properties: { from_square: squarePattern, to_square: squarePattern, color: colorSchema }, required: ["from_square", "to_square", "color"] } },
          highlights: { type: "array", maxItems: 64, items: { type: "object", properties: { square: squarePattern, color: colorSchema }, required: ["square", "color"] } },
          note: { type: "string", maxLength: 4000 },
        } }, execute: async (input: Marks & { note?: string }) => {
          latest.current.mark(checkMarks(input));
          if (input.note !== undefined) latest.current.setNote(String(input.note).slice(0, 4000));
          return JSON.stringify({ annotated: true, ...checkMarks(input), note: input.note });
        } },
      { name: "chess_board_play_line", description: "Add a legal SAN/UCI variation starting at the currently displayed position. Leaves the board at the start so the user can step through it.",
        inputSchema: { type: "object", properties: { moves: { type: "array", minItems: 1, maxItems: 100, items: { type: "string" } }, label: { type: "string" }, note: { type: "string" } }, required: ["moves"] },
        execute: async ({ moves, label, note }: { moves: string[]; label?: string; note?: string }) => {
          const frames = latest.current.addLine(moves, label || "Coach's line", String(note || "").slice(0, 2000));
          return JSON.stringify({ added: true, final_fen: frames.at(-1).fen });
        } },
      { name: "chess_board_load", description: "Load a PGN game or FEN position into the existing live board without opening another widget.",
        inputSchema: { type: "object", properties: { position_or_pgn: { type: "string", maxLength: 25000 }, orientation: { type: "string", enum: ["white", "black"] } }, required: ["position_or_pgn"] },
        execute: async ({ position_or_pgn, orientation }: { position_or_pgn: string; orientation?: "white" | "black" }) => {
          const data = localBoard(position_or_pgn, orientation || "white"); latest.current.loadBoard(data);
          return JSON.stringify({ loaded: true, total_plies: data.frames.length - 1 });
        } },
    ];
    for (const tool of tools) registry.registerTool({ ...tool, execute: async (input: any) => {
      const result = await tool.execute(input);
      // A following model read must observe the view that the action just changed.
      await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
      return result;
    } });
    return () => { for (const tool of tools) registry.unregisterTool?.(tool.name); };
  }, []);

  const squareStyles: Record<string, React.CSSProperties> = {};
  if (current.uci) for (const square of [current.uci.slice(0, 2), current.uci.slice(2, 4)]) squareStyles[square] = { backgroundColor: "#ead36c88" };
  for (const h of marks.highlights) squareStyles[h.square] = { backgroundColor: `${COLORS[h.color]}88`, boxShadow: `inset 0 0 0 3px ${COLORS[h.color]}` };
  for (const square of validTargets) squareStyles[square] = { ...squareStyles[square], backgroundImage: "radial-gradient(circle, #203a4780 18%, transparent 20%)" };
  if (selected || arrowStart) squareStyles[(selected || arrowStart)!] = { boxShadow: "inset 0 0 0 4px #203a47" };
  const players = orientation === "white" ? ["Black", "White"] : ["White", "Black"];
  function playerRow(player: string) {
    return <div className="player"><span className={`player-disc ${player.toLowerCase()}`} />
      <strong>{data.headers[player] || player}</strong><span className="muted">{data.headers[player + "Elo"] || ""}</span>
      {data.headers.Result && data.headers.Result !== "*" && <span className="result">{data.headers.Result}</span>}</div>;
  }

  return <main className="app-shell">
    <header className="topbar"><div className="brand"><span className="brand-icon" aria-hidden="true">♞</span><div><div className="eyebrow">CHESS COACH</div><h1>{data.title}</h1></div></div><span className="tag">Analysis</span></header>
    {(error || notice) && <div role={error ? "alert" : "status"} className={error ? "message error" : "message"}>{error || notice}<button aria-label="Dismiss message" onClick={() => { setError(""); setNotice(""); }}>×</button></div>}
    <div className="workspace">
      <section className="board-column" aria-label="Chessboard and controls">
        {playerRow(players[0])}
        <div className="board-wrap" data-testid="board">
          <Chessboard options={{ id: "coach", position: current.fen, boardOrientation: orientation,
            darkSquareStyle: { backgroundColor: "#77956b" }, lightSquareStyle: { backgroundColor: "#edeed1" },
            squareStyles, showAnimations: false, allowDragging: mode === "move" && !promotion,
            allowDragOffBoard: false, dragActivationDistance: 5,
            canDragPiece: ({ square }) => !!square && chess.get(square as Square)?.color === chess.turn(),
            onPieceDrop: ({ sourceSquare, targetSquare }) => targetSquare ? applyMove(sourceSquare, targetSquare) : false,
            onSquareClick: ({ square }) => clickSquare(square),
            allowDrawingArrows: false, clearArrowsOnClick: false,
            arrows: marks.arrows.map(a => ({ startSquare: a.from_square, endSquare: a.to_square, color: COLORS[a.color] })),
            arrowOptions: { ...defaultArrowOptions, opacity: .82, arrowWidthDenominator: 7 },
            onSquareMouseDown: ({ square }, event) => {
              if (event.button === 2) { event.preventDefault(); rightStart.current = { square, color: event.shiftKey ? "red" : event.ctrlKey ? "blue" : event.altKey ? "orange" : color }; }
            },
            onSquareMouseUp: ({ square }, event) => {
              if (event.button === 2 && rightStart.current) { const start = rightStart.current; rightStart.current = null; arrow(start.square, square, start.color); }
            },
          }} />
          {promotion && <div className="promotion" role="dialog" aria-modal="true" aria-label="Choose promotion piece"><strong>Promote to…</strong><div>{[["q", "Queen"], ["r", "Rook"], ["b", "Bishop"], ["n", "Knight"]].map(([piece, name]) => <button key={piece} onClick={() => applyMove(promotion.from, promotion.to, piece)}>{name}</button>)}</div><button onClick={() => setPromotion(null)}>Cancel</button></div>}
        </div>
        {playerRow(players[1])}
        <div className="position-bar"><strong data-testid="position-status" aria-live="polite">{status}</strong><span>{current.label === "Start" ? `Move ${current.fen.split(" ")[5]}` : current.label}{clockLabel(current.clock) ? ` · ${clockLabel(current.clock)}` : ""}</span></div>
        <nav className="transport" aria-label="Replay moves">
          <button aria-label="First position" disabled={ply === 0} onClick={() => { setPlaying(false); navigate(0); }}>⇤</button>
          <button aria-label="Previous move" disabled={ply === 0} onClick={() => { setPlaying(false); navigate(ply - 1); }}>←</button>
          <button aria-label={playing ? "Pause replay" : "Play replay"} disabled={frames.length === 1} onClick={() => { if (!playing && ply === frames.length - 1) navigate(0); setPlaying(!playing); }}>{playing ? "Ⅱ" : "▶"}</button>
          <button aria-label="Next move" disabled={ply === frames.length - 1} onClick={() => { setPlaying(false); navigate(ply + 1); }}>→</button>
          <button aria-label="Last position" disabled={ply === frames.length - 1} onClick={() => { setPlaying(false); navigate(frames.length - 1); }}>⇥</button>
        </nav>
        <div className="board-actions"><button onClick={() => setOrientation(orientation === "white" ? "black" : "white")}>⇅ Flip board</button>{active && <button onClick={() => { setPly(Math.min(active.at, data.frames.length - 1)); setActive(null); setPlaying(false); setSelected(null); setPromotion(null); }}>↩ Back to game</button>}<span className="muted">{ply} / {frames.length - 1}</span></div>
        <div className="draw-toolbar" aria-label="Board annotation tools">
          <div className="segmented">{([['move', 'Move'], ['arrow', 'Arrow'], ['square', 'Highlight']] as const).map(([value, label]) => <button key={value} aria-pressed={mode === value} onClick={() => { setMode(value); setSelected(null); setArrowStart(null); }}>{label}</button>)}</div>
          <div className="colors">{(Object.keys(COLORS) as Color[]).map(c => <button key={c} className={`swatch ${color === c ? "chosen" : ""}`} style={{ "--swatch": COLORS[c] } as React.CSSProperties} aria-label={`${c} annotation`} aria-pressed={color === c} onClick={() => setColor(c)} />)}</div>
          <button className="clear" onClick={() => { mark(EMPTY); setArrowStart(null); }}>Clear marks</button>
        </div>
        <p className="hint">{mode === "arrow" ? arrowStart ? "Now tap the destination square." : "Tap a start square, then a destination." : mode === "square" ? "Tap a square to highlight it. Tap again to remove." : "Drag pieces or tap two squares. Right-drag draws arrows; right-click marks a square."}</p>
      </section>
      <aside className="side-panel">
        <div className="coach-note"><div className="eyebrow">{active ? active.label : "POSITION NOTES"}</div><p>{(active?.note || note || current.comment || "Explore a move, mark an idea, or ask your coach about this position.").replace(/\[%[^\]]*\]/g, "").trim()}</p></div>
        <div className="tabs" role="tablist" aria-label="Analysis panels">{([['moves', 'Moves'], ['lines', 'Engine & lines'], ['load', 'Load game']] as const).map(([value, label]) => <button key={value} role="tab" aria-selected={tab === value} onClick={() => setTab(value)}>{label}</button>)}</div>
        <div className="panel-content">
          {tab === "moves" && <><div className="panel-label">{active?.label || "Recorded game"}</div><div className="moves" aria-label="Move list">
            <button className={ply === 0 ? "current" : ""} onClick={() => navigate(0)}>Start</button>
            {frames.slice(1).map((f, index) => <button key={index} className={ply === index + 1 ? "current" : ""} aria-current={ply === index + 1 ? "step" : undefined} onClick={() => { setPlaying(false); navigate(index + 1); }}>{f.label}</button>)}
          </div>{frames.length === 1 && <p className="empty">Move a piece to explore a line, or load a game to replay it.</p>}
          {data.variations.length > 0 && <div className="saved-lines"><div className="panel-label">Prepared variations</div>{data.variations.map(v => <button key={v.id} onClick={() => chooseVariation(v)}>{v.label} <span>↗</span></button>)}</div>}</>}
          {tab === "lines" && <><div className="engine-header"><strong>Stockfish</strong><label>Search <select aria-label="Engine search time" value={seconds} onChange={e => setSeconds(Number(e.target.value))}><option value={1}>1 sec</option><option value={3}>3 sec</option><option value={8}>8 sec</option></select></label></div>
            <button className="primary full" disabled={!canUseHost || !!busy} onClick={analyze}>{busy === "engine" ? "Analyzing…" : "Analyze this position"}</button>
            {!canUseHost && <p className="hint">Open this board from ChessRailway in chat to run Stockfish.</p>}
            {currentAnalysis?.terminal && <p>{currentAnalysis.result} · {currentAnalysis.reason?.replaceAll("_", " ")}</p>}
            {currentAnalysis?.lines.map((line, i) => <button className="engine-line" key={i} onClick={() => addLine(line.uci, `${currentAnalysis.engine || "Stockfish"} · line ${i + 1}`, `Analysis of ${current.label}. ${scoreLabel(line)}; depth ${line.depth}.`)}><span><strong>{scoreLabel(line)}</strong><small>Depth {line.depth}</small></span><span>{line.san.slice(0, 10).join(" ")}</span></button>)}
            {currentAnalysis && <p className="hint">{currentAnalysis.engine || "Stockfish"} · Positive scores favor White. Tap a line to replay it. Finite searches are estimates.{currentAnalysis.can_claim_draw ? " A draw can be claimed in this position." : ""}</p>}
            {!currentAnalysis && <p className="empty">Analyze to see candidate moves and playable engine lines for the current position.</p>}
            {data.variations.map(v => <button className="line-button" key={v.id} onClick={() => chooseVariation(v)}>{v.label} ↗</button>)}
          </>}
          {tab === "load" && <><label className="field-label" htmlFor="username">Chess.com username</label><div className="input-row"><input id="username" placeholder="Your username" value={username} maxLength={40} onChange={e => setUsername(e.target.value)} /><button disabled={!canUseHost || !!busy || !username.trim()} onClick={() => run("games", async () => {
            const result = await callTool("get_board_games", { username: username.trim(), count: 5, time_class: "rapid" });
            const found = (result.structuredContent?.games || []) as BoardGame[]; setGames(found);
            if (!found.length) setNotice("No recent rapid games were found.");
          })}>{busy === "games" ? "Loading…" : "Fetch games"}</button></div>
          {games.map(g => <button className="game-choice" key={g.url} onClick={() => run("load", async () => {
            const result = await callTool("show_chess_board", { pgn: g.pgn, orientation: g.color, title: `${g.white} vs ${g.black}` }); loadBoard(result._meta?.board as BoardData);
          })}><strong>{g.white} vs {g.black}</strong><small>{g.date} · {g.result}</small></button>)}
          {!canUseHost && <p className="hint">Fetching games is available when the board is open in chat. You can paste a game below.</p>}
          <label className="field-label" htmlFor="position-input">Paste a PGN game or FEN position</label><textarea id="position-input" value={input} maxLength={25000} rows={5} placeholder="1. e4 e5 2. Nf3 Nc6…" onChange={e => setInput(e.target.value)} />
          <button className="primary full" disabled={!!busy} onClick={loadInput}>Load on board</button>
          <div className="load-extras"><button onClick={() => loadBoard(localBoard())}>Starting position</button><button onClick={() => loadBoard(sampleBoard())}>Try a mating puzzle</button></div>
          <label className="field-label" htmlFor="current-fen">Current FEN</label><textarea id="current-fen" readOnly rows={2} value={current.fen} onFocus={e => e.target.select()} />
          </>}
        </div>
        <div className="ask-panel"><label className="field-label" htmlFor="coach-question">Discuss this position</label><input id="coach-question" value={question} maxLength={1000} placeholder="What should I look for here?" onChange={e => setQuestion(e.target.value)} /><button className="primary full" disabled={!canUseHost || !!busy} onClick={() => run("ask", async () => {
          await askCoach(`${question.trim() || "Explain this position and my markings. Check it with Stockfish and show useful arrows or a variation on the board."}\n\nChessboard context:\n${JSON.stringify(context())}`); setNotice("Position and markings sent to your coach.");
        })}>{busy === "ask" ? "Sending…" : "Ask coach about this position ↗"}</button><p className="hint">Includes the exact position, your arrows and any engine result.</p></div>
      </aside>
    </div>
    <footer><span className={`connection-dot ${canUseHost ? "connected" : ""}`} />{canUseHost ? "Connected to your coach" : host === "connecting" ? "Connecting to your coach…" : "Board preview · Open in ChessRailway for coaching"}<span className="footer-note">Moves here are analysis only.</span></footer>
  </main>;
}

class ErrorBoundary extends React.Component<React.PropsWithChildren, { error: boolean }> {
  state = { error: false };
  static getDerivedStateFromError() { return { error: true }; }
  render() { return this.state.error ? <div className="message error">The board could not load this position. Reopen it or try another PGN.</div> : this.props.children; }
}

createRoot(document.getElementById("root")!).render(<ErrorBoundary><BoardApp /></ErrorBoundary>);
