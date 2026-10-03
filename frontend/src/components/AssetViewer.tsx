import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight, Copy, Download, Heart, Star, Tag, Trash2 } from 'lucide-react'
import { api, copyText, post, type Asset, type Detail, type Character } from '../api'
import { sizeLabel } from '../lib/utils'
import { Button } from './ui/button'
import { PromptGroups } from './PromptGroups'
import { PrivacyPanel } from './Privacy'
import { References } from './References'
import { Modal } from './ui/dialog'

type Props = { userId: string; superadmin: boolean; id: string | null; assets: Asset[]; admin: boolean; close: () => void; navigate: (id: string) => void; run: (task: () => Promise<unknown>, message?: string) => Promise<void>; search: (q: string) => void }
export function AssetViewer({ userId, superadmin, id, assets, admin, close, navigate, run, search }: Props) {
  const [tab, setTab] = useState('generation')
  const [notes, setNotes] = useState('')
  const [tag, setTag] = useState('')
  const detail = useQuery({ queryKey: ['detail', id], queryFn: () => api<Detail>(`/assets/${id}`), enabled: !!id })
  const asset = detail.data
  const characters = (asset?.generation.characters || []) as Character[]
  const index = assets.findIndex(a => a.id === id)
  const personal = (body: unknown) => run(() => api(`/assets/${id}/personal`, { method: 'PATCH', body: JSON.stringify(body) }))
  useEffect(() => { setNotes(asset?.notes || '') }, [asset?.notes, id])
  useEffect(() => {
    if (!id) return
    const keydown = (event: KeyboardEvent) => {
      if ((event.target as HTMLElement).closest('input,textarea,select,[contenteditable=true]') || event.ctrlKey || event.metaKey || event.altKey) return
      if (event.key === 'ArrowRight' && assets[index + 1]) navigate(assets[index + 1].id)
      if (event.key === 'ArrowLeft' && assets[index - 1]) navigate(assets[index - 1].id)
      if (!asset) return
      if (event.key.toLowerCase() === 'f') void personal({ favorite: !asset.favorite })
      if (/^[1-5]$/.test(event.key)) void personal({ rating: Number(event.key) })
      const reviews: Record<string, string> = { k: 'keep', m: 'maybe', r: 'reject' }
      if (reviews[event.key.toLowerCase()]) void personal({ review: reviews[event.key.toLowerCase()] })
      if (event.key.toLowerCase() === 'c') void run(() => copyText(String(asset.generation.prompt || '')), '提示词已复制')
    }
    window.addEventListener('keydown', keydown)
    return () => window.removeEventListener('keydown', keydown)
  })
  const copy = (value: unknown) => run(() => copyText(typeof value === 'string' ? value : JSON.stringify(value, null, 2)), '已复制')
  return <Modal open={!!id} onOpenChange={open => !open && close()} title={asset?.filename || '资产详情'} description="← → 切换 · F 收藏 · 1–5 评分 · K/M/R 筛选 · C 复制提示词" wide>
    {detail.isPending ? <div className="loading">正在读取元信息…</div> : detail.error ? <div role="alert" className="error-box">{detail.error.message}</div> : asset && <div className="viewer-layout">
      <div className="viewer-image"><img src={asset.original} alt={asset.filename} /><div className="viewer-navigation"><Button variant="outline" size="icon" aria-label="上一张" disabled={!assets[index - 1]} onClick={() => navigate(assets[index - 1].id)}><ChevronLeft size={20} /></Button><span>{index >= 0 ? `${index + 1} / ${assets.length}` : '资产预览'}</span><Button variant="outline" size="icon" aria-label="下一张" disabled={!assets[index + 1]} onClick={() => navigate(assets[index + 1].id)}><ChevronRight size={20} /></Button></div></div>
      <div className="viewer-info"><div className="row between"><span className="badge">{asset.generator}</span><span className="muted">{asset.width} × {asset.height} · {sizeLabel(asset.file_size)}</span></div>
        <PrivacyPanel key={asset.id} asset={asset} userId={userId} superadmin={superadmin} run={run} /><div className="viewer-actions"><Button variant={asset.favorite ? 'default' : 'outline'} size="sm" onClick={() => personal({ favorite: !asset.favorite })}><Heart size={15} fill={asset.favorite ? 'currentColor' : 'none'} />收藏</Button><div className="stars">{[1, 2, 3, 4, 5].map(n => <button key={n} aria-label={`评分 ${n}`} onClick={() => personal({ rating: asset.rating === n ? null : n })}><Star size={19} fill={(asset.rating || 0) >= n ? 'currentColor' : 'none'} /></button>)}</div><a className="icon-button" href={`${asset.original}?download=true`} aria-label="下载原图"><Download size={17} /></a></div>
        <select aria-label="我的筛选状态" value={asset.review} onChange={e => personal({ review: e.target.value })}><option value="unreviewed">未筛选</option><option value="keep">保留</option><option value="maybe">待定</option><option value="reject">淘汰</option></select>
        {!!asset.warnings.length && <div className="warning-box">{asset.warnings.map((w, i) => <p key={i}>{w}</p>)}</div>}
        <div className="tabs">{[['generation', '生成参数'], ['tokens', '标签'], ['references', '参考图'], ['raw', '原始信息'], ['file', '文件']].map(([value, label]) => <button key={value} className={tab === value ? 'active' : ''} onClick={() => setTab(value)}>{label}</button>)}</div>
        {tab === 'generation' && <><div className="prompt-heading">正向提示词 <button className="text-button" onClick={() => copy(asset.generation.prompt || '')}><Copy size={13} />复制</button></div><div className="prompt-text">{String(asset.generation.prompt || '未发现正向提示词')}</div><div className="prompt-heading">负向提示词 <button className="text-button" onClick={() => copy(asset.generation.negative || '')}><Copy size={13} />复制</button></div><div className="prompt-text subdued">{String(asset.generation.negative || '无')}</div>
          {characters.map(character => <section className="character-card" key={character.index}><div className="row between"><h3>{character.name}</h3><span className="badge">{character.enabled ? '启用' : '停用'}</span></div><div className="prompt-heading">角色正向提示词<button className="text-button" onClick={() => copy(character.prompt)}><Copy size={13} />复制</button></div><div className="prompt-text">{character.prompt || '无'}</div><div className="prompt-heading">角色负向提示词<button className="text-button" onClick={() => copy(character.negative)}><Copy size={13} />复制</button></div><div className="prompt-text subdued">{character.negative || '无'}</div><p className="muted">位置：{JSON.stringify(character.centers)}</p></section>)}
          <dl className="metadata-grid">{[['model', '模型'], ['seed', 'Seed'], ['steps', '步数'], ['cfg', 'CFG'], ['sampler', '采样器'], ['scheduler', '调度器']].map(([field, label]) => <div key={field}><dt>{label}</dt><dd><button title="点击搜索此参数" onClick={() => { if (asset.generation[field] != null) { search(`${field}:${JSON.stringify(String(asset.generation[field]))}`); close() } }}>{String(asset.generation[field] ?? '—')}</button></dd></div>)}</dl>
          <Button variant="outline" onClick={() => copy(asset.generation)}><Copy size={15} />复制完整生成参数</Button>
          {!!asset.generation.workflow && <div className="row"><Button variant="ghost" size="sm" onClick={() => run(async () => { const response = await fetch(`/api/v1/assets/${id}/workflow`); if (!response.ok) throw new Error('工作流读取失败'); await copyText(await response.text()) }, '工作流已复制')}>复制工作流</Button><a href={`/api/v1/assets/${id}/workflow`} className="text-button">下载工作流</a></div>}
          {!!asset.conflicts.length && <details className="warning-box"><summary>{asset.conflicts.length} 项元信息冲突</summary><pre>{JSON.stringify(asset.conflicts, null, 2)}</pre></details>}
        </>}
        {tab === 'tokens' && <><p className="eyebrow">共享标签</p><div className="tag-cloud">{asset.tags.map(t => <span key={t} className="tag"><button onClick={() => { search(`tag:${JSON.stringify(t)}`); close() }}>{t}</button>{asset.tag_sources.some(item => item.name === t && item.source === 'user') && <button aria-label={`移除标签 ${t}`} onClick={() => run(() => post('/assets/bulk', { asset_ids: [id], action: 'remove_tag', value: t }), '已移除此图片的人工标签')}><Trash2 size={12} /></button>}</span>)}</div><form className="row" onSubmit={e => { e.preventDefault(); void run(() => post('/assets/bulk', { asset_ids: [id], action: 'add_tag', value: tag }), '标签已添加'); setTag('') }}><input aria-label="新标签" placeholder="hair:white" value={tag} onChange={e => setTag(e.target.value)} /><Button variant="outline" size="icon" aria-label="添加标签" disabled={!tag}><Tag size={15} /></Button></form><p className="muted">移除按钮仅解除当前图片的人工标签；管理员可在「标签探索」中全局删除。</p><PromptGroups key={asset.id} id={asset.id} run={run} /></>}
        {tab === 'references' && <References key={asset.id} id={asset.id} characters={characters} run={run} />}
        {tab === 'raw' && <><Button variant="outline" size="sm" onClick={() => copy(asset.raw)}>复制原始信息</Button>{asset.raw.map(raw => <details key={raw.id}><summary>{raw.parser} v{raw.parser_version}</summary><pre>{JSON.stringify(raw.data, null, 2)}</pre></details>)}</>}
        {tab === 'file' && <><dl className="file-info"><dt>SHA-256</dt><dd>{asset.sha256}</dd><dt>导入时间</dt><dd>{new Date(asset.imported_at).toLocaleString()}</dd>{asset.files.map((file, i) => <div key={i}><dt>{file.present ? '原文件路径' : '文件缺失'}</dt><dd>{file.path}</dd></div>)}</dl>{admin && <div className="row"><Button variant="outline" size="sm" onClick={() => run(() => post('/assets/bulk', { asset_ids: [id], action: 'reparse' }), '重新解析任务已创建')}>重新解析</Button><Button variant="destructive" size="sm" onClick={() => run(async () => { await post('/assets/bulk', { asset_ids: [id], action: asset.trashed ? 'restore' : 'trash' }); close() }, asset.trashed ? '已恢复' : '已移入回收站')}><Trash2 size={14} />{asset.trashed ? '恢复' : '回收站'}</Button></div>}</>}
        <div className="notes"><label htmlFor="personal-notes">私人备注</label><textarea id="personal-notes" rows={3} value={notes} onChange={e => setNotes(e.target.value)} placeholder="记下这次生成的灵感与调整…" /><Button variant="ghost" size="sm" disabled={notes === asset.notes} onClick={() => personal({ notes })}>保存备注</Button></div>
      </div></div>}
  </Modal>
}
