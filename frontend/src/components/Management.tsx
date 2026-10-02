import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowUpRight, Check, FileImage, FolderPlus, LoaderCircle, Plus, RefreshCw, UploadCloud, X } from 'lucide-react'
import { api, post, type AST, type Collection, type Job, type Library } from '../api'
import { Button } from './ui/button'
import { BackupsPanel } from './Downloads'
import { Modal } from './ui/dialog'
import { SearchBuilder, emptyCondition } from './SearchBuilder'

type Run = (task: () => Promise<unknown>, message?: string) => Promise<void>
const statusNames: Record<string, string> = { pending: '等待中', running: '处理中', completed: '已完成', failed: '失败', cancelled: '已取消' }
const kindNames: Record<string, string> = { import: '上传导入', scan: '目录扫描', reparse: '重新解析', export: '导出文件', classify: '自动分类', backup: '系统备份' }

export function ImportView({ libraries, admin, run }: { libraries: Library[]; admin: boolean; run: Run }) {
  const managed = libraries.filter(l => l.mode === 'managed')
  const [library, setLibrary] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState('')
  const upload = () => run(async () => {
    setBusy(true)
    try {
      // Keep files from a folder together so identical names in different folders cannot collide.
      const groups = new Map<string, File[]>()
      for (const file of files) {
        const folder = file.webkitRelativePath?.split('/').slice(0, -1).join('/') || ''
        groups.set(folder, [...(groups.get(folder) || []), file])
      }
      let done = 0
      for (const group of groups.values()) {
        const images = group.filter(f => /\.(png|jpe?g|webp)$/i.test(f.name))
        for (let i = 0; i < images.length; i += 50) {
          const batch = images.slice(i, i + 50)
          const attached = new Set(batch)
          for (const image of batch) {
            const stem = image.name.replace(/\.[^.]+$/, '')
            for (const sidecar of group) if ([image.name + '.json', image.name + '.txt', stem + '.json', stem + '.txt'].includes(sidecar.name)) attached.add(sidecar)
          }
          if (attached.size > 200) throw new Error('当前批次 Sidecar 过多，请减少文件后重试')
          const body = new FormData(); body.set('library_id', library || managed[0]?.id || '')
          attached.forEach(f => body.append('files', f))
          await api('/imports', { method: 'POST', body }); done += batch.length; setProgress(`已提交 ${done} 张图片`)
        }
      }
      if (!done) throw new Error('请至少选择一张 PNG、JPEG 或 WebP 图片')
      setFiles([])
    } finally { setBusy(false) }
  }, '导入任务已提交，可在任务页面查看进度')
  return <div className="content-narrow"><div className="section-intro"><p className="eyebrow">COLLECT YOUR CREATIONS</p><h2>把灵感留在这里</h2><p>上传图片及其 Sidecar，自动提取生成参数、建立索引并整理标签。</p></div>
    <div className="panel"><label>目标托管库<select aria-label="目标托管库" value={library || managed[0]?.id || ''} onChange={e => setLibrary(e.target.value)}>{!managed.length && <option>请先由管理员创建托管库</option>}{managed.map(l => <option value={l.id} key={l.id}>{l.name}</option>)}</select></label>
      <div className="drop-zone" onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); if (!busy) setFiles(Array.from(e.dataTransfer.files)) }}><UploadCloud size={44} strokeWidth={1.1} /><h3>拖入图片，开始归档</h3><p>PNG、JPEG、WebP · 单张最多 64 MB · 支持 JSON / TXT Sidecar</p><div className="row"><label className="button button-primary file-label">选择文件<input aria-label="选择上传文件" type="file" multiple disabled={busy} accept=".png,.jpg,.jpeg,.webp,.json,.txt" onChange={e => setFiles(Array.from(e.target.files || []))} /></label><label className="button button-outline file-label">选择文件夹<input aria-label="选择上传文件夹" type="file" multiple disabled={busy} ref={el => { el?.setAttribute('webkitdirectory', '') }} onChange={e => setFiles(Array.from(e.target.files || []))} /></label></div></div>
      {!!files.length && <div className="upload-summary"><FileImage size={20} /><span>{files.length} 个文件已就绪</span><Button disabled={busy || !managed.length} onClick={upload}>{busy ? <LoaderCircle className="spin" size={15} /> : <Check size={15} />}提交导入</Button><Button variant="ghost" size="icon" aria-label="清空选择" disabled={busy} onClick={() => setFiles([])}><X size={16} /></Button></div>}{progress && <p className="muted">{progress}</p>}</div>
    <h3 className="section-title">已连接的媒体库 <span>{libraries.length}</span></h3><div className="library-list">{libraries.map(library => <div className="panel row between" key={library.id}><div><h3><FolderPlus size={16} />{library.name}</h3><p className="muted">{library.mode === 'managed' ? '托管库 · 上传原件' : `索引库 · ${library.watch_enabled ? '自动监控' : '手动扫描'}`} · {library.count.toLocaleString()} 张图片</p>{library.root_path && <code>{library.root_path}</code>}</div>{library.mode === 'indexed' && admin && <Button variant="outline" size="sm" onClick={() => run(() => post(`/libraries/${library.id}/scan`), '扫描任务已创建')}><RefreshCw size={14} />扫描</Button>}</div>)}</div>
  </div>
}

