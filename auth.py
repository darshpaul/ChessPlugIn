from __future__ import annotations
import os, time, secrets, hashlib, hmac, html, base64, re
from urllib.parse import urlencode
from collections import defaultdict, deque
import httpx
import jwt
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

BASE=os.environ.get("CHESS_COACH_PUBLIC_URL","https://chess-coach-mcp-production-6a7c.up.railway.app").rstrip("/")
RESOURCE=BASE+"/mcp"
router=APIRouter()
codes={}
failures=defaultdict(deque)
client_metadata={}
SCOPES={"chess:analyze", "offline_access"}

def configured():
    return bool(os.environ.get("CHESS_COACH_LOGIN_PASSWORD") and os.environ.get("CHESS_COACH_OAUTH_SIGNING_KEY"))

def error(name,status=400):
    return JSONResponse({"error":name},status_code=status,headers={"Cache-Control":"no-store"})

def challenge():
    return JSONResponse({"error":"unauthorized"},status_code=401,headers={"WWW-Authenticate":f'Bearer realm="Chess Coach", resource_metadata="{BASE}/.well-known/oauth-protected-resource", scope="chess:analyze"',"Cache-Control":"no-store"})

def valid_client(client,redirect):
    # Restrict metadata fetches to official ChatGPT documents; never follow redirects.
    return bool(re.fullmatch(r"https://chatgpt\.com/oauth/(?:[A-Za-z0-9_-]+/)?client\.json", client)
                and re.fullmatch(r"https://chatgpt\.com/(?:connector_platform_oauth_redirect|connector/oauth/[A-Za-z0-9_-]+)", redirect))

async def check_client_metadata(client, redirect):
    """CIMD is an exact published redirect allowlist, not just a URL pattern."""
    if not valid_client(client, redirect):
        return "invalid_client"
    cached=client_metadata.get(client)
    if cached and cached[0]>time.monotonic():
        metadata=cached[1]
    else:
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as session:
                response=await session.get(client, headers={"Accept":"application/json"})
                response.raise_for_status()
                metadata=response.json()
            if not isinstance(metadata,dict) or metadata.get("client_id")!=client:
                return "invalid_client"
        except (httpx.HTTPError, ValueError):
            return "temporarily_unavailable"
        if len(client_metadata)>=128:
            client_metadata.clear()
        client_metadata[client]=(time.monotonic()+300,metadata)
    # ChatGPT's current CIMD has both fields. The plural list is authoritative;
    # its legacy preference may be private_key_jwt even when none is supported.
    methods=metadata.get("token_endpoint_auth_methods_supported")
    if methods is None:
        methods=[metadata.get("token_endpoint_auth_method", "none")]
    if not isinstance(methods,list) or "none" not in methods:
        return "invalid_client"
    redirects=metadata.get("redirect_uris")
    if not isinstance(redirects,list) or redirect not in redirects:
        return "invalid_client"
    return ""

def check_params(p):
    if p.get("response_type")!="code":return "unsupported_response_type"
    if not valid_client(p.get("client_id",""),p.get("redirect_uri","")):return "invalid_client"
    cc=p.get("code_challenge","")
    if p.get("code_challenge_method")!="S256" or not re.fullmatch(r"[A-Za-z0-9_-]{43}",cc):return "invalid_request"
    if p.get("resource")!=RESOURCE:return "invalid_target"
    scopes=set(p.get("scope","chess:analyze").split())
    if "chess:analyze" not in scopes or not scopes<=SCOPES:return "invalid_scope"
    if not 1<=len(p.get("state",""))<=2048:return "invalid_request"
    return ""

def encode(kind,client,ttl):
    now=int(time.time())
    return jwt.encode({"iss":BASE,"aud":RESOURCE,"sub":"owner","iat":now,"nbf":now,"exp":now+ttl,"jti":secrets.token_urlsafe(20),"type":kind,"client_id":client,"scope":"chess:analyze"},os.environ["CHESS_COACH_OAUTH_SIGNING_KEY"],algorithm="HS256")

def verify_access(token):
    try:
        data=jwt.decode(token,os.environ.get("CHESS_COACH_OAUTH_SIGNING_KEY",""),algorithms=["HS256"],audience=RESOURCE,issuer=BASE,options={"require":["exp","iat","iss","aud","sub"]})
        return data.get("type")=="access" and data.get("sub")=="owner" and "chess:analyze" in data.get("scope","").split()
    except (jwt.PyJWTError,ValueError):return False

@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
async def resource_metadata():
    return {"resource":RESOURCE,"authorization_servers":[BASE],"scopes_supported":["chess:analyze"]}

@router.get("/.well-known/oauth-authorization-server")
async def server_metadata():
    return {"issuer":BASE,"authorization_response_iss_parameter_supported":True,"authorization_endpoint":BASE+"/authorize","token_endpoint":BASE+"/token","response_types_supported":["code"],"grant_types_supported":["authorization_code","refresh_token"],"token_endpoint_auth_methods_supported":["none"],"code_challenge_methods_supported":["S256"],"scopes_supported":["chess:analyze","offline_access"],"client_id_metadata_document_supported":True}

