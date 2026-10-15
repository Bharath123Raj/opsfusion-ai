"""Explicit raw HTTP stream contract. No arbitrary model paths or remote fetches."""
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .stream_store import StreamStore, StreamConflict, FEATURES, MAX_INPUTS, MAX_EVENTS


class OpenStream(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    baseline_id:str=Field(pattern='^[0-9a-f]{32}$')
    asset_id:str=Field(pattern='^[A-Za-z0-9_.-]{1,120}$')
    source_id:str=Field(pattern='^[A-Za-z0-9_.-]{1,120}$')
    synthetic:bool


class InputBase(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    message_id:str=Field(pattern='^[A-Za-z0-9_.-]{1,120}$')
    timestamp:str=Field(min_length=1,max_length=64)
    asset_id:str=Field(pattern='^[A-Za-z0-9_.-]{1,120}$')
    synthetic:bool


class SensorInput(InputBase):
    vibration_rms_mm_s:float=Field(ge=0,allow_inf_nan=False)
    temperature_c:float=Field(allow_inf_nan=False)


class ControllerInput(InputBase):
    level:Literal['DEBUG','INFO','WARN','ERROR','CRITICAL']
    code:str=Field(pattern='^[A-Z0-9_]{1,64}$')
    message:str=Field(min_length=1,max_length=1000)


def install(app,api_user,bearer):
    router=APIRouter(prefix='/v1/streams',tags=['live sensor/controller streams'],dependencies=[Depends(api_user)])
    def token(credentials=Depends(bearer)):return credentials.credentials
    def store(request:Request):
        # A process-local immutable-model cache; all mutable ingestion state lives in SQLite.
        if getattr(request.app.state,'stream_store',None) is None:
            request.app.state.stream_store=StreamStore(request.app.state.project_root,request.app.state.accounts_path)
        return request.app.state.stream_store
    def call(fn,*args):
        try:return fn(*args)
        except PermissionError:raise HTTPException(403,'Your account cannot perform this stream action') from None
        except StreamConflict as exc:raise HTTPException(409,str(exc)) from None
        except RuntimeError:raise HTTPException(503,'Sensor dependencies unavailable; install requirements-sensor.txt') from None

    @router.get('/contract')
    def contract():
        return {'units':{'vibration_rms_mm_s':'RMS velocity in mm/s','temperature_c':'degrees Celsius'},
                'timestamp_policy':'UTC-normalized acquisition declaration; <=30s old / <=5s future; nondecreasing across inputs, strictly increasing within each kind',
                'authentication':'Existing local bearer session; admin/operator ingestion, viewer read-only',
                'baseline_policy':'Trusted admin registration via local CLI; API accepts IDs only',
                'max_inputs_per_stream':MAX_INPUTS,'max_events_per_stream':MAX_EVENTS,
                'synthetic_policy':'Source declaration fixed at stream creation; synthetic training propagates to derived events',
                'fusion_scope':'Same asset within one HTTP stream; opt-in /v1/watches add bounded camera/HTTP cooccurrence reports.'}

    @router.get('/baselines')
    def baselines(s=Depends(store),t=Depends(token)):return call(s.baselines,t)

    @router.get('')
    def streams(s=Depends(store),t=Depends(token)):return call(s.streams,t)

    @router.post('')
    def open_stream(body:OpenStream,s=Depends(store),t=Depends(token)):
        return call(s.open,t,body.baseline_id,body.asset_id,body.source_id,body.synthetic)

    @router.get('/{sid}')
    def state(sid:str,s=Depends(store),t=Depends(token)):return call(s.get,t,sid)

    @router.post('/{sid}/readings')
    def readings(sid:str,body:SensorInput,s=Depends(store),t=Depends(token)):return call(s.accept,t,sid,'sensor',body.model_dump())

    @router.post('/{sid}/logs')
    def logs(sid:str,body:ControllerInput,s=Depends(store),t=Depends(token)):return call(s.accept,t,sid,'log',body.model_dump())

    @router.post('/{sid}/close')
    def close(sid:str,s=Depends(store),t=Depends(token)):return call(s.close,t,sid)

    @router.get('/{sid}/export')
    def export(sid:str,s=Depends(store),t=Depends(token)):return call(s.export,t,sid)

    app.include_router(router)
