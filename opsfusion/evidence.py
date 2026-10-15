"""Resolve restored references only within existing generated evidence trees."""
import json
from pathlib import Path, PurePosixPath
from urllib.parse import unquote,urlsplit


def restored_path(root,ref):
    root=Path(root).resolve();registry=root/'runs/restore-origins.json'
    try:
        parts=urlsplit(ref)
        if parts.scheme!='file' or parts.netloc or parts.query or parts.fragment or not registry.is_file() or registry.stat().st_size>65536:return None
        data=json.loads(registry.read_text())
        if data.get('format')!='evidence-roots-1' or not isinstance(data.get('origins'),list) or len(data['origins'])>100:return None
        path=unquote(parts.path)
        for origin in data['origins']:
            if not isinstance(origin,str):continue
            old=urlsplit(origin)
            if old.scheme!='file' or old.netloc:continue
            prefix=unquote(old.path).rstrip('/')+'/'
            if not path.startswith(prefix):continue
            suffix=path[len(prefix):];relative=PurePosixPath(suffix)
            if '\\' in suffix or '..' in relative.parts or relative.is_absolute() or len(relative.parts)<3 or relative.parts[:2] not in (('runs','live'),('runs','raw-ingestion')):continue
            candidate=(root/Path(*relative.parts)).resolve()
            try:candidate.relative_to(root/relative.parts[0]/relative.parts[1])
            except ValueError:continue
            return candidate
    except (OSError,ValueError,TypeError):pass
    return None
