import { CharacterCards } from './CharacterCards'
import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, post, copyText, type Asset, type Detail, type Character } from '../api'
import { Button } from './ui/button'

type Run = (task: () => Promise<unknown>, message?: string) => Promise<void>
type Member = { id: string; username: string; active: boolean }
type Link = { id: string; expires_at: string; revoked: boolean; expired: boolean }

export function OwnerSelect({ change, label = '修改上传者' }: { change: (id: string) => void; label?: string }) {
  const users = useQuery({ queryKey: ['users'], queryFn: () => api<Member[]>('/users') })
  return <select aria-label={label} value="" onChange={e => e.target.value && change(e.target.value)}><option value="">{label}</option>{users.data?.filter(u => u.active).map(u => <option value={u.id} key={u.id}>{u.username}</option>)}</select>
}

export function PrivacyPanel({ asset, userId, superadmin, run }: { asset: Asset; userId: string; superadmin: boolean; run: Run }) {
  const owns = superadmin || asset.uploader_id === userId
  const [days, setDays] = useState(7), [url, setUrl] = useState('')
  const links = useQuery({ queryKey: ['shares', asset.id], queryFn: () => api<Link[]>(`/assets/${asset.id}/shares`), enabled: owns })
  return <section className="privacy-panel"><p>上传者：<strong>{asset.uploader}</strong> · <span className="badge">{asset.is_public ? '已公开到公共空间' : '私人图片'}</span></p>
    {owns && <><div className="row"><Button variant="outline" size="sm" onClick={() => run(async () => { await post('/assets/bulk', { asset_ids: [asset.id], action: asset.is_public ? 'unpublish' : 'publish' }); setUrl('') }, asset.is_public ? '已取消公开，并撤销旧分享链接' : '已公开到公共空间')}>{asset.is_public ? '取消公开' : '公开到公共空间'}</Button>{superadmin && <OwnerSelect change={id => run(() => post('/assets/bulk', { asset_ids: [asset.id], action: 'uploader', value: id }), '上传者已修改，旧分享链接已撤销')} />}</div>
    {!asset.trashed && <><h3>限时分享图链</h3><p className="muted">持有链接的人无需登录即可查看图片、生成参数和附件，并复制、下载。不会公开私人备注或评分。请使用访问者能连接的本站地址。</p><div className="row"><select aria-label="分享有效期" value={days} onChange={e => setDays(Number(e.target.value))}>{[1, 7, 30].map(n => <option key={n} value={n}>{n} 天</option>)}</select><Button variant="outline" size="sm" onClick={() => run(async () => { const result = await post<{ path: string }>(`/assets/${asset.id}/shares`, { days }); setUrl(new URL(result.path, location.origin).href) }, '分享链接已生成，请复制保存')}>生成分享图链</Button></div></>}
    {url && <div className="form-stack"><input aria-label="分享图链" readOnly value={url} /><Button size="sm" onClick={() => run(() => copyText(url), '链接已复制')}>复制图链</Button></div>}
    {links.data?.map(link => <div className="row between" key={link.id}><small>{new Date(link.expires_at).toLocaleString()} 到期 · {link.revoked ? '已撤销' : link.expired ? '已过期' : '有效'}</small>{!link.revoked && !link.expired && <Button variant="ghost" size="sm" onClick={() => run(async () => { await api(`/assets/${asset.id}/shares/${link.id}`, { method: 'DELETE' }); setUrl('') }, '链接已撤销')}>撤销</Button>}</div>)}</>}
  </section>
}

type Shared = Omit<Detail, 'notes' | 'rating' | 'favorite' | 'review' | 'files'> & {
  expires_at: string;
  prompt_groups: { groups: { id: string; name: string; color: string; source: string; tokens: { text: string }[] }[] };
  attachments: { id: string; url: string; filename: string; caption: string; character_index: number | null }[];
}

