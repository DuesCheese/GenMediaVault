import { ChevronDown } from 'lucide-react'

export function SpaceSwitcher({ view, superadmin, change }: { view: string; superadmin: boolean; change: (view: string) => void }) {
  const spaces = [{ value: 'library', label: '全部空间' }, { value: 'private', label: '我的私人空间' }, { value: 'public', label: '公共空间' }, ...(superadmin ? [{ value: 'all_private', label: '所有私人空间' }] : [])]
  const current = spaces.find(space => space.value === view) || spaces[0]
  return <div className="workspace workspace-switcher">
    <span className="workspace-icon">G</span>
    <div><strong>创作档案</strong><small><span className="online-dot" />{current.label}</small></div>
    <ChevronDown size={15} aria-hidden="true" />
    <select aria-label="切换空间" value={current.value} onChange={event => change(event.target.value)}>
      {spaces.map(space => <option key={space.value} value={space.value}>{space.label}</option>)}
    </select>
  </div>
}
