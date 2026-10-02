import { Plus, Trash2 } from 'lucide-react'
import { Button } from './ui/button'
import type { AST, Node } from '../api'

export const fieldTypes: Record<string, string> = { prompt: 'text', negative: 'text', 'prompt.tag': 'enum', 'negative.tag': 'enum', model: 'enum', generator: 'enum', tag: 'enum', 'tag.namespace': 'enum', lora: 'enum', seed: 'seed', steps: 'number', cfg: 'number', width: 'number', height: 'number', ratio: 'number', size: 'number', filename: 'text', path: 'text', extension: 'enum', sampler: 'enum', scheduler: 'enum', orientation: 'enum', imported: 'date', created: 'date', favorite: 'bool', rating: 'number', review: 'enum' }
const names: Record<string, string> = { prompt: '正向提示词', negative: '负向提示词', 'prompt.tag': '正向完整标签', model: '模型', generator: '生成器', tag: '标签', lora: 'LoRA', steps: '步数', cfg: 'CFG', rating: '我的评分', favorite: '我的收藏', width: '宽度', height: '高度', imported: '导入日期', created: '文件日期' }
const opNames: Record<string, string> = { eq: '等于', contains: '包含', prefix: '开头是', missing: '缺失', gte: '大于等于', lte: '小于等于', gt: '大于', lt: '小于', between: '范围' }
export const emptyCondition = (): Node => ({ type: 'condition', field: 'prompt', op: 'contains', value: '' })

function Expression({ node, change, remove }: { node: Node; change: (node: Node) => void; remove?: () => void }) {
  if (node.type === 'not') return <div className="filter-group"><div className="filter-group-title">排除以下条件 <Button variant="ghost" size="sm" onClick={() => change(node.child)}>取消排除</Button></div><Expression node={node.child} change={child => change({ ...node, child })} />{remove && <Button variant="ghost" size="sm" onClick={remove}>移除</Button>}</div>
  if (node.type === 'and' || node.type === 'or') return <div className="filter-group"><div className="filter-group-title"><select aria-label="条件逻辑" value={node.type} onChange={e => change({ ...node, type: e.target.value as 'and' | 'or' })}><option value="and">满足全部条件（AND）</option><option value="or">满足任一条件（OR）</option></select><Button variant="ghost" size="sm" onClick={() => change({ type: 'not', child: node })}>排除本组</Button>{remove && <Button variant="ghost" size="icon" aria-label="移除条件组" onClick={remove}><Trash2 size={14} /></Button>}</div>
    {node.children.map((child, index) => <Expression key={index} node={child} change={next => change({ ...node, children: node.children.map((n, i) => i === index ? next : n) })} remove={() => change({ ...node, children: node.children.filter((_, i) => i !== index) })} />)}
    <div className="row"><Button variant="ghost" size="sm" onClick={() => change({ ...node, children: [...node.children, emptyCondition()] })}><Plus size={14} />添加条件</Button><Button variant="ghost" size="sm" onClick={() => change({ ...node, children: [...node.children, { type: 'or', children: [emptyCondition()] }] })}>添加条件组</Button></div></div>
  if (node.type !== 'condition') return null
  const type = fieldTypes[node.field] || 'text'
  const ops = ['eq', ...(type === 'text' || type === 'enum' ? ['contains', 'prefix'] : []), ...(type === 'number' || type === 'date' ? ['gte', 'lte', 'gt', 'lt', 'between'] : []), 'missing']
  return <div className="filter-condition"><select aria-label="搜索字段" value={node.field} onChange={e => change({ ...node, field: e.target.value, op: fieldTypes[e.target.value] === 'text' ? 'contains' : 'eq', value: '' })}>{Object.keys(fieldTypes).map(field => <option value={field} key={field}>{names[field] || field}</option>)}</select>
    <select aria-label="比较方式" value={node.op} onChange={e => change({ ...node, op: e.target.value, value: e.target.value === 'between' ? ['', ''] : e.target.value === 'missing' ? null : '' })}>{ops.map(op => <option value={op} key={op}>{opNames[op]}</option>)}</select>
    {node.op !== 'missing' && (node.op === 'between' ? <div className="row"><input aria-label="范围起点" value={String((node.value as string[])?.[0] ?? '')} onChange={e => change({ ...node, value: [e.target.value, (node.value as string[])[1]] })} /><span>至</span><input aria-label="范围终点" value={String((node.value as string[])?.[1] ?? '')} onChange={e => change({ ...node, value: [(node.value as string[])[0], e.target.value] })} /></div> : type === 'bool' ? <select aria-label="条件值" value={String(node.value)} onChange={e => change({ ...node, value: e.target.value === 'true' })}><option value="">请选择</option><option value="true">是</option><option value="false">否</option></select> : <input aria-label="条件值" placeholder={type === 'date' ? '2026-10-01' : '输入值'} value={String(node.value ?? '')} onChange={e => change({ ...node, value: e.target.value })} />)}
    {remove && <Button variant="ghost" size="icon" aria-label="移除条件" onClick={remove}><Trash2 size={15} /></Button>}</div>
}
export function SearchBuilder({ value, onChange }: { value: AST; onChange: (ast: AST) => void }) {
  return <Expression node={value.root} change={root => onChange({ version: 1, root })} />
}