export function JobsView({ jobs, run, admin, userId }: { jobs: Job[]; run: Run; admin: boolean; userId: string }) {
  return <div className="content-narrow"><div className="section-intro"><p className="eyebrow">BACKGROUND ACTIVITY</p><h2>每一步，都有记录</h2><p>任务在后台持续执行。失败项目可以重试，成功导入的图片会被保留。</p></div>{!jobs.length && <div className="empty-state"><Check size={36} /><h3>当前没有后台任务</h3><p>上传文件或扫描目录后，进度会显示在这里。</p></div>}{jobs.map(job => <div className="panel job-card" key={job.id}><div className="row between"><h3>{job.status === 'running' && <LoaderCircle size={17} className="spin" />}{kindNames[job.kind] || job.kind}</h3><span className={`status status-${job.status}`}>{statusNames[job.status]}</span></div><p className="muted">{new Date(job.created_at).toLocaleString()} · {job.id.slice(0, 8)}</p>{job.progress.total != null && <progress max={job.progress.total || 1} value={job.status === 'completed' ? job.progress.total : job.progress.completed || 0} />}
      {job.progress.completed != null && <p className="muted">已处理 {job.progress.completed}{job.progress.total != null ? ` / ${job.progress.total}` : ''}</p>}{job.error && <div role="alert" className="error-box">{job.error}</div>}{(job.result.errors?.length || job.progress.errors?.length || 0) > 0 && <details><summary>查看失败文件</summary><pre>{JSON.stringify(job.result.errors || job.progress.errors, null, 2)}</pre></details>}
      <div className="row">{job.result.download && job.status === 'completed' && <a className="button button-outline" href={job.result.download}>{job.kind === 'backup' ? '下载备份包' : '下载导出包'}<ArrowUpRight size={14} /></a>}{(admin || userId === job.requested_by) && (['failed', 'cancelled'].includes(job.status) ? <Button variant="outline" size="sm" onClick={() => run(() => post(`/jobs/${job.id}/retry`), '任务已重新排队')}>重试</Button> : ['pending', 'running'].includes(job.status) && <Button variant="ghost" size="sm" onClick={() => run(() => post(`/jobs/${job.id}/cancel`), '已请求取消')}>取消任务</Button>)}</div></div>)}</div>
}

