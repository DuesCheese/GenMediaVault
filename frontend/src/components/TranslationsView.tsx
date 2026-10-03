import { useEffect, useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Copy, Languages, Plus, Search, Trash2 } from 'lucide-react'
import { api, copyText, post, type components } from '../api'
import { Button } from './ui/button'
import { Modal } from './ui/dialog'

type Entry = components['schemas']['TranslationOut']
type Page = components['schemas']['TranslationPage']
type Run = (task: () => Promise<unknown>, message?: string) => Promise<void>
export function TranslationsView({ admin, run }: { admin: boolean; run: Run }) {
  const [search, setSearch] = useState(''), [query, setQuery] = useState(''), [group, setGroup] = useState('')
  const [page, setPage] = useState(1), [missing, setMissing] = useState(false)
  const [editing, setEditing] = useState<Partial<Entry> | null>(null), [groupText, setGroupText] = useState('')
  const [error, setError] = useState(''), [saving, setSaving] = useState(false), [syncing, setSyncing] = useState(false), [syncResult, setSyncResult] = useState('')
  useEffect(() => { const timer = setTimeout(() => { setQuery(search); setPage(1) }, 250); return () => clearTimeout(timer) }, [search])
  const groups = useQuery({ queryKey: ['translation-groups'], queryFn: () => api<string[]>('/translations/groups') })
  const results = useQuery({ queryKey: ['translations', query, group, missing, page], placeholderData: keepPreviousData,
    queryFn: () => api<Page>(`/translations?${new URLSearchParams({ q: query, group, untranslated: String(missing), page: String(page), limit: '50' })}`) })
  const open = (item?: Entry) => { setEditing(item || { tag: '', translation: '', groups: [] }); setGroupText(item?.groups.join('\n') || ''); setError('') }
  const save = async () => {
    if (!editing) return
    setSaving(true); setError('')
    const body = { tag: editing.tag, translation: editing.translation, groups: groupText.split('\n').map(value => value.trim()).filter(Boolean), ...(editing.id ? { revision: editing.revision } : {}) }
    try {
      await api(editing.id ? `/translations/${editing.id}` : '/translations', { method: editing.id ? 'PUT' : 'POST', body: JSON.stringify(body) })
      setEditing(null); await run(async () => {}, '翻译已保存，图片中的对照同步更新')
    } catch (exception) { setError((exception as Error).message) } finally { setSaving(false) }
  }
  return <div className="content-narrow translation-page"><div className="section-intro row between"><div><p className="eyebrow">LOCAL TAG DICTIONARY</p><h2><Languages size={23} />Tag 翻译</h2><p>输入中文或部分英文查找对照，按分类浏览。{admin ? '修改后所有成员共享。' : '词表由管理员维护。'}</p></div>{admin && <div className="row wrap"><Button variant="outline" disabled={syncing} onClick={() => run(async () => { setSyncing(true); try { const result = await post<{ groups: number; added: number }>('/translations/sync-tags'); setSyncResult(`已合并 ${result.groups} 个分组，新增 ${result.added} 条 Tag 关系。可到标签探索查看。`) } finally { setSyncing(false) } }, '已同步到标签探索')}>{syncing ? '同步中…' : '同步为标签'}</Button><Button onClick={() => open()}><Plus size={15} />添加翻译</Button></div>}</div>{syncResult && <p role="status" className="muted">{syncResult}</p>}<p className="muted">同步为标签将合并全部对照及分类，不受当前筛选影响；无分类词条归入“未分组”。手工删除的 Tag 关系在再次同步时会重新加入。</p>
    <div className="translation-filters"><label className="translation-search"><Search size={16} /><input aria-label="搜索 Tag 翻译" placeholder="例如：白发、white、hair…" value={search} onChange={event => setSearch(event.target.value)} /></label><select aria-label="翻译分类筛选" value={group} onChange={event => { setGroup(event.target.value); setPage(1) }}><option value="">全部分类</option>{groups.data?.map(name => <option key={name}>{name}</option>)}</select><label className="checkbox-label"><input type="checkbox" checked={missing} onChange={event => { setMissing(event.target.checked); setPage(1) }} />只看未翻译</label></div>
    <div className="row between translation-count"><span className="muted">{results.data ? `${results.data.total.toLocaleString()} 条对照` : '正在加载…'}{results.isFetching && results.data ? ' · 查询中…' : ''}</span>{group && <Button variant="ghost" size="sm" onClick={() => { setGroup(''); setPage(1) }}>清除分类</Button>}</div>
    {results.error && <p role="alert" className="error-box">{results.error.message}</p>}
    <div className="translation-list">{results.data?.items.map(item => <article className="translation-row" key={item.id}><div className="translation-word"><strong>{item.tag}</strong><button className="icon-button" aria-label={`复制英文 ${item.tag}`} onClick={() => run(() => copyText(item.tag), '英文 Tag 已复制')}><Copy size={13} /></button></div><div className="translation-meaning"><span>{item.translation || '尚无翻译'}</span><div className="tag-cloud">{item.groups.map(name => <button className="tag" key={name} onClick={() => { setGroup(name); setPage(1) }}>{name}</button>)}</div></div>{admin && <div className="row translation-actions"><Button variant="ghost" size="sm" onClick={() => open(item)} aria-label={`编辑翻译 ${item.tag}`}>编辑</Button><Button variant="ghost" size="icon" aria-label={`删除翻译 ${item.tag}`} onClick={() => { if (confirm(`删除「${item.tag}」的中英对照？原始提示词、图片标签和人工分组不会被删除。`)) void run(() => api(`/translations/${item.id}?revision=${item.revision}`, { method: 'DELETE' }), '翻译已删除') }}><Trash2 size={14} /></Button></div>}</article>)}</div>
    {results.data?.total === 0 && <div className="empty-state"><Languages size={32} /><h3>没有匹配的翻译</h3><p>试试更短的中文或英文关键词，或清除分类条件。</p>{admin && <Button variant="outline" onClick={() => open()}>添加缺失的翻译</Button>}</div>}
    <div className="row between translation-pagination"><Button variant="outline" disabled={page === 1 || results.isFetching} onClick={() => setPage(page - 1)}>上一页</Button><span className="muted">第 {page} / {Math.max(1, Math.ceil((results.data?.total || 0) / 50))} 页</span><Button variant="outline" disabled={!results.data || page * 50 >= results.data.total || results.isFetching} onClick={() => setPage(page + 1)}>下一页</Button></div>
    <Modal open={!!editing} onOpenChange={value => !value && !saving && setEditing(null)} title={editing?.id ? '编辑 Tag 翻译' : '添加 Tag 翻译'} description="英文 Tag 的大小写、下划线与空格视为同一词条。分类关系用于查询，不覆盖图片的人工分组。">{editing && <form className="form-stack" onSubmit={event => { event.preventDefault(); void save() }}><label>英文 Tag<input aria-label="英文 Tag" required maxLength={500} value={editing.tag} onChange={event => setEditing({ ...editing, tag: event.target.value })} /></label><label>中文翻译<textarea aria-label="中文翻译" maxLength={2000} rows={3} value={editing.translation} onChange={event => setEditing({ ...editing, translation: event.target.value })} /></label><label>所属分类（每行一个）<textarea aria-label="翻译所属分类" rows={3} value={groupText} onChange={event => setGroupText(event.target.value)} /></label><select aria-label="添加已有翻译分类" value="" onChange={event => { if (event.target.value) setGroupText(Array.from(new Set([...groupText.split('\n').filter(Boolean), event.target.value])).join('\n')) }}><option value="">添加已有分类…</option>{groups.data?.map(name => <option key={name}>{name}</option>)}</select>{error && <p role="alert" className="error-box">{error}</p>}<Button type="submit" disabled={saving || !editing.tag?.trim()}>{saving ? '正在保存…' : '保存翻译'}</Button></form>}</Modal>
  </div>
}
