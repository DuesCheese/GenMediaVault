import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Download } from 'lucide-react'
import { api, post, type Job } from '../api'
import { Button } from './ui/button'
import { Modal } from './ui/dialog'
import { sizeLabel } from '../lib/utils'
type Run = (task: () => Promise<unknown>, message?: string) => Promise<void>
type Backup = Job & { label: string; result: Job['result'] & { size?: number; warnings?: string[]; snapshot_at?: string }; progress: Job['progress'] & { phase?: string } }
function download(url: string) { const link = document.createElement('a'); link.href = url; link.download = ''; document.body.appendChild(link); link.click(); link.remove() }
export function ExportDialog({ ids, open, close, run }: { ids: string[]; open: boolean; close: () => void; run: Run }) {
  const [metadata, setMetadata] = useState(false), [attachments, setAttachments] = useState(true), [jobId, setJobId] = useState('')
  const downloaded = useRef('')
  const job = useQuery({ queryKey: ['download-job', jobId], queryFn: () => api<Job>(`/jobs/${jobId}`), enabled: !!jobId && open, refetchInterval: query => ['completed', 'failed', 'cancelled'].includes(query.state.data?.status || '') ? false : 1000 })
  useEffect(() => { if (job.data?.status === 'completed' && job.data.result.download && downloaded.current !== jobId) { downloaded.current = jobId; download(job.data.result.download) } }, [job.data, jobId])
  const busy = !!jobId && !['completed', 'failed', 'cancelled'].includes(job.data?.status || '')
  return <Modal open={open} onOpenChange={value => !value && close()} title="批量下载图片" description="后台打包为 ZIP，完成后自动下载；也可以稍后从后台任务下载。"><div className="form-stack"><p>已选择 {ids.length} 张图片</p><label className="checkbox-label"><input type="checkbox" checked={metadata} onChange={e => setMetadata(e.target.checked)} disabled={busy} />同时包含生成参数、提示词分组和我的备注</label><label className="checkbox-label"><input type="checkbox" checked={attachments} onChange={e => setAttachments(e.target.checked)} disabled={busy} />同时包含动作参考图附件</label><Button disabled={!ids.length || busy || ids.length > 1000} onClick={() => run(async () => { const response = await post<{ job_id: string }>('/exports', { asset_ids: ids, include_metadata: metadata, include_attachments: attachments }); setJobId(response.job_id) })}><Download size={16} />{busy ? '正在打包…' : '打包并下载 ZIP'}</Button>{job.data?.progress.total != null && <progress max={job.data.progress.total || 1} value={job.data.status === 'completed' ? job.data.progress.total : job.data.progress.completed || 0} />}{(job.error || job.data?.error) && <p role="alert" className="error-box">{job.error?.message || job.data?.error}</p>}{job.data?.status === 'completed' && <a className="button button-outline" href={job.data.result.download}>下载已生成的 ZIP</a>}<p className="muted">单次最多 1000 张。源图缺失会在压缩包内的 manifest.json 中注明。</p></div></Modal>
}

export function BackupsPanel({ run }: { run: Run }) {
  const [label, setLabel] = useState(''), [indexed, setIndexed] = useState(true), [waiting, setWaiting] = useState('')
  const downloaded = useRef('')
  const query = useQuery({ queryKey: ['backups'], queryFn: () => api<Backup[]>('/backups'), refetchInterval: 2000 })
  const active = query.data?.some(job => ['pending', 'running'].includes(job.status))
  useEffect(() => { const job = query.data?.find(item => item.id === waiting); if (job?.status === 'completed' && job.result.download && downloaded.current !== job.id) { downloaded.current = job.id; download(job.result.download) } }, [query.data, waiting])
  return <div className="panel backup-panel"><h3>系统备份 <span className="badge">仅管理员</span></h3><p className="muted">按时间点保存完整数据库、托管原图、参考附件及缓存。打包期间工作空间暂时只读，完成后自动下载。</p><label>备份备注<input aria-label="备份备注" maxLength={120} placeholder="例如：整理完成、升级前" value={label} onChange={e => setLabel(e.target.value)} /></label><label className="checkbox-label"><input type="checkbox" checked={indexed} onChange={e => setIndexed(e.target.checked)} />同时备份索引库原图</label><p className="muted">不嵌套历史备份包；部署 .env 另行保管。备份含所有成员的私人数据，仅管理员可下载。</p><Button disabled={active} onClick={() => run(async () => { const result = await post<{ job_id: string }>('/backups', { label, include_indexed: indexed }); setWaiting(result.job_id) }, '系统备份已开始，完成后自动下载')}><Download size={15} />创建备份并下载</Button>{query.error && <p className="error-box">{query.error.message}</p>}
    <div className="backup-history">{query.data?.map(job => <article key={job.id}><div className="row between"><strong>{job.label || '完整备份'}</strong><small>{new Date(job.created_at).toLocaleString()}</small></div><p className="muted">{({ pending: '等待中', running: job.progress.phase || '正在备份', completed: '已完成', failed: '失败', cancelled: '已取消' } as Record<string, string>)[job.status]} {job.result.size ? `· ${sizeLabel(job.result.size)}` : ''}</p>{job.error && <p className="error-box">{job.error}</p>}{!!job.result.warnings?.length && <details className="warning-box"><summary>{job.result.warnings.length} 个源文件缺失，详见清单</summary><pre>{job.result.warnings.join('\n')}</pre></details>}<div className="row">{job.status === 'completed' && job.result.download && <a className="text-button" href={job.result.download}>下载备份 ZIP</a>}{['failed', 'cancelled'].includes(job.status) && <Button variant="ghost" size="sm" onClick={() => run(() => post(`/jobs/${job.id}/retry`), '已重新排队')}>重试</Button>}{['pending', 'running'].includes(job.status) && <Button variant="ghost" size="sm" onClick={() => run(() => post(`/jobs/${job.id}/cancel`), '已取消备份')}>取消</Button>}</div></article>)}</div>
  </div>
}
