import { App, applyDocumentTheme, applyHostStyleVariables } from "@modelcontextprotocol/ext-apps";
import type { BoardData } from "./model";

type Result = { structuredContent?: Record<string, unknown>; _meta?: Record<string, unknown>; isError?: boolean; content?: { type: string; text?: string }[] };
export type HostState = "connecting" | "connected" | "standalone" | "unavailable";
type WebTool = { name: string; description: string; inputSchema: Record<string, unknown>; annotations?: Record<string, boolean>; execute: (input: any) => Promise<string> };
declare global {
  interface Window {
    openai?: {
      widgetState?: unknown; toolOutput?: unknown; toolResponseMetadata?: Record<string, unknown>;
      setWidgetState?: (state: unknown) => void;
      callTool?: (name: string, args: Record<string, unknown>) => Promise<Result>;
      sendFollowUpMessage?: (args: { prompt: string }) => Promise<void>;
    };
  }
  interface Document { modelContext?: { registerTool(tool: WebTool): void; unregisterTool?(name: string): void } }
}

export const app = new App({ name: "Chess Coach board", version: "1.0.0" }, { availableDisplayModes: ["inline", "fullscreen"] });
let ready = false;
let last: BoardData | undefined;
let status: HostState = window.parent === window ? "standalone" : "connecting";
const listeners = new Set<(data: BoardData) => void>();
const statusListeners = new Set<(state: HostState) => void>();
function setStatus(state: HostState) { status = state; for (const fn of statusListeners) fn(state); }

function accept(result: Result) {
  const data = result._meta?.board as BoardData | undefined;
  if (data) { last = data; for (const fn of listeners) fn(data); }
}
app.ontoolresult = result => accept(result as Result);
app.onhostcontextchanged = context => {
  if (context.theme) applyDocumentTheme(context.theme);
  if (context.styles?.variables) applyHostStyleVariables(context.styles.variables);
  for (const side of ["top", "bottom", "left", "right"] as const) {
    document.documentElement.style.setProperty(`--safe-${side}`, `${context.safeAreaInsets?.[side] || 0}px`);
  }
};
export function subscribeBoard(fn: (data: BoardData) => void) { listeners.add(fn); if (last) fn(last); return () => { listeners.delete(fn); }; }
export function subscribeStatus(fn: (state: HostState) => void) { statusListeners.add(fn); fn(status); return () => { statusListeners.delete(fn); }; }

if (window.parent !== window) {
  app.connect().then(() => {
    ready = true;
    setStatus("connected");
    app.onhostcontextchanged?.(app.getHostContext() || {});
  }).catch(() => setStatus(window.openai?.callTool ? "connected" : "unavailable"));
  // Older compatible ChatGPT hosts may hydrate the result through their extension.
  if (window.openai?.toolResponseMetadata?.board) accept({ _meta: window.openai.toolResponseMetadata });
}

export async function callTool(name: string, args: Record<string, unknown>): Promise<Result> {
  const result = (ready ? await app.callServerTool({ name, arguments: args }, { timeout: 90000 })
    : window.openai?.callTool ? await window.openai.callTool(name, args)
    : (() => { throw new Error("Open this board from ChessRailway in chat to use this action."); })()) as Result;
  if (result.isError || result.structuredContent?.error) throw new Error(String(result.structuredContent?.error || result.content?.find(c => c.type === "text")?.text || "The action failed. Please try again."));
  return result as Result;
}

export function updateContext(context: Record<string, unknown>, state: unknown) {
  window.openai?.setWidgetState?.(state);
  if (ready) void app.updateModelContext({ structuredContent: context }).catch(() => {});
}

export async function askCoach(text: string) {
  if (ready) {
    const result = await app.sendMessage({ role: "user", content: [{ type: "text", text }] });
    if (result.isError) throw new Error("The coach could not receive the position. Please try again.");
  } else if (window.openai?.sendFollowUpMessage) await window.openai.sendFollowUpMessage({ prompt: text });
  else throw new Error("Open this board from ChessRailway in chat to ask your coach.");
}
