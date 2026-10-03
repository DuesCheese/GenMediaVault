"""Fixed-repository Compose updater. No HTTP listener; only this service owns the Docker socket."""
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from uuid import UUID

from releases import latest_release, release_files, version_tuple

CONTROL = Path('/control')
WORKSPACE = Path('/workspace')


def atomic(name, value):
    temporary = CONTROL / (name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    os.replace(temporary, CONTROL / name)


def state(phase, message, **extra):
    atomic('status.json', {'phase': phase, 'message': message, **extra})


def docker(*args, timeout=3600, **kwargs):
    # Never expose Docker configuration or credentials in API status/errors.
    return subprocess.run(['docker', *args], check=True, timeout=timeout,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs).stdout


def inspect(container):
    return json.loads(docker('inspect', container, timeout=30))[0]


def update(request):
    request['id'] = str(UUID(request['id']))
    own = inspect(os.environ['HOSTNAME'])
    project = own['Config']['Labels']['com.docker.compose.project']
    ids = docker('ps', '-aq', '--filter', f'label=com.docker.compose.project={project}',
                 '--filter', 'label=com.docker.compose.service=app').decode().split()
    if len(ids) != 1:
        raise ValueError('无法唯一定位当前应用容器')
    app = inspect(ids[0])
    old_images = {}
    for service in ('app', 'worker'):
        container = docker('ps', '-aq', '--filter', f'label=com.docker.compose.project={project}',
                           '--filter', f'label=com.docker.compose.service={service}').decode().strip()
        old_images[service] = inspect(container)['Image']
    imports = next(m['Source'] for m in app['Mounts'] if m['Destination'] == '/imports')
    state('downloading', '正在从 GitHub 下载正式发布包')
    release = latest_release()
    if version_tuple(release['version']) <= version_tuple(request['current']):
        state('completed', '当前已经是最新版本')
        return
    files = release_files(release)
    target = CONTROL / 'updates' / request['id']
    source, backup = target / 'source', target / 'previous'
    source.mkdir(parents=True)
    backup.mkdir()
    for name, content in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    state('building', '正在构建新版本，当前服务继续运行', version=release['version'])
    image = f'genmedia-update:{request["id"]}'
    docker('build', '-t', image, str(source))
    env = dict(os.environ, GMV_IMPORT_ROOT=imports)
    # Use the original project name and host import mount, never a container-relative host path.
    compose = ['compose', '-p', project, '--project-directory', str(WORKSPACE),
               '--env-file', str(WORKSPACE / '.env'), '-f', str(WORKSPACE / 'compose.yaml')]
    override = target / 'images.json'
    override.write_text(json.dumps({'services': {s: {'image': image} for s in old_images}}))
    stopped, changed, dumped = False, [], False
    try:
        state('backing_up', '正在停止写入并备份数据库，服务将短暂不可用')
        docker(*compose, 'stop', 'app', 'worker', env=env)
        stopped = True
        db_id = docker('ps', '-q', '--filter', f'label=com.docker.compose.project={project}',
                       '--filter', 'label=com.docker.compose.service=postgres').decode().strip()
        with (target / 'database.dump').open('wb') as stream:
            subprocess.run(['docker', 'exec', db_id, 'pg_dump', '-U', 'genmedia', '-d', 'genmedia', '-Fc'],
                           stdout=stream, stderr=subprocess.PIPE, check=True, timeout=3600)
        dumped = True
        # Preserve .env and runtime directories; save every replaced file for recovery.
        for name in files:
            if name == 'GenMediaVault.exe':
                # Windows locks a running launcher; stage its replacement separately.
                shutil.copyfile(source / name, WORKSPACE / 'GenMediaVault.update.exe')
                continue
            dest = WORKSPACE / name
            if dest.is_symlink() or not dest.resolve().is_relative_to(WORKSPACE.resolve()):
                raise ValueError('项目目录包含越界链接')
            if dest.exists():
                previous = backup / name
                previous.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(dest, previous)
            changed.append(name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, dest)
        state('deploying', '正在运行数据库迁移并启动新版本')
        docker(*compose, '-f', str(override), 'up', '-d', '--no-build', '--no-deps', '--wait',
               '--wait-timeout', '300', 'app', 'worker', env=env)
        state('completed', '更新完成，请刷新页面。升级前数据库及源码已保留。', version=release['version'], backup=str(target))
    except Exception:
        if stopped:
            state('rolling_back', '更新未成功，正在恢复升级前数据库与应用')
            docker(*compose, '-f', str(override), 'stop', 'app', 'worker', env=env)
            if dumped:
                with (target / 'database.dump').open('rb') as stream:
                    docker('exec', '-i', db_id, 'pg_restore', '-U', 'genmedia', '-d', 'genmedia',
                           '--clean', '--if-exists', '--exit-on-error', stdin=stream)
            for name in reversed(changed):
                dest, previous = WORKSPACE / name, backup / name
                if previous.exists():
                    shutil.copyfile(previous, dest)
                else:
                    dest.unlink(missing_ok=True)
            override.write_text(json.dumps({'services': {s: {'image': old_images[s]} for s in old_images}}))
            docker(*compose, '-f', str(override), 'up', '-d', '--no-build', '--no-deps', '--wait',
                   '--wait-timeout', '300', 'app', 'worker', env=env)
            state('failed', '新版本启动失败，已恢复升级前数据库和应用。可检查 updater 日志后重试。', backup=str(target))
        raise


def main():
    CONTROL.mkdir(exist_ok=True)
    os.chmod(CONTROL, 0o777)
    # A controller restart during deployment needs human recovery, never replay destructive migration steps.
    if (CONTROL / 'request.json').exists():
        state('failed', '更新服务在任务中途重启。请检查 updates 中的备份并按升级文档恢复后重试。')
        (CONTROL / 'request.json').rename(CONTROL / f'interrupted-{int(time.time())}.json')
    elif (CONTROL / 'status.json').exists():
        previous = json.loads((CONTROL / 'status.json').read_text())
        if previous.get('phase') not in ('idle', 'completed', 'failed'):
            state('failed', '上次更新未正常结束，请检查备份和应用状态后再重试。')
    (CONTROL / 'request.lock').unlink(missing_ok=True)
    def heartbeat():
        while True:
            atomic('heartbeat.json', {'time': time.time()})
            time.sleep(5)
    threading.Thread(target=heartbeat, daemon=True).start()
    while True:
        path = CONTROL / 'request.json'
        if path.exists():
            try:
                request = json.loads(path.read_text())
                update(request)
            except Exception as exc:
                previous = json.loads((CONTROL / 'status.json').read_text()) if (CONTROL / 'status.json').exists() else {}
                if previous.get('phase') != 'failed':
                    state('failed', '更新未完成，请检查服务器网络、磁盘及 updater 日志；升级备份保留在控制卷 updates 目录。')
                diagnostic = (getattr(exc, 'stderr', b'') or b'').decode('utf-8', errors='replace')[-4000:]
                if (WORKSPACE / '.env').exists():
                    for line in (WORKSPACE / '.env').read_text(encoding='utf-8-sig').splitlines():
                        key, _, value = line.partition('=')
                        secret = value.strip().strip('"\'')
                        if ('PASSWORD' in key or 'TOKEN' in key) and secret:
                            diagnostic = diagnostic.replace(secret, '[REDACTED]')
                print(type(exc).__name__ + ': ' + diagnostic, flush=True)
            finally:
                path.unlink(missing_ok=True)
                (CONTROL / 'request.lock').unlink(missing_ok=True)
        time.sleep(2)


if __name__ == '__main__':
    main()
