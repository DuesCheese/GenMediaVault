import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, post } from '../api'
import { Button } from './ui/button'

type Release = { version: string; current: string; available: boolean; url: string; notes: string }
export function UpdatesPanel({ run }: { run: (task: () => Promise<unknown>, message?: string) => Promise<void> }) {
  const [release, setRelease] = useState<Release | null>(null), [busy, setBusy] = useState(false)
  const status = useQuery({ queryKey: ['update-status'], queryFn: () => api<{ online: boolean; phase: string; message: string; current: string }>('/system/update'), refetchInterval: 5000 })
  const running = status.data && !['idle', 'completed', 'failed'].includes(status.data.phase)
  return <section className="panel"><div className="row between"><h3>GitHub 在线更新</h3><span className="badge">v{status.data?.current || '0.6.1'}</span></div><p className="muted">服务器从 DuesCheese/GenMediaVault 获取正式版本。更新前自动保存数据库和被替换的源码，安装期间服务会短暂重启。</p>
    <p role="status">{status.error ? '服务暂时无法连接，更新重启期间请稍候。' : status.data?.message}</p>{status.data && !status.data.online && <p className="warning-box">更新服务离线，请按部署文档启动 updater 容器。</p>}
    <div className="row"><Button variant="outline" disabled={busy || !!running} onClick={() => run(async () => { setBusy(true); try { setRelease(await api<Release>('/system/update/check')) } finally { setBusy(false) } })}>{busy ? '检查中…' : '检查更新'}</Button>{release?.available && <Button disabled={busy || !!running || !status.data?.online} onClick={() => run(async () => { setBusy(true); try { await post('/system/update') } finally { setBusy(false) } }, '更新已提交，服务恢复后请刷新页面')}>更新到 v{release.version}</Button>}</div>
    {release && <div><p>{release.available ? `发现新版本 v${release.version}` : '当前已是最新版本'} · <a href={release.url} target="_blank" rel="noreferrer">查看 GitHub 发布说明</a></p><details><summary>版本说明</summary><pre>{release.notes}</pre></details></div>}
  </section>
}