type Rule = { id: string; name: string; condition: AST; actions: { type: 'add_tag' | 'add_collection'; value: string }[]; priority: number; enabled: boolean }
export function RulesView({ run, collections }: { run: Run; collections: Collection[] }) {
  const rules = useQuery({ queryKey: ['rules'], queryFn: () => api<Rule[]>('/rules') })
  const [editing, setEditing] = useState<Rule | null>(null)
  const save = () => run(async () => { if (!editing) return; const { id, ...body } = editing; await api(id ? `/rules/${id}` : '/rules', { method: id ? 'PATCH' : 'POST', body: JSON.stringify(body) }); setEditing(null) }, '规则已保存；可重新分类现有图片')
  return <div className="content-narrow"><div className="section-intro row between"><div><p className="eyebrow">AUTOMATIC ORGANIZATION</p><h2>让整理自动发生</h2><p>规则按优先级执行，仅添加共享标签和集合，保留人工整理结果。</p></div><Button onClick={() => setEditing({ id: '', name: '', condition: { version: 1, root: { type: 'and', children: [emptyCondition()] } }, actions: [{ type: 'add_tag', value: '' }], priority: 0, enabled: true })}><Plus size={15} />创建规则</Button></div>
    <div className="panel row between"><div><h3>内置分类</h3><p className="muted">生成器 · 横竖构图 · 发色 · 眼睛 · 服装 · 场景 · 光线</p></div><Button variant="outline" onClick={() => run(() => post('/rules/apply'), '已创建全库分类任务')}><RefreshCw size={15} />重新分类</Button></div>{rules.error && <p className="error-box">{rules.error.message}</p>}
    {rules.data?.map(rule => <div className="panel row between" key={rule.id}><div><h3>{rule.name}</h3><p className="muted">优先级 {rule.priority} · {rule.enabled ? '已启用' : '已停用'}</p><div className="tag-cloud">{rule.actions.map((a, i) => <span key={i} className="tag">{a.type === 'add_tag' ? a.value : '加入集合'}</span>)}</div></div><div className="row"><Button variant="outline" size="sm" onClick={() => setEditing(structuredClone(rule))}>编辑</Button><Button variant="ghost" size="sm" onClick={() => run(() => api(`/rules/${rule.id}`, { method: 'DELETE' }), '规则已删除')}>删除</Button></div></div>)}
    <Modal open={!!editing} onOpenChange={open => !open && setEditing(null)} title="自动分类规则" description="共享规则不能包含个人收藏或评分条件。">{editing && <form onSubmit={e => { e.preventDefault(); void save() }} className="form-stack"><label>规则名称<input required value={editing.name} onChange={e => setEditing({ ...editing, name: e.target.value })} /></label><SearchBuilder value={editing.condition} onChange={condition => setEditing({ ...editing, condition })} />
      {editing.actions.map((action, index) => <div className="row" key={index}><select aria-label="规则动作" value={action.type} onChange={e => setEditing({ ...editing, actions: editing.actions.map((a, i) => i === index ? { type: e.target.value as 'add_tag' | 'add_collection', value: '' } : a) })}><option value="add_tag">添加标签</option><option value="add_collection">加入集合</option></select>{action.type === 'add_tag' ? <input required aria-label="规则标签" placeholder="hair:white" value={action.value} onChange={e => setEditing({ ...editing, actions: editing.actions.map((a, i) => i === index ? { ...a, value: e.target.value } : a) })} /> : <select required aria-label="规则集合" value={action.value} onChange={e => setEditing({ ...editing, actions: editing.actions.map((a, i) => i === index ? { ...a, value: e.target.value } : a) })}><option value="">选择集合</option>{collections.filter(c => c.kind === 'static').map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select>}</div>)}
      <Button variant="ghost" type="button" onClick={() => setEditing({ ...editing, actions: [...editing.actions, { type: 'add_tag', value: '' }] })}>添加动作</Button><label>优先级<input type="number" min="0" max="1000" value={editing.priority} onChange={e => setEditing({ ...editing, priority: Number(e.target.value) })} /></label><label className="checkbox-label"><input type="checkbox" checked={editing.enabled} onChange={e => setEditing({ ...editing, enabled: e.target.checked })} />启用规则</label><Button type="submit">保存规则</Button></form>}</Modal>
  </div>
}

export function SettingsView({ run, libraries, admin }: { run: Run; libraries: Library[]; admin: boolean }) {
  const [libraryForm, setLibraryForm] = useState<{ name: string; mode: string; root_path: string; watch_enabled: boolean }>({ name: '', mode: 'managed', root_path: '', watch_enabled: true })
  const [userForm, setUserForm] = useState({ username: '', password: '', role: 'user' })
  const [password, setPassword] = useState({ old_password: '', password: '' })
  const users = useQuery({ queryKey: ['users'], queryFn: () => api<{ id: string; username: string; role: string; active: boolean }[]>('/users'), enabled: admin })
  const system = useQuery({ queryKey: ['system'], queryFn: () => api<{ import_roots: string[]; parser_version: string; parsers: string[] }>('/system'), enabled: admin })
  return <div className="content-narrow"><div className="section-intro"><p className="eyebrow">YOUR WORKSPACE</p><h2>工作空间设置</h2><p>媒体库在成员间共享，收藏、评分与备注仅属于你。</p></div>
    {admin && <><BackupsPanel run={run} /><div className="panel"><h3>添加媒体库</h3><form className="form-stack" onSubmit={e => { e.preventDefault(); void run(async () => { await post('/libraries', libraryForm); setLibraryForm({ ...libraryForm, name: '' }) }, '媒体库已创建') }}><label>名称<input required value={libraryForm.name} onChange={e => setLibraryForm({ ...libraryForm, name: e.target.value })} /></label><label>存储方式<select value={libraryForm.mode} onChange={e => setLibraryForm({ ...libraryForm, mode: e.target.value })}><option value="managed">托管库 — 保存上传原件</option><option value="indexed">索引库 — 读取现有目录</option></select></label>{libraryForm.mode === 'indexed' && <><label>容器内目录<input required placeholder={system.data?.import_roots[0] || '/imports'} value={libraryForm.root_path} onChange={e => setLibraryForm({ ...libraryForm, root_path: e.target.value })} /></label><p className="muted">允许范围：{system.data?.import_roots.join('、') || '未配置导入挂载'}</p><label className="checkbox-label"><input type="checkbox" checked={libraryForm.watch_enabled} onChange={e => setLibraryForm({ ...libraryForm, watch_enabled: e.target.checked })} />自动监控新文件</label></>}<Button type="submit"><FolderPlus size={15} />添加媒体库</Button></form></div>
      {libraries.filter(l => l.mode === 'indexed').map(library => <div className="panel row between" key={library.id}><span>{library.name}</span><label className="checkbox-label"><input type="checkbox" checked={library.watch_enabled} onChange={e => run(() => api(`/libraries/${library.id}`, { method: 'PATCH', body: JSON.stringify({ name: library.name, watch_enabled: e.target.checked }) }))} />自动监控</label></div>)}
      <div className="panel"><h3>成员管理</h3><div className="member-list">{users.data?.map(user => <div className="row between" key={user.id}><span>{user.username}<small className="muted"> · {user.role === 'admin' ? '管理员' : '普通成员'}</small></span><Button size="sm" variant="ghost" onClick={() => run(() => api(`/users/${user.id}`, { method: 'PATCH', body: JSON.stringify({ active: !user.active }) }), '账号状态已更新')}>{user.active ? '停用' : '启用'}</Button></div>)}</div><form className="form-stack" onSubmit={e => { e.preventDefault(); void run(async () => { await post('/users', userForm); setUserForm({ username: '', password: '', role: 'user' }) }, '账号已创建') }}><label>用户名<input required value={userForm.username} onChange={e => setUserForm({ ...userForm, username: e.target.value })} /></label><label>初始密码<input required type="password" minLength={12} autoComplete="new-password" value={userForm.password} onChange={e => setUserForm({ ...userForm, password: e.target.value })} /></label><select aria-label="账号角色" value={userForm.role} onChange={e => setUserForm({ ...userForm, role: e.target.value })}><option value="user">普通成员</option><option value="admin">管理员</option></select><Button type="submit"><Plus size={15} />创建账号</Button></form></div>
      <div className="panel"><h3>元信息解析器 <span className="badge">v{system.data?.parser_version}</span></h3><div className="tag-cloud">{system.data?.parsers.map(p => <span className="tag" key={p}>{p}</span>)}</div><p className="muted">原始信息保留，解析后的字段可以重新生成。原文件缺失时，新增提取能力无法运行。</p><Button variant="outline" onClick={() => run(() => post('/maintenance/reparse-novelai'), 'NovelAI 重新解析任务已创建')}>更新已有 NovelAI 角色信息</Button></div></>}
    <div className="panel"><h3>修改我的密码</h3><form className="form-stack" onSubmit={e => { e.preventDefault(); void run(async () => { await post('/auth/password', password); window.dispatchEvent(new Event('session-expired')) }, '密码已修改，请重新登录') }}><label>原密码<input type="password" required autoComplete="current-password" value={password.old_password} onChange={e => setPassword({ ...password, old_password: e.target.value })} /></label><label>新密码<input type="password" minLength={12} required autoComplete="new-password" value={password.password} onChange={e => setPassword({ ...password, password: e.target.value })} /></label><Button type="submit">修改密码</Button></form></div>
  </div>
}
