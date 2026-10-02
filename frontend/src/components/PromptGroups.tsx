import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowDown, ArrowUp, Copy, Plus, X } from 'lucide-react'
import { api, copyText, post, type components } from '../api'
import { Button } from './ui/button'

type Token = { key: string; text: string }
type Group = { id: string; name: string; color: string; source: string; tokens: Token[] }
type Layout = { revision: number; groups: Group[]; sources: { id: string; label: string; text: string; tokens: Token[] }[] }
type Run = (task: () => Promise<unknown>, message?: string) => Promise<void>
const colors = ['#d3b888', '#8fbcbb', '#b48ead', '#a3be8c', '#ebcb8b', '#bf616a']
function groupId() {
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128
  const hex = Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

export function PromptGroups({ id, run }: { id: string; run: Run }) {
  const client = useQueryClient()
  const query = useQuery({ queryKey: ['prompt-groups', id], queryFn: () => api<Layout>(`/assets/${id}/prompt-groups`) })
  const [groups, setGroups] = useState<Group[]>([]), [revision, setRevision] = useState(0)
  const [dirty, setDirty] = useState(false), [source, setSource] = useState('base:positive')
  const [selected, setSelected] = useState(new Set<string>()), [copyGroups, setCopyGroups] = useState(new Set<string>())
  const [target, setTarget] = useState('')
  const [translated, setTranslated] = useState(false)
  const texts = Array.from(new Set([...(query.data?.sources.flatMap(item => item.tokens.map(token => token.text)) || []), ...groups.flatMap(group => group.tokens.map(token => token.text))])).filter(text => text.length <= 10000)
  const translations = useQuery({ queryKey: ['prompt-translations', texts], enabled: translated && !!query.data,
    queryFn: async () => {
      const batches: string[][] = [[]]; let length = 0
      for (const text of texts) { if (batches.at(-1)!.length >= 500 || length + text.length > 150000) { batches.push([]); length = 0 }; batches.at(-1)!.push(text); length += text.length }
      const responses = await Promise.all(batches.map(texts => post<components['schemas']['LookupOut']>('/translations/lookup', { texts })))
      return Object.fromEntries(responses.flatMap(response => response.items).map(item => [item.text, item]))
    } })
  const label = (text: string) => translated ? translations.data?.[text]?.translated || text : text
  const tokenTitle = (text: string) => [text, ...(translated ? translations.data?.[text]?.groups || [] : [])].join(' · ')
  useEffect(() => { if (query.data && !dirty) { setGroups(query.data.groups); setRevision(query.data.revision) } }, [query.data, dirty])
  const change = (next: Group[]) => { setGroups(next); setDirty(true) }
  const visible = groups.filter(group => group.source === source)
  const tokens = query.data?.sources.find(item => item.id === source)?.tokens || []
  const toggle = (key: string) => { const next = new Set(selected); if (next.has(key)) next.delete(key); else next.add(key); setSelected(next) }
  const assign = (groupId: string) => {
    if (!visible.some(group => group.id === groupId)) return
    const chosen = tokens.filter(token => selected.has(token.key))
    change(groups.map(group => ({ ...group, tokens: [...group.tokens.filter(token => !selected.has(token.key)), ...(group.id === groupId ? chosen : [])] })))
    setSelected(new Set())
  }
  const create = () => {
    const group = { id: groupId(), name: `分组 ${visible.length + 1}`, color: colors[groups.length % colors.length], source, tokens: tokens.filter(token => selected.has(token.key)) }
    change([...groups.map(item => ({ ...item, tokens: item.tokens.filter(token => !selected.has(token.key)) })), group]); setSelected(new Set()); setTarget(group.id)
  }
  const copy = (items: Group[]) => run(() => copyText(items.flatMap(group => group.tokens.map(token => token.text)).join(', ')), '分组提示词已复制')
  const move = (id: string, direction: number) => { const index = groups.findIndex(group => group.id === id), next = [...groups]; if (next[index + direction]) { [next[index], next[index + direction]] = [next[index + direction], next[index]]; change(next) } }
  if (query.error) return <p role="alert" className="error-box">{query.error.message}</p>
  if (!query.data) return <p className="muted">正在读取提示词分组…</p>
  return <section className="prompt-organizer"><div className="row between"><h3>提示词分组</h3><span className="badge">共享 · {dirty ? '未保存' : '已保存'}</span></div><p className="muted">选中提示词后分组；颜色和复制顺序会随图片保存，保留原文权重。</p>
    <div className="translation-toggle"><label className="checkbox-label"><input type="checkbox" role="switch" aria-label="提示词翻译" checked={translated} onChange={event => setTranslated(event.target.checked)} />提示词翻译{translated && translations.isFetching ? ' · 正在查表…' : ''}</label><p className="muted">{translated ? '命中对照表时显示中文；悬停查看原文和分类。未命中保留原文。' : '开启后使用共享 Tag 对照表翻译。'}复制始终保留英文原文及权重。</p></div>
    {translated && translations.error && <p role="alert" className="error-box">翻译读取失败，暂时显示原文：{translations.error.message}</p>}
    {dirty && query.data.revision !== revision && <div role="alert" className="warning-box">其他成员已更新分组。<Button variant="ghost" size="sm" onClick={() => setDirty(false)}>放弃本地修改并刷新</Button></div>}
    <select aria-label="提示词来源" value={source} onChange={e => { setSource(e.target.value); setSelected(new Set()); setTarget('') }}>{query.data.sources.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}{groups.filter(group => !query.data.sources.some(item => item.id === group.source)).map(group => <option key={group.id} value={group.source}>保留的分组 · {group.source}</option>)}</select>
    <div className="tag-cloud token-picker">{tokens.map(token => { const group = groups.find(item => item.tokens.some(entry => entry.key === token.key)); return <button key={token.key} className={`tag ${selected.has(token.key) ? 'token-selected' : ''}`} style={{ borderColor: group?.color }} aria-pressed={selected.has(token.key)} onClick={() => toggle(token.key)} title={`${tokenTitle(token.text)}${group ? ` · 分组：${group.name}` : ''}`}>{label(token.text)}</button> })}{!tokens.length && <span className="muted">此来源没有提示词。</span>}</div>
    <div className="row wrap"><Button variant="outline" size="sm" disabled={groups.length >= 32} onClick={create}><Plus size={14} />新建分组{selected.size ? `（${selected.size}）` : ''}</Button><select aria-label="目标提示词分组" value={target} onChange={e => setTarget(e.target.value)}><option value="">选择已有分组</option>{visible.map(group => <option key={group.id} value={group.id}>{group.name}</option>)}</select><Button size="sm" variant="ghost" disabled={!target || !selected.size} onClick={() => assign(target)}>移入分组</Button></div>
    {visible.map(group => <div className="prompt-group" style={{ borderLeftColor: group.color }} key={group.id}><div className="row"><input aria-label={`复制组 ${group.name}`} type="checkbox" checked={copyGroups.has(group.id)} onChange={e => { const next = new Set(copyGroups); if (e.target.checked) next.add(group.id); else next.delete(group.id); setCopyGroups(next) }} /><input aria-label="分组名称" value={group.name} maxLength={80} onChange={e => change(groups.map(item => item.id === group.id ? { ...item, name: e.target.value } : item))} /><input aria-label={`分组颜色 ${group.name}`} type="color" value={group.color} onChange={e => change(groups.map(item => item.id === group.id ? { ...item, color: e.target.value } : item))} /></div>
      <div className="tag-cloud">{group.tokens.map(token => <span className="tag" style={{ color: group.color }} key={token.key} title={tokenTitle(token.text)}>{label(token.text)}<button aria-label={`移出 ${token.text}`} onClick={() => change(groups.map(item => item.id === group.id ? { ...item, tokens: item.tokens.filter(entry => entry.key !== token.key) } : item))}><X size={12} /></button></span>)}</div><div className="row wrap"><Button variant="ghost" size="sm" onClick={() => copy([group])}><Copy size={13} />复制本组</Button><button className="icon-button" aria-label={`上移 ${group.name}`} onClick={() => move(group.id, -1)}><ArrowUp size={14} /></button><button className="icon-button" aria-label={`下移 ${group.name}`} onClick={() => move(group.id, 1)}><ArrowDown size={14} /></button><Button variant="ghost" size="sm" onClick={() => change(groups.filter(item => item.id !== group.id))}>删除分组</Button></div></div>)}
    <div className="row wrap"><Button disabled={!dirty || groups.some(group => !group.name.trim())} onClick={() => run(async () => { const saved = await api<{ revision: number }>(`/assets/${id}/prompt-groups`, { method: 'PUT', body: JSON.stringify({ revision, groups }) }); client.setQueryData(['prompt-groups', id], { ...query.data, groups, revision: saved.revision }); setRevision(saved.revision); setDirty(false) }, '提示词分组已保存')}>保存分组</Button><Button variant="outline" disabled={!visible.some(group => copyGroups.has(group.id))} onClick={() => copy(visible.filter(group => copyGroups.has(group.id)))}>复制选中分组</Button></div>
  </section>
}
