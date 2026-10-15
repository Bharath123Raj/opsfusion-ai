"""Same-site browser access for a private reverse-proxied Streamlit backend.

The cookie contains the existing opaque account bearer, whose hash is persisted.
It authenticates the gateway only; /v1 still requires an explicit bearer header.
"""
from dataclasses import dataclass
import hmac
from html import escape
import os
import secrets
from urllib.parse import parse_qs, urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from .accounts import Accounts


@dataclass(frozen=True)
class AccessSettings:
    mode:str
    origin:str

    @property
    def secure(self):return self.mode=='https'

    @property
    def cookie(self):return '__Host-opsfusion_session' if self.secure else 'opsfusion_session'

    @property
    def csrf_cookie(self):return '__Host-opsfusion_csrf' if self.secure else 'opsfusion_csrf'


def settings():
    mode=os.environ.get('OPSFUSION_ACCESS_MODE','off')
    if mode=='off':return None
    origin=os.environ.get('OPSFUSION_PUBLIC_ORIGIN','')
    try:
        p=urlsplit(origin);p.port
        if mode not in ('local','https') or not p.hostname or p.path not in ('','/') or p.query or p.fragment or p.username or p.password or any(c.isspace() for c in origin) or '\x00' in origin:raise ValueError()
        if mode=='https' and p.scheme!='https':raise ValueError()
        if mode=='local' and (p.scheme!='http' or p.hostname not in ('127.0.0.1','localhost','::1')):raise ValueError()
    except ValueError:raise ValueError('Access mode requires an HTTPS public origin, or explicit local mode with a loopback HTTP origin') from None
    host=p.hostname.encode('idna').decode().lower()
    if ':' in host:host='['+host+']'
    port=p.port
    if port and port!=(443 if p.scheme=='https' else 80):host+=':'+str(port)
    return AccessSettings(mode,p.scheme+'://'+host)


def _headers(response):
    response.headers.update({'Cache-Control':'no-store','Pragma':'no-cache','X-Content-Type-Options':'nosniff',
        'Referrer-Policy':'same-origin','Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"})
    return response


def _page(cfg,logout=False,error='',status=200):
    nonce=secrets.token_urlsafe(32);title='Sign out of OpsFusion' if logout else 'Sign in to OpsFusion'
    fields='' if logout else '<label>Username<input name="username" autocomplete="username" maxlength="64" required></label><label>Password<input name="password" type="password" autocomplete="current-password" maxlength="256" required></label>'
    response=HTMLResponse('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>'+title+'</title><style>body{font:17px system-ui;background:#f3f6fa;color:#182235;margin:0}main{max-width:380px;margin:12vh auto;padding:32px;background:white;border-radius:12px}label{display:block;margin:18px 0}input,button{box-sizing:border-box;width:100%;font:inherit;padding:10px;margin-top:6px}button{background:#164da0;color:white;border:0;border-radius:6px}p{line-height:1.5}.error{color:#a31927}</style><main><h1>'+title+'</h1><p>Use your OpsFusion account.</p><p class="error" role="alert">'+escape(error)+'</p><form method="post" action="/access/'+('logout' if logout else 'login')+'"><input type="hidden" name="csrf" value="'+nonce+'">'+fields+'<button type="submit">'+('Sign out' if logout else 'Sign in')+'</button></form></main></html>',status_code=status)
    response.set_cookie(cfg.csrf_cookie,nonce,max_age=1800,path='/',httponly=True,secure=cfg.secure,samesite='strict')
    return _headers(response)


async def _form(request,cfg):
    if request.headers.get('origin')!=cfg.origin or request.headers.get('content-type','').split(';')[0]!='application/x-www-form-urlencoded':raise ValueError('Invalid same-site form')
    body=await request.body()
    if len(body)>4096:raise ValueError('Form too large')
    try:
        fields=parse_qs(body.decode('utf-8'),strict_parsing=True,max_num_fields=4)
        if any(len(v)!=1 for v in fields.values()):raise ValueError()
        fields={k:v[0] for k,v in fields.items()}
        nonce=fields.pop('csrf','');cookie=request.cookies.get(cfg.csrf_cookie,'')
        if not 20<=len(nonce)<=128 or not hmac.compare_digest(nonce,cookie):raise ValueError()
        return fields
    except (ValueError,TypeError,UnicodeError):raise ValueError('Invalid same-site form') from None


def install(app):
    cfg=settings()
    if cfg is None:return
    router=APIRouter(prefix='/access')
    def accounts(request):return Accounts(request.app.state.accounts_path)

    @router.get('/login')
    def login_page():return _page(cfg)

    @router.post('/login')
    async def login(request:Request):
        try:
            fields=await _form(request,cfg)
            if set(fields)!={'username','password'}:raise ValueError()
        except ValueError:return _headers(JSONResponse({'detail':'Invalid same-site form'},status_code=403))
        try:token=accounts(request).login(fields['username'],fields['password'])
        except (PermissionError,ValueError):return _page(cfg,error='Sign-in failed. Check your credentials or retry later.',status=401)
        accounts(request).logout(request.cookies.get(cfg.cookie))
        response=RedirectResponse('/',status_code=303)
        response.set_cookie(cfg.cookie,token,max_age=28800,path='/',httponly=True,secure=cfg.secure,samesite='lax')
        response.delete_cookie(cfg.csrf_cookie,path='/',secure=cfg.secure,httponly=True,samesite='strict')
        return _headers(response)

    @router.get('/verify')
    def verify(request:Request):
        try:accounts(request).require(request.cookies.get(cfg.cookie),'read')
        except PermissionError:
            original=request.headers.get('x-forwarded-uri','')
            if original=='/':return _headers(RedirectResponse('/access/login',status_code=303))
            return _headers(JSONResponse({'detail':'Sign in to access OpsFusion'},status_code=401))
        return _headers(Response(status_code=204))

    @router.get('/logout')
    def logout_page():return _page(cfg,logout=True)

    @router.post('/logout')
    async def logout(request:Request):
        try:
            fields=await _form(request,cfg)
            if fields:raise ValueError()
        except ValueError:return _headers(JSONResponse({'detail':'Invalid same-site form'},status_code=403))
        accounts(request).logout(request.cookies.get(cfg.cookie))
        response=RedirectResponse('/access/login',status_code=303)
        response.delete_cookie(cfg.cookie,path='/',secure=cfg.secure,httponly=True,samesite='lax')
        response.delete_cookie(cfg.csrf_cookie,path='/',secure=cfg.secure,httponly=True,samesite='strict')
        return _headers(response)

    app.include_router(router)
