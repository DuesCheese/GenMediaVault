"""Administrators enqueue upgrades; the isolated Docker controller executes them."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException

from .config import settings
from .releases import latest_release, version_tuple
from .security import admin

router = APIRouter(prefix='/api/v1/system/update', dependencies=[Depends(admin)])
VERSION = '0.6.0'


def control():
    if not settings().update_control_dir:
        raise HTTPException(503, '在线更新服务未启用，请使用新版 Compose 启动 updater 服务')
    return Path(settings().update_control_dir)


@router.get('')
def status():
    root = control()
    try:
        heartbeat = json.loads((root / 'heartbeat.json').read_text())
        online = datetime.now(timezone.utc).timestamp() - heartbeat['time'] < 30
    except (OSError, ValueError, KeyError):
        online = False
    try:
        value = json.loads((root / 'status.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        value = {'phase': 'idle', 'message': '尚未执行更新'}
    return {**value, 'online': online, 'current': VERSION}


@router.get('/check')
def check():
    try:
        release = latest_release()
        return {**{k: v for k, v in release.items() if k != 'files'}, 'current': VERSION,
                'available': version_tuple(release['version']) > version_tuple(VERSION)}
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(502, '无法检查 GitHub 正式版本，请检查服务器网络后重试') from exc


@router.post('', status_code=202)
def start():
    root = control()
    if not status()['online']:
        raise HTTPException(503, '更新服务离线，请启动 updater 容器')
    if not check()['available']:
        raise HTTPException(409, '当前已经是最新版本')
    try:
        fd = os.open(root / 'request.lock', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise HTTPException(409, '已有更新正在处理') from exc
    os.close(fd)
    temporary = root / 'request.tmp'
    temporary.write_text(json.dumps({'id': str(uuid4()), 'current': VERSION}), encoding='utf-8')
    os.replace(temporary, root / 'request.json')
    return {'message': '更新已提交。将先构建新版本并备份数据库，服务会短暂重启。'}
