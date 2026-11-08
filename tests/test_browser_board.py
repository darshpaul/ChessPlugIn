"""Exercise the real bundle, MCP Apps bridge, OAuth-protected tools and Stockfish.

The host is a small test harness, not a claim of live ChatGPT verification.
All browser network traffic is intercepted locally.
"""
import json
import time
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright, expect

import auth
import server
from conftest import CLIENT
from test_board import MATE_FEN, MATE_LINE, PGN, stockfish
from test_oauth import issue_code, rpc, token_form


def host_html(result):
    initial = json.dumps(result).replace("<", "\\u003c")
    return """<!doctype html><html><body style="margin:0"><iframe title="Chess board" src="https://board.test/board" sandbox="allow-scripts allow-same-origin" style="width:100%;height:1100px;border:0"></iframe><script>
    const initial = INITIAL;
    window.messages = [];
    window.addEventListener('message', async event => {
      const frame = document.querySelector('iframe');
      if (event.source !== frame.contentWindow || event.data?.jsonrpc !== '2.0') return;
      const m = event.data;
      window.messages.push(m);
      const reply = result => frame.contentWindow.postMessage({jsonrpc:'2.0',id:m.id,result}, '*');
      if (m.method === 'ui/initialize') reply({
        protocolVersion:m.params.protocolVersion, hostInfo:{name:'Browser test host',version:'1'},
        hostCapabilities:{serverTools:{},message:{text:{}},updateModelContext:{structuredContent:{},text:{}}},
        hostContext:{theme:'light',displayMode:'inline',availableDisplayModes:['inline','fullscreen']}
      });
      else if (m.method === 'ui/notifications/initialized') frame.contentWindow.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:initial}, '*');
      else if (m.method === 'tools/call') {
        const response = await fetch('/rpc', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(m.params)});
        const value = await response.json();
        reply(value.result);
      }
      else if (m.method === 'ui/notifications/size-changed') frame.style.height = Math.max(600, m.params.height || 1100) + 'px';
      else if (m.id != null) reply({});
    });
    </script></body></html>""".replace("INITIAL", initial)


@pytest.fixture
def browser_board(client, params):
    access = client.post("/token", data=token_form(issue_code(client, params), params)).json()["access_token"]
    initial = rpc(client, access, "tools/call", {"name": "show_chess_board", "arguments": {"pgn": PGN, "ply": 2}}).json()["result"]
    resource = rpc(client, access, "resources/read", {"uri": server.BOARD_URI}).json()["result"]["contents"][0]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1000, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script("""
          window.boardTools = {};
          Object.defineProperty(document, 'modelContext', {value:{
            registerTool(tool) { window.boardTools[tool.name] = tool; },
            unregisterTool(name) { delete window.boardTools[name]; }
          }});
          window.openai = {setWidgetState(state) {window.savedWidgetState = state;}};
        """)
        def intercept(route):
            request = route.request
            if request.url == "https://host.test/":
                route.fulfill(content_type="text/html", body=host_html(initial))
            elif request.url == "https://board.test/board":
                route.fulfill(content_type="text/html", body=resource["text"], headers={
                    "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'"})
            elif request.url == "https://host.test/rpc":
                result = rpc(client, access, "tools/call", request.post_data_json)
                route.fulfill(content_type="application/json", body=result.text)
            else:
                route.abort()
        page.route("**/*", intercept)
        page.goto("https://host.test/")
        frame = page.frame(url="https://board.test/board")
        expect(frame.locator("footer")).to_contain_text("Connected to your coach")
        expect(frame.get_by_test_id("position-status")).to_have_text("White to move")
        yield page, frame, errors
        browser.close()


def state(frame):
    return json.loads(frame.evaluate("window.boardTools.chess_board_read.execute({})"))


def web_tool(frame, name, args):
    return frame.evaluate("([name,args]) => window.boardTools[name].execute(args)", [name, args])


def wait_widget_ply(frame, ply):
    # waitForFunction's string evaluation is intentionally blocked by the widget CSP.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if frame.evaluate("window.savedWidgetState?.ply") == ply and state(frame)["ply"] == ply:
            return
        frame.page.wait_for_timeout(50)
    raise AssertionError(f"Widget did not persist/display ply {ply}")


