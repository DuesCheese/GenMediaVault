import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, type Character } from '../api'
import { Button } from './ui/button'

type Reference = { id: string; filename: string; caption: string; character_index: number | null; url: string; size: number }
type Run = (task: () => Promise<unknown>, message?: string) => Promise<void>
export function References({ id, characters, run }: { id: string; characters: Character[]; run: Run }) {
  const query = useQuery({ queryKey: ['references', id], queryFn: () => api<Reference[]>(`/assets/${id}/attachments`) })
  const [caption, setCaption] = useState(''), [character, setCharacter] = useState(''), [busy, setBusy] = useState(false)
  const upload = (files: File[]) => run(async () => { setBusy(true); try { for (const file of files) { const body = new FormData(); body.append('file', file); body.set('caption', caption); if (character !== '') body.set('character_index', character); await api(`/assets/${id}/attachments`, { method: 'POST', body }) } } finally { setBusy(false) } }, '参考图已添加')
  return <section className="reference-panel"><h3>动作参考图</h3><p className="muted">为整张图片或指定角色保存参考附件，所有成员可查看。</p><label>参考说明<input aria-label="参考图说明" maxLength={1000} value={caption} onChange={e => setCaption(e.target.value)} placeholder="例如：手势、站姿、双人动作" /></label><select aria-label="参考图关联角色" value={character} onChange={e => setCharacter(e.target.value)}><option value="">整张图片</option>{characters.map((item, i) => <option key={i} value={i}>{item.name || `角色 ${i+1}`}</option>)}</select><label className="button button-outline file-label">{busy ? '正在上传…' : '添加参考图'}<input aria-label="添加参考图" disabled={busy} type="file" multiple accept="image/png,image/jpeg,image/webp" onChange={e => { const files = Array.from(e.target.files || []); e.target.value = ''; if (files.length) void upload(files) }} /></label>
    {query.error && <p className="error-box">{query.error.message}</p>}<div className="reference-grid">{query.data?.map(item => <article className="reference-card" key={item.id}><a href={item.url} target="_blank" rel="noreferrer"><img loading="lazy" src={item.url} alt={item.caption || item.filename} /></a><strong>{item.caption || item.filename}</strong><small className="muted">{item.character_index == null ? '整张图片' : characters[item.character_index]?.name || `角色 ${item.character_index + 1}`}</small><div className="row between"><a className="text-button" href={`${item.url}?download=true`}>下载</a><Button size="sm" variant="ghost" onClick={() => { if (confirm('从这张图片移除此参考附件？')) void run(() => api(`/attachments/${item.id}`, { method: 'DELETE' }), '参考图已移除') }}>移除</Button></div></article>)}</div>
  </section>
}