export function SharedPage({ token }: { token: string }) {
  const result = useQuery({ queryKey: ['shared', token], queryFn: () => api<Shared>(`/shared/${encodeURIComponent(token)}`), refetchInterval: 30000, retry: false })
  const [message, setMessage] = useState('')
  const [clock, setClock] = useState(Date.now())
  // Stop displaying data after expiration even if a network refresh fails.
  useEffect(() => { const timer = window.setInterval(() => setClock(Date.now()), 1000); return () => clearInterval(timer) }, [])
  const asset = result.data
  const copy = async (value: unknown) => { try { await copyText(typeof value === 'string' ? value : JSON.stringify(value, null, 2)); setMessage('已复制') } catch (e) { setMessage((e as Error).message) } }
  if (result.isPending) return <main className="shared-page">正在读取分享图片…</main>
  if (result.error || !asset || new Date(asset.expires_at).getTime() <= clock) return <main className="shared-page"><h1>分享链接不可用</h1><p>{result.error?.message || '分享链接已过期，请联系上传者重新分享。'}</p></main>
  return <main className="shared-page"><header><p className="eyebrow">GENMEDIA VAULT · 只读分享</p><h1>{asset.filename}</h1><p className="muted">上传者 {asset.uploader} · {asset.width} × {asset.height} · {new Date(asset.expires_at).toLocaleString()} 到期</p><a className="button button-primary" href={`${asset.original}?download=true`}>下载原图片</a></header>
    <div className="shared-layout"><div><img className="shared-original" src={asset.original} alt={asset.filename} /></div><div>
      {['prompt', 'negative'].map(field => <section className="panel" key={field}><div className="row between"><h2>{field === 'prompt' ? '正向提示词' : '负向提示词'}</h2><Button size="sm" variant="outline" onClick={() => copy(asset.generation[field] || '')}>复制</Button></div><p className="prompt-text">{String(asset.generation[field] || '无')}</p></section>)}
      {Array.isArray(asset.generation.characters) && asset.generation.characters.length > 0 && <section className="panel" aria-label="角色信息"><h2>角色信息</h2><CharacterCards characters={asset.generation.characters as Character[]} copy={copy} /></section>}
      <section className="panel"><h2>提示词分组</h2>{asset.prompt_groups.groups.map(group => <div key={group.id} className="share-group" style={{ borderColor: group.color }}><div className="row between"><strong style={{ color: group.color }}>{group.name}</strong><Button size="sm" variant="ghost" onClick={() => copy(group.tokens.map(t => t.text).join(', '))}>复制分组</Button></div><small>{group.source}</small><p>{group.tokens.map(t => t.text).join(', ')}</p></div>)}</section>
      <section className="panel"><h2>标签</h2><div className="tag-cloud">{asset.tags.map(tag => <span className="tag" key={tag}>{tag}</span>)}</div><Button variant="ghost" onClick={() => copy(asset.tags.join(', '))}>复制标签</Button></section>
      <section className="panel"><h2>完整生成信息</h2><Button variant="outline" onClick={() => copy(asset.generation)}>复制完整参数</Button><pre>{JSON.stringify(asset.generation, null, 2)}</pre></section>
      {!!asset.attachments.length && <section className="panel"><h2>参考图附件</h2>{asset.attachments.map(a => <figure key={a.id}><img className="shared-reference" src={a.url} alt={a.filename} /><figcaption>{a.caption || a.filename}{a.character_index !== null ? ` · 角色 ${a.character_index + 1}` : ''}</figcaption><a href={a.url} download={a.filename}>下载附件</a></figure>)}</section>}
      <details className="panel"><summary>原始元数据、冲突及文件信息</summary><Button variant="outline" onClick={() => copy(asset)}>复制全部图片信息</Button><pre>{JSON.stringify({ sha256: asset.sha256, file_size: asset.file_size, imported_at: asset.imported_at, raw: asset.raw, conflicts: asset.conflicts, warnings: asset.warnings, tokens: asset.tokens }, null, 2)}</pre></details>
    </div></div>{message && <p role="status" className="toast">{message}</p>}</main>
}