def test_replay_legal_moves_drawing_context_and_coach_message(browser_board):
    page, frame, errors = browser_board
    assert state(frame)["ply"] == 2
    frame.get_by_role("button", name="Next move", exact=True).click()
    expect(frame.get_by_test_id("position-status")).to_have_text("Black to move")
    frame.get_by_role("button", name="Previous move", exact=True).click()
    original = state(frame)["fen"]
    frame.locator('[data-square="g1"]').click()
    frame.locator('[data-square="g4"]').click()
    assert state(frame)["fen"] == original
    # Real drag-and-drop rather than calling the move handler.
    start = frame.locator('[data-square="g1"]').bounding_box()
    end = frame.locator('[data-square="f3"]').bounding_box()
    page.mouse.move(start["x"] + start["width"] / 2, start["y"] + start["height"] / 2)
    page.mouse.down()
    page.mouse.move(end["x"] + end["width"] / 2, end["y"] + end["height"] / 2, steps=10)
    page.mouse.up()
    expect(frame.get_by_test_id("position-status")).to_have_text("Black to move")
    assert state(frame)["history"]["moves"][-1] == "g1f3"
    frame.get_by_role("button", name="Back to game").click()
    expect(frame.get_by_test_id("position-status")).to_have_text("White to move")
    assert state(frame)["fen"] == original
    frame.get_by_role("button", name="Arrow", exact=True).click()
    frame.locator('[data-square="g1"]').click()
    frame.locator('[data-square="f3"]').click()
    assert state(frame)["arrows"] == [{"from_square": "g1", "to_square": "f3", "color": "green"}]
    frame.get_by_role("button", name="Highlight", exact=True).click()
    frame.get_by_role("button", name="red annotation").click()
    frame.locator('[data-square="e4"]').click()
    assert state(frame)["highlights"] == [{"square": "e4", "color": "red"}]
    # The assistant's in-place tools use the same state as the user controls.
    web_tool(frame, "chess_board_annotate", {"arrows": [{"from_square": "f1", "to_square": "c4", "color": "blue"}], "note": "Develop with a threat."})
    expect(frame.get_by_text("Develop with a threat.")).to_be_visible()
    assert state(frame)["arrows"][0]["color"] == "blue"
    frame.get_by_label("Discuss this position").fill("Why this bishop move?")
    frame.get_by_role("button", name="Ask coach about this position").click()
    expect(frame.get_by_text("Position and markings sent to your coach.")).to_be_visible()
    message = page.evaluate("window.messages.filter(m => m.method === 'ui/message').at(-1)")
    assert original in message["params"]["content"][0]["text"]
    assert "Why this bishop move?" in message["params"]["content"][0]["text"]
    page.wait_for_function("window.messages.some(m => m.method === 'ui/update-model-context' && m.params.structuredContent.arrows[0]?.color === 'blue')")
    assert not errors


def test_stockfish_and_playable_mate_line(browser_board, stockfish):
    page, frame, errors = browser_board
    web_tool(frame, "chess_board_load", {"position_or_pgn": MATE_FEN})
    frame.get_by_role("tab", name="Engine & lines").click()
    frame.get_by_label("Engine search time").select_option("1")
    frame.get_by_role("button", name="Analyze this position").click()
    expect(frame.get_by_role("button", name="White mates in 6")).to_be_visible(timeout=15000)
    frame.get_by_role("button", name="White mates in 6").click()
    frame.get_by_role("button", name="Last position").click()
    expect(frame.get_by_test_id("position-status")).to_have_text("Black is checkmated")
    assert state(frame)["history"]["moves"][-1] == "h4h7"
    assert not errors


def test_promotions_castling_en_passant_and_invalid_import(browser_board):
    page, frame, errors = browser_board
    web_tool(frame, "chess_board_load", {"position_or_pgn": "7k/P7/8/8/8/8/8/7K w - - 0 1"})
    frame.locator('[data-square="a7"]').click()
    frame.locator('[data-square="a8"]').click()
    expect(frame.get_by_role("dialog", name="Choose promotion piece")).to_be_visible()
    frame.get_by_role("button", name="Knight", exact=True).click()
    assert state(frame)["fen"].startswith("N6k/")
    web_tool(frame, "chess_board_load", {"position_or_pgn": "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1"})
    frame.locator('[data-square="e1"]').click()
    frame.locator('[data-square="g1"]').click()
    assert state(frame)["fen"].split()[0].endswith("R4RK1")
    web_tool(frame, "chess_board_load", {"position_or_pgn": "7k/8/8/3pP3/8/8/8/7K w - d6 0 2"})
    frame.locator('[data-square="e5"]').click()
    frame.locator('[data-square="d6"]').click()
    assert state(frame)["fen"].split()[0] == "7k/8/3P4/8/8/8/8/7K"
    previous = state(frame)["fen"]
    frame.get_by_role("tab", name="Load game").click()
    frame.get_by_label("Paste a PGN game or FEN position").fill("1. e5 *")
    frame.get_by_role("button", name="Load on board").click()
    expect(frame.get_by_role("alert")).to_contain_text("Invalid PGN")
    assert state(frame)["fen"] == previous
    assert not errors


