"""Loopback-only launch recommended: stateless event replay, no file-path access."""
from typing import Literal
from pathlib import Path
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from . import __version__
from .core import Event
from . import app_service as service
from .accounts import Accounts
from .runtime import data_root


class EventInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    event_id: str = Field(min_length=1, max_length=256)
    timestamp: str = Field(min_length=1, max_length=64)
    asset_id: str = Field(min_length=1, max_length=256)
    modality: str = Field(min_length=1, max_length=32)
    kind: str = Field(min_length=1, max_length=64)
    source_id: str = Field(min_length=1, max_length=256)
    evidence_ref: str = Field(min_length=1, max_length=2048)
    summary: str = Field(min_length=1, max_length=4000)
    # Preserve integer versus float JSON values so canonical replay IDs match the CLI.
    confidence: float | int = Field(ge=0, le=1)
    synthetic: bool

    @model_validator(mode='after')
    def validate_event(self):
        Event(**self.model_dump())
        return self


class ReplayInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    events: list[EventInput] = Field(max_length=service.MAX_EVENTS)
    engine: Literal['fixed', 'episode'] = 'episode'
    window_seconds: float = Field(default=90, gt=0, le=3600, allow_inf_nan=False)
    quiet_gap_seconds: float = Field(default=20, gt=0, le=3600, allow_inf_nan=False)


class ExplainInput(ReplayInput):
    incident_id: str = Field(min_length=1, max_length=1024)
    scope: Literal['first', 'final'] = 'first'


class BenchmarkInput(ReplayInput):
    events: list[EventInput] = Field(max_length=service.MAX_BENCHMARK_EVENTS)
    labels: dict


class BodyLimitMiddleware:
    """Cap chunked bodies too; no reliance on user-supplied Content-Length."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            chunk = message.get('body', b'')
            size += len(chunk)
            if size > service.MAX_BYTES:
                return await JSONResponse({'detail': 'Request exceeds the 2 MiB limit'}, status_code=413)(scope, receive, send)
            chunks.append(chunk)
            if not message.get('more_body', False):
                break
        body = b''.join(chunks)
        consumed = False

        async def bounded_receive():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {'type': 'http.request', 'body': body, 'more_body': False}
            return await receive()
        await self.app(scope, bounded_receive, send)


bearer=HTTPBearer(auto_error=False)


def api_user(request: Request,credentials: HTTPAuthorizationCredentials | None=Depends(bearer)):
    try:
        if credentials is None or credentials.scheme.lower()!='bearer':raise PermissionError()
        return Accounts(request.app.state.accounts_path).require(credentials.credentials,'read')
    except PermissionError:raise HTTPException(401,'Sign in with a valid bearer token',headers={'WWW-Authenticate':'Bearer'}) from None


class LoginInput(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    username: str=Field(min_length=1,max_length=64)
    password: str=Field(max_length=256)


def login(body: LoginInput,request: Request):
    try:
        accounts=Accounts(request.app.state.accounts_path)
        token=accounts.login(body.username,body.password)
        return JSONResponse({'access_token':token,'token_type':'bearer','expires_in':28800,'user':accounts.identity(token)},headers={'Cache-Control':'no-store'})
    except PermissionError:raise HTTPException(401,'Sign-in failed') from None


def logout(request: Request,credentials: HTTPAuthorizationCredentials=Depends(bearer),user=Depends(api_user)):
    Accounts(request.app.state.accounts_path).logout(credentials.credentials)
    return {'signed_out':True}


async def invalid_value(request: Request, exc: ValueError):
    return JSONResponse(status_code=422, content={'detail': str(exc)})


def health():
    return {'status': 'ok', 'version': __version__, 'mode': 'authenticated_local_review', 'live_model_calls': 0}


def contract():
    return {'event_fields': sorted(service.EVENT_FIELDS), 'max_request_bytes': service.MAX_BYTES,
            'max_events': service.MAX_EVENTS, 'max_benchmark_events': service.MAX_BENCHMARK_EVENTS,
            'engines': ['fixed', 'episode'], 'explanation_mode': 'offline_deterministic_catalogue',
            'evidence_policy': 'References are metadata; no path reads or URL fetching.'}


def run(body):
    return service.run_events([e.model_dump() for e in body.events], body.engine, body.window_seconds, body.quiet_gap_seconds)


def replay_events(body: ReplayInput):
    return run(body)


def explain_incident(body: ExplainInput):
    bundle = run(body)
    reports = bundle['explanations'] if body.scope == 'first' else service.explanations(bundle['replay'], 'final')
    for report in reports:
        if report['incident_id'] == body.incident_id:
            return report
    raise HTTPException(status_code=404, detail='Incident ID is not present in this replay and scope')


def benchmark(body: BenchmarkInput):
    try:
        return service.benchmark_events([e.model_dump() for e in body.events], body.labels, body.window_seconds, body.quiet_gap_seconds)
    except (KeyError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=422, detail='Malformed incident-label contract') from exc


def export(body: ReplayInput):
    return Response(service.export_run(run(body)), media_type='application/zip',
                    headers={'Content-Disposition': 'attachment; filename="opsfusion-review.zip"'})


def create_app(accounts_path=None,project_root=None):
    app=FastAPI(title='OpsFusion authenticated local review API',version=__version__,
                description='Bearer authentication for event review and registered-model sensor/controller ingestion. Local installation; keep loopback binding. HTTP inputs cannot choose file paths or load arbitrary models.')
    app.state.accounts_path=Path(accounts_path) if accounts_path else (Path(project_root) if project_root else data_root())/'runs/accounts.sqlite3'
    app.state.project_root=Path(project_root) if project_root else (app.state.accounts_path.parent.parent if app.state.accounts_path.parent.name=='runs' else app.state.accounts_path.parent)
    app.add_middleware(BodyLimitMiddleware);app.add_exception_handler(ValueError,invalid_value)
    @app.exception_handler(RequestValidationError)
    async def validation_error(request,exc):
        if request.url.path.startswith('/auth/'):
            return JSONResponse({'detail':'Invalid authentication request'},status_code=422,headers={'Cache-Control':'no-store'})
        from fastapi.exception_handlers import request_validation_exception_handler
        return await request_validation_exception_handler(request,exc)
    app.add_api_route('/health',health,methods=['GET'])
    app.add_api_route('/auth/login',login,methods=['POST'])
    app.add_api_route('/auth/logout',logout,methods=['POST'])
    for path,endpoint,method in [('/v1/contract',contract,'GET'),('/v1/replay',replay_events,'POST'),
                                 ('/v1/explain',explain_incident,'POST'),('/v1/benchmark',benchmark,'POST'),('/v1/export',export,'POST')]:
        app.add_api_route(path,endpoint,methods=[method],dependencies=[Depends(api_user)])
    from .stream_api import install
    install(app,api_user,bearer)
    from .watch_api import install as install_watches
    install_watches(app,api_user,bearer)
    from .access import install as install_access
    install_access(app)
    return app


app=create_app()