def form_page(p,message=""):
    allowed={"response_type","client_id","redirect_uri","code_challenge","code_challenge_method","scope","state","resource"}
    hidden="".join(f'<input type="hidden" name="{html.escape(k,quote=True)}" value="{html.escape(str(v),quote=True)}">' for k,v in p.items() if k in allowed)
    note='<p style="color:#b91c1c">'+html.escape(message)+'</p>' if message else ""
    body='<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Chess Coach</title></head><body style="font-family:system-ui;background:#f5f5f5;padding:3rem 1rem"><main style="max-width:430px;margin:auto;background:white;padding:2rem;border-radius:16px"><h2>Connect Chess Coach</h2><p>Enter your private Chess Coach password to authorize ChatGPT.</p>'+note+'<form method="post" action="/authorize">'+hidden+'<label>Password <input name="password" type="password" required autocomplete="current-password" style="display:block;width:100%;padding:10px;box-sizing:border-box;margin:10px 0 20px"></label><button type="submit">Authorize Chess Coach</button></form></main></body></html>'
    # Chromium checks the POST's redirect against form-action too. Allow the
    # validated callback origin or successful logins silently remain on this page.
    return HTMLResponse(body,headers={"Cache-Control":"no-store","Referrer-Policy":"no-referrer","Content-Security-Policy":"default-src 'none'; style-src 'unsafe-inline'; form-action 'self' https://chatgpt.com; base-uri 'none'; frame-ancestors 'none'"})

@router.get("/authorize")
async def authorize_get(request:Request):
    if not configured():return HTMLResponse("Configure CHESS_COACH_LOGIN_PASSWORD and CHESS_COACH_OAUTH_SIGNING_KEY in Railway.",status_code=503)
    p=dict(request.query_params);bad=check_params(p)
    if not bad:bad=await check_client_metadata(p["client_id"],p["redirect_uri"])
    return error(bad) if bad else form_page(p)

@router.post("/authorize")
async def authorize_post(request:Request):
    if not configured():return error("server_unavailable",503)
    form=await request.form();p={k:str(v) for k,v in form.items() if k!="password"}
    bad=check_params(p)
    if not bad:bad=await check_client_metadata(p["client_id"],p["redirect_uri"])
    if bad:return error(bad)
    ip=request.client.host if request.client else "unknown";now=time.monotonic();bucket=failures[ip]
    while bucket and bucket[0]<now-900:bucket.popleft()
    if len(bucket)>=8:return form_page(p,"Too many attempts. Try again in 15 minutes.")
    if not hmac.compare_digest(str(form.get("password","")).encode(),os.environ["CHESS_COACH_LOGIN_PASSWORD"].encode()):
        bucket.append(now);return form_page(p,"Incorrect password.")
    bucket.clear()
    for k,v in list(codes.items()):
        if v["exp"]<time.time():codes.pop(k,None)
    code=secrets.token_urlsafe(32)
    codes[code]={"client":p["client_id"],"redirect":p["redirect_uri"],"challenge":p["code_challenge"],"scope":p.get("scope","chess:analyze"),"resource":p["resource"],"exp":time.time()+300}
    return RedirectResponse(p["redirect_uri"]+"?"+urlencode({"code":code,"state":p["state"],"iss":BASE}),status_code=303,headers={"Cache-Control":"no-store","Referrer-Policy":"no-referrer"})

@router.post("/token")
async def token_endpoint(request:Request):
    if not configured():return error("server_unavailable",503)
    f=await request.form();kind=str(f.get("grant_type",""));client=str(f.get("client_id",""))
    if str(f.get("resource",RESOURCE))!=RESOURCE:return error("invalid_target")
    if request.headers.get("authorization") or f.get("client_secret") or f.get("client_assertion"):
        return error("invalid_client",401)
    if kind=="authorization_code":
        code=str(f.get("code",""));record=codes.get(code)
        if not record or record["exp"]<time.time():return error("invalid_grant")
        verifier=str(f.get("code_verifier",""))
        digest=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        if not (re.fullmatch(r"[A-Za-z0-9._~-]{43,128}",verifier) and hmac.compare_digest(digest,record["challenge"]) and client==record["client"] and str(f.get("redirect_uri",""))==record["redirect"] and str(f.get("resource",RESOURCE))==record["resource"]):return error("invalid_grant")
        # Consume only after validation. No await occurs between lookup and pop.
        codes.pop(code)
        result={"access_token":encode("access",client,3600),"token_type":"Bearer","expires_in":3600,"scope":"chess:analyze"}
        if "offline_access" in record["scope"].split():result["refresh_token"]=encode("refresh",client,2592000)
        return JSONResponse(result,headers={"Cache-Control":"no-store"})
    if kind=="refresh_token":
        try:
            raw=jwt.decode(str(f.get("refresh_token","")),os.environ["CHESS_COACH_OAUTH_SIGNING_KEY"],algorithms=["HS256"],audience=RESOURCE,issuer=BASE,options={"require":["exp","iat","iss","aud","sub"]})
            if raw.get("type")!="refresh" or raw.get("sub")!="owner" or raw.get("client_id")!=client or "chess:analyze" not in raw.get("scope","").split():raise ValueError()
        except (jwt.PyJWTError,ValueError):return error("invalid_grant")
        return JSONResponse({"access_token":encode("access",client,3600),"refresh_token":encode("refresh",client,2592000),"token_type":"Bearer","expires_in":3600,"scope":"chess:analyze"},headers={"Cache-Control":"no-store"})
    return error("unsupported_grant_type")