def test_fetch_game_and_restore_widget_view(browser_board, monkeypatch):
    page, frame, errors = browser_board
    monkeypatch.setattr(server, "fetch_recent_games", lambda *args: [{
        "pgn": PGN, "url": "https://www.chess.com/game/live/1", "end_time": 1,
        "time_class": "rapid", "white": {"username": "Player", "rating": 800, "result": "win"},
        "black": {"username": "Opponent", "rating": 800, "result": "checkmated"},
    }])
    frame.get_by_role("tab", name="Load game").click()
    frame.get_by_label("Chess.com username").fill("Player")
    frame.get_by_role("button", name="Fetch games").click()
    frame.get_by_role("button", name="Player vs Opponent").click()
    expect(frame.get_by_role("heading", name="Player vs Opponent")).to_be_visible()
    assert state(frame)["ply"] == 0
    frame.get_by_role("button", name="Last position").click()
    wait_widget_ply(frame, 4)
    # A remount/resume supplies the widget-scoped snapshot separately from its original result.
    frame.evaluate("window.openai.widgetState = structuredClone(window.savedWidgetState)")
    initial = frame.evaluate("window.savedWidgetState.boardId")
    frame.get_by_role("button", name="First position").click()
    expect(frame.get_by_test_id("position-status")).to_have_text("White to move")
    page.evaluate("""async () => {
      const response = await fetch('/rpc', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'show_chess_board',arguments:{pgn:PGN,title:'Player vs Opponent'}})});
      const data = await response.json();
      document.querySelector('iframe').contentWindow.postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:data.result},'*');
    }""".replace("PGN", json.dumps(PGN)))
    wait_widget_ply(frame, 4)
    assert state(frame)["board_id"] == initial
    assert state(frame)["ply"] == 4
    assert not errors


def test_marks_follow_position_without_leaking_between_branches(browser_board):
    page, frame, errors = browser_board
    frame.locator('[data-square="g1"]').click()
    frame.locator('[data-square="f3"]').click()
    expect(frame.get_by_test_id("position-status")).to_have_text("Black to move")
    web_tool(frame, "chess_board_annotate", {"highlights": [{"square": "f3", "color": "green"}]})
    frame.get_by_role("button", name="Back to game").click()
    expect(frame.get_by_test_id("position-status")).to_have_text("White to move")
    frame.locator('[data-square="f1"]').click()
    frame.locator('[data-square="c4"]').click()
    expect(frame.get_by_test_id("position-status")).to_have_text("Black to move")
    assert state(frame)["highlights"] == []
    web_tool(frame, "chess_board_annotate", {"arrows": [{"from_square": "g8", "to_square": "f6", "color": "blue"}]})
    web_tool(frame, "chess_board_play_line", {"moves": ["Nf6"], "label": "Develop"})
    assert state(frame)["arrows"][0]["from_square"] == "g8"
    assert not errors


@pytest.mark.parametrize("width", [375, 768, 1440])
def test_responsive_layout_flip_and_right_drag(browser_board, width):
    page, frame, errors = browser_board
    page.set_viewport_size({"width": width, "height": 1000})
    expect(frame.get_by_test_id("board")).to_be_visible()
    assert frame.evaluate("document.documentElement.scrollWidth <= innerWidth")
    frame.get_by_role("button", name="Flip board").click()
    assert state(frame)["orientation"] == "black"
    a = frame.locator('[data-square="f1"]').bounding_box()
    b = frame.locator('[data-square="c4"]').bounding_box()
    page.mouse.move(a["x"] + a["width"] / 2, a["y"] + a["height"] / 2)
    page.mouse.down(button="right")
    page.mouse.move(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2, steps=6)
    page.mouse.up(button="right")
    assert state(frame)["arrows"][0]["from_square"] == "f1"
    frame.get_by_role("button", name="Clear marks").click()
    assert state(frame)["arrows"] == []
    if width == 1440:
        web_tool(frame, "chess_board_load", {"position_or_pgn": MATE_FEN})
        web_tool(frame, "chess_board_annotate", {"arrows": [{"from_square": "h7", "to_square": "g6", "color": "green"}, {"from_square": "h1", "to_square": "h8", "color": "orange"}], "highlights": [{"square": "h8", "color": "red"}], "note": "37.Bg6+! controls h7 and blocks the queen's defense. Then sacrifice the rook on h8 to bring your queen into the attack."})
        web_tool(frame, "chess_board_play_line", {"moves": MATE_LINE, "label": "Mate in six", "note": "37.Bg6+! controls h7 and blocks the queen's defense. Then sacrifice the rook on h8 to bring your queen into the attack."})
        # Mark the active line, since each line/position keeps its own annotations.
        web_tool(frame, "chess_board_annotate", {"arrows": [{"from_square": "h7", "to_square": "g6", "color": "green"}, {"from_square": "h1", "to_square": "h8", "color": "orange"}], "highlights": [{"square": "h8", "color": "red"}]})
        path = Path("../chess-analysis/board-desktop.png")
        path.parent.mkdir(exist_ok=True)
        frame.locator(".app-shell").screenshot(path=str(path))
    assert not errors
