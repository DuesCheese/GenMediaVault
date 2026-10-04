import { Copy } from 'lucide-react'
import type { Character } from '../api'

export function CharacterCards({ characters, copy }: { characters: Character[]; copy: (text: string) => void }) {
  return <>{characters.map(character => <section className="character-card" key={character.index} aria-label={character.name || `角色 ${character.index + 1}`}>
    <div className="row between"><h3>{character.name || `角色 ${character.index + 1}`}</h3><span className="badge">{character.enabled ? '启用' : '停用'}</span></div>
    <div className="prompt-heading">角色正向提示词<button className="text-button" aria-label="复制角色正向提示词" onClick={() => copy(character.prompt || '')}><Copy size={13} />复制</button></div>
    <div className="prompt-text">{character.prompt || '无'}</div>
    <div className="prompt-heading">角色负向提示词<button className="text-button" aria-label="复制角色负向提示词" onClick={() => copy(character.negative || '')}><Copy size={13} />复制</button></div>
    <div className="prompt-text subdued">{character.negative || '无'}</div>
    <p className="muted">位置：{character.centers == null ? '未指定' : JSON.stringify(character.centers)}</p>
  </section>)}</>
}
