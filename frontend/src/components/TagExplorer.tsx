import { useEffect, useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { ArrowUpRight, Copy, Globe2, Plus, Trash2 } from 'lucide-react'
import { api, copyText, type components } from '../api'
import { Button } from './ui/button'
import { Modal } from './ui/dialog'

type Group = components['schemas']['GroupOut']
type Members = components['schemas']['MembersOut']
type Run = (task: () => Promise<unknown>, message?: string) => Promise<void>

export function TagExplorer({ admin, run, search }: { admin: boolean; run: Run; search: (q: string) => void }) {
  const [filter, setFilter] = useState(''), [query, setQuery] = useState(''), [active, setActive] = useState('')
  const [tagFilter, setTagFilter] = useState(''), [tagQuery, setTagQuery] = useState(''), [page, setPage] = useState(1)
  const [groupForm, setGroupForm] = useState<Partial<Group> | null>(null)
  const [memberForm, setMemberForm] = useState<{ tag: string; previous_key?: string; revision: number } | null>(null)
  const [legacyFilter, setLegacyFilter] = useState('')
  const [error, setError] = useState(''), [busy, setBusy] = useState(false)
  useEffect(() => { const timer = setTimeout(() => setQuery(filter), 250); return () => clearTimeout(timer) }, [filter])
  useEffect(() => { const timer = setTimeout(() => { setTagQuery(tagFilter); setPage(1) }, 250); return () => clearTimeout(timer) }, [tagFilter])
  const groups = useQuery({ queryKey: ['tag-groups', query], queryFn: () => api<Group[]>(`/tag-groups?q=${encodeURIComponent(query)}`) })
  const members = useQuery({ queryKey: ['global-group-tags', active, tagQuery, page], enabled: !!active, placeholderData: keepPreviousData,
    queryFn: () => api<Members>(`/tag-groups/${active}/tags?${new URLSearchParams({ q: tagQuery, page: String(page) })}`) })
  const tags = useQuery({ queryKey: ['tags'], queryFn: () => api<{ id: string; name: string; namespace: string; count: number }[]>('/tags') })
  const current = members.data?.group.id === active ? members.data.group : undefined
  useEffect(() => { if (members.data && !members.isPlaceholderData) setPage(value => Math.min(value, Math.max(1, Math.ceil(members.data!.total / 50)))) }, [members.data, members.isPlaceholderData])
  const selectGroup = (id: string) => { setActive(id); setTagFilter(''); setTagQuery(''); setPage(1) }
  const save = async () => {
    setBusy(true); setError('')
    try {
      if (groupForm) {
        const body = { name: groupForm.name, color: groupForm.color, ...(groupForm.id ? { revision: groupForm.revision } : {}) }
        const saved = await api<Group>(groupForm.id ? `/tag-groups/${groupForm.id}` : '/tag-groups', { method: groupForm.id ? 'PUT' : 'POST', body: JSON.stringify(body) })
        selectGroup(saved.id); setGroupForm(null)
      } else if (memberForm && current) {
        await api(`/tag-groups/${current.id}/tags`, { method: 'PUT', body: JSON.stringify(memberForm) }); setMemberForm(null)
      }
      await run(async () => {}, '全局分组已保存，图片分组同步更新')
    } catch (exception) { setError((exception as Error).message) } finally { setBusy(false) }
  }
  return <div className="content-narrow global-explorer"><div className="section-intro row between"><div><p className="eyebrow">EXPLORE BY TAG GROUPS</p><h2><Globe2 size={23} />标签探索</h2><p>全局分组共享维护，图片中的相同 Tag 自动归组。支持按组名、中文翻译或英文 Tag 查找。</p></div><Button onClick={() => { setGroupForm({ name: '', color: '#8fbcbb' }); setError('') }}><Plus size={14} />新建全局组</Button></div>
    <input aria-label="搜索全局分组" placeholder="搜索组名、中文或英文 Tag…" value={filter} onChange={e => setFilter(e.target.value)} />
    {groups.error && <p role="alert" className="error-box">{groups.error.message}</p>}
    <div className="global-explorer-layout"><nav className="global-group-nav" aria-label="全局分组列表">{groups.data?.map(group => <button className={active === group.id ? 'active' : ''} key={group.id} onClick={() => selectGroup(group.id)}><span className="group-color-dot" style={{ background: group.color }} /><span>{group.name}</span><small>{group.count}</small></button>)}{groups.data?.length === 0 && <p className="muted">没有匹配的分组。可从 Tag 翻译页同步，或将图片分组设为全局组。</p>}</nav>
      <section className="global-group-detail">{!active ? <div className="empty-state"><Globe2 size={32} /><h3>选择一个全局分组</h3><p>查看、添加和整理组内 Tag。修改会应用于所有图片。</p></div> : <>
        {members.error && <p role="alert" className="error-box">{members.error.message}</p>}
        {current && <><div className="row between wrap"><div><h3 style={{ color: current.color }}>{current.name}</h3><span className="muted">{current.count} 个 Tag</span></div><div className="row wrap"><Button size="sm" variant="outline" onClick={() => { setGroupForm(current); setError('') }}>编辑全局组</Button>{admin && <Button size="sm" variant="ghost" onClick={() => { if (confirm(`删除全局组「${current.name}」及其 Tag 关系？图片原始提示词和普通人工分组保留。`)) void run(async () => { await api(`/tag-groups/${current.id}?revision=${current.revision}`, { method: 'DELETE' }); setActive('') }, '全局组已删除') }}><Trash2 size={13} />删除全局组</Button>}</div></div>
          <div className="row global-member-filter"><input aria-label="搜索组内 Tag" placeholder="组内搜索中文或英文…" value={tagFilter} onChange={e => setTagFilter(e.target.value)} /><Button size="sm" onClick={() => { setMemberForm({ tag: '', revision: current.revision }); setError('') }}><Plus size={13} />添加 Tag</Button></div>
          <div className="translation-list">{members.data?.items.map(item => <article className="global-member" key={item.key}><div><strong>{item.tag}</strong>{item.translation && <p className="muted">{item.translation}</p>}</div><div className="row wrap"><button className="icon-button" aria-label={`复制 Tag ${item.tag}`} onClick={() => run(() => copyText(item.tag), '英文 Tag 已复制')}><Copy size={13} /></button><Button variant="ghost" size="sm" aria-label={`搜索 Tag 图片 ${item.tag}`} onClick={() => search(`prompt.tag:${JSON.stringify(item.key)} OR negative.tag:${JSON.stringify(item.key)}`)}>找图片<ArrowUpRight size={13} /></Button><Button variant="ghost" size="sm" aria-label={`编辑全局 Tag ${item.tag}`} onClick={() => { setMemberForm({ tag: item.tag, previous_key: item.key, revision: current.revision }); setError('') }}>编辑</Button><Button variant="ghost" size="icon" aria-label={`删除全局 Tag ${item.tag}`} onClick={() => { if (confirm(`从全局组「${current.name}」移除「${item.tag}」？所有图片将同步取消这项归组，原始提示词保留。`)) void run(() => api(`/tag-groups/${current.id}/tags?${new URLSearchParams({ key: item.key, revision: String(current.revision) })}`, { method: 'DELETE' }), 'Tag 已从全局组移除') }}><Trash2 size={13} /></Button></div></article>)}</div>
          {members.data?.total === 0 && <p className="muted">没有匹配的 Tag。</p>}
          <div className="row between translation-pagination"><Button variant="outline" size="sm" disabled={page === 1 || members.isFetching} onClick={() => setPage(page - 1)}>上一页</Button><span className="muted">{page} / {Math.max(1, Math.ceil((members.data?.total || 0) / 50))}</span><Button variant="outline" size="sm" disabled={page * 50 >= (members.data?.total || 0) || members.isFetching} onClick={() => setPage(page + 1)}>下一页</Button></div>
        </>}
      </>}</section></div>
    <details className="legacy-tags"><summary>图片共享标签 · {tags.data?.length || 0}</summary><p className="muted">生成器、自动分类及图片手工标签。</p><input aria-label="筛选标签" placeholder="筛选共享标签…" value={legacyFilter} onChange={e => setLegacyFilter(e.target.value)} /><div className="tag-explorer">{tags.data?.filter(tag => tag.name.includes(legacyFilter)).map(tag => <div className="panel tag-explorer-card" key={tag.id}><button className="tag-explorer-search" onClick={() => search(`tag:${JSON.stringify(tag.name)}`)}><span className="eyebrow">{tag.namespace || '通用'}</span><strong>{tag.name}</strong><span className="muted">{tag.count} 张图片 <ArrowUpRight size={14} /></span></button>{admin && <Button variant="ghost" size="sm" aria-label={`删除共享标签 ${tag.name}`} onClick={() => { if (confirm(`删除共享标签「${tag.name}」？将从 ${tag.count} 张图片移除，原图保留。`)) void run(() => api(`/tags/${tag.id}`, { method: 'DELETE' }), '共享标签已删除') }}><Trash2 size={13} />删除</Button>}</div>)}</div></details>
    <Modal open={!!groupForm || !!memberForm} onOpenChange={open => { if (!open && !busy) { setGroupForm(null); setMemberForm(null) } }} title={groupForm ? (groupForm.id ? '编辑全局组' : '新建全局组') : (memberForm?.previous_key ? '编辑全局 Tag' : '添加 Tag')} description="修改会同步到其他图片的全局分组，原始提示词保留。"><form className="form-stack" onSubmit={e => { e.preventDefault(); void save() }}>{groupForm ? <><label>全局组名称<input aria-label="全局组名称" required maxLength={120} value={groupForm.name} onChange={e => setGroupForm({ ...groupForm, name: e.target.value })} /></label><label>全局组颜色<input aria-label="全局组颜色" type="color" value={groupForm.color} onChange={e => setGroupForm({ ...groupForm, color: e.target.value })} /></label></> : memberForm && <label>英文 Tag<input aria-label="全局英文 Tag" required maxLength={10000} value={memberForm.tag} onChange={e => setMemberForm({ ...memberForm, tag: e.target.value })} /></label>}{error && <p role="alert" className="error-box">{error}</p>}<Button type="submit" disabled={busy}>{busy ? '正在保存…' : '保存'}</Button></form></Modal>
  </div>
}
