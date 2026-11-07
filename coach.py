from __future__ import annotations
import os, re, time
from io import StringIO
from datetime import datetime, timezone
from urllib.parse import urlparse
import chess, chess.pgn, chess.engine, httpx

class CoachError(Exception): pass
def validate_username(name):
    name=name.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{2,40}",name): raise CoachError("Invalid Chess.com username.")
    return name
def bounds(count,seconds=None):
    if type(count)!=int or not 1<=count<=10: raise CoachError("Count must be 1-10.")
    if seconds is not None and not .05<=seconds<=.5: raise CoachError("Seconds must be 0.05-0.5.")
def get_json(client,url):
    p=urlparse(url)
    if p.scheme!="https" or p.hostname!="api.chess.com" or not p.path.startswith("/pub/player/"): raise CoachError("Invalid archive URL.")
    for attempt in range(3):
        try:
            r=client.get(url,headers={"User-Agent":"ChessCoachPersonal/1.0","Accept":"application/json"},follow_redirects=False)
            if r.status_code==404: raise CoachError("Chess.com user/archive not found.")
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError,ValueError) as e:
            if attempt==2: raise CoachError("Chess.com API request failed: "+type(e).__name__)
            time.sleep(.6*(attempt+1))
def fetch_recent_games(username,count=5,time_class="rapid"):
    username=validate_username(username);bounds(count)
    if time_class not in ("rapid","blitz","bullet","daily","all"): raise CoachError("Invalid time control.")
    found={}
    with httpx.Client(timeout=20) as client:
        months=get_json(client,f"https://api.chess.com/pub/player/{username.lower()}/games/archives").get("archives",[])
        for url in reversed(months[-12:]):
            for g in get_json(client,url).get("games",[]):
                if g.get("rules","chess")!="chess" or not g.get("pgn"): continue
                if time_class!="all" and g.get("time_class")!=time_class: continue
                if username.lower() not in [g.get("white",{}).get("username","").lower(),g.get("black",{}).get("username","").lower()]: continue
                found[g.get("uuid") or g.get("url")]=g
            if len(found)>=count: break
    return sorted(found.values(),key=lambda g:g.get("end_time",0),reverse=True)[:count]
def summarize_game(g,username):
    iswhite=g.get("white",{}).get("username","").casefold()==username.casefold()
    player=g["white" if iswhite else "black"]; opp=g["black" if iswhite else "white"]
    return {"url":g.get("url"),"date":datetime.fromtimestamp(g.get("end_time",0),timezone.utc).date().isoformat(),"color":"white" if iswhite else "black","opponent":opp.get("username"),"rating":player.get("rating"),"result":player.get("result"),"time_class":g.get("time_class")}
def engine_info(engine,board,seconds,cache):
    fen=board.fen()
    if fen in cache:return cache[fen]
    info=engine.analyse(board,chess.engine.Limit(time=seconds))
    cp=info["score"].white().score(mate_score=10000) or 0
    pv=info.get("pv",[]); b=board.copy(); san=[]
    for mv in pv[:6]:
        if mv not in b.legal_moves:break
        san.append(b.san(mv));b.push(mv)
    result=(cp,san[0] if san else "?", " ".join(san))
    cache[fen]=result;return result
def analyze_game(pgn,username,engine,seconds,url="",cache=None):
    game=chess.pgn.read_game(StringIO(pgn))
    if game is None or game.errors:raise CoachError("Invalid PGN.")
    white=game.headers.get("White","");black=game.headers.get("Black","")
    if username.casefold() not in (white.casefold(),black.casefold()):raise CoachError("Player not in PGN.")
    color=chess.WHITE if username.casefold()==white.casefold() else chess.BLACK
    sign=1 if color else -1
    board=game.board();cache={} if cache is None else cache
    losses={"opening":[],"later":[]}; issues=[]
    for mv in game.mainline_moves():
        mine=board.turn==color;move_number=board.fullmove_number
        if mine:
            fen=board.fen();played=board.san(mv);before,best,pv=engine_info(engine,board,seconds,cache)
        board.push(mv)
        if not mine:continue
        after,_,_=engine_info(engine,board,seconds,cache)
        cpl=max(0,min(1500,(before-after)*sign));stage="opening" if 5<=move_number<=15 else "later"
        losses[stage].append(cpl)
        if cpl>=90:
            issues.append({"move":f"{move_number}{'.' if color else '...'}{played}","move_number":move_number,"phase":stage,"played":played,"best":best,"principal_variation":pv,"approx_cp_loss":cpl,"fen_before":fen,"eval_before":round(before*sign/100,2),"eval_after":round(after*sign/100,2)})
    issues.sort(key=lambda a:(a["phase"]!="opening",-a["approx_cp_loss"]))
    return {"opponent":black if color else white,"color":"white" if color else "black","date":game.headers.get("Date"),"result":game.headers.get("Result"),"url":url,"avg_opening_cp_loss":round(sum(losses["opening"])/len(losses["opening"]),1) if losses["opening"] else None,"avg_other_cp_loss":round(sum(losses["later"])/len(losses["later"]),1) if losses["later"] else None,"opening_mistake_count":sum(i["phase"]=="opening" for i in issues),"critical_positions":issues[:8]}
def engine_path():
    path=os.environ.get("STOCKFISH_PATH","/usr/games/stockfish")
    if not os.path.isfile(path):raise CoachError("Stockfish binary unavailable.")
    return path
def report(username,games,failures,seconds):
    return {"username":username,"games_analyzed":len(games),"engine_seconds_per_position":seconds,"games_with_opening_errors":sum(g["opening_mistake_count"]>0 for g in games),"games":games,"skipped_games":failures,"training_plan":["On moves 5-15 identify opponents' threats before attacking.","Calculate opponent's strongest checks and captures before each move.","In quiet positions improve the worst piece or prepare a justified pawn break.","Review critical FEN positions without the engine."],"caveats":"Short engine searches are approximate. A 90cp threshold is a coaching heuristic, not an official blunder label."}
def analyze_records(username,records,seconds=.15):
    bounds(len(records),seconds)
    if not records:raise CoachError("No matching games.")
    engine=chess.engine.SimpleEngine.popen_uci(engine_path(),timeout=20);games=[];errors=[];cache={}
    try:
        for g in records:
            try:games.append(analyze_game(g["pgn"],username,engine,seconds,g.get("url",""),cache))
            except (CoachError,ValueError,KeyError,chess.engine.EngineError) as e:errors.append(str(e)[:150])
    finally:engine.quit()
    if not games:raise CoachError("No games were analyzable.")
    return report(username,games,errors,seconds)
def analyze_pasted_pgn(pgn,username,seconds=.15):
    username=validate_username(username);bounds(1,seconds)
    if not 1<=len(pgn)<=25000:raise CoachError("PGN length must be 1-25000 characters.")
    engine=chess.engine.SimpleEngine.popen_uci(engine_path(),timeout=20)
    try:game=analyze_game(pgn,username,engine,seconds)
    finally:engine.quit()
    return report(username,[game],[],seconds)
