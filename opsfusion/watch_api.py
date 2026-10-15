"""Authenticated source linking; never accepts arbitrary event/media uploads."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .correlation import WatchStore


class LinkInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    camera_session: str = Field(pattern='^[0-9a-f]{32}$')
    http_stream: str = Field(pattern='^[0-9a-f]{32}$')
    horizon_seconds: float = Field(default=10., ge=1, le=30, allow_inf_nan=False)


def install(app, api_user, bearer):
    router = APIRouter(prefix='/v1/watches', dependencies=[Depends(api_user)])

    def store(request: Request):
        return WatchStore(request.app.state.project_root, request.app.state.accounts_path)

    def token(credentials=Depends(bearer)):
        return credentials.credentials

    def call(fn, *args):
        try: return fn(*args)
        except PermissionError: raise HTTPException(403, 'This account cannot perform that action') from None

    @router.get('/installation')
    def installation(s=Depends(store), t=Depends(token)):
        return {'installation_id':call(s.installation_id,t)}

    @router.get('')
    def watches(s=Depends(store), t=Depends(token)):
        return call(s.watches, t)

    @router.post('')
    def link(body: LinkInput, s=Depends(store), t=Depends(token)):
        return call(s.open, t, body.camera_session, body.http_stream, body.horizon_seconds)

    @router.get('/{wid}')
    def state(wid: str, s=Depends(store), t=Depends(token)):
        return call(s.get, t, wid)

    @router.post('/{wid}/close')
    def close(wid: str, s=Depends(store), t=Depends(token)):
        return call(s.close, t, wid)

    @router.get('/{wid}/export')
    def export(wid: str, s=Depends(store), t=Depends(token)):
        return call(s.export, t, wid)

    app.include_router(router)
