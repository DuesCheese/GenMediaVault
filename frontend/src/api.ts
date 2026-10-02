import type { components } from './api.generated'
export type Asset = components['schemas']['AssetOut']
export type Session = components['schemas']['SessionOut']
export type SearchResult = components['schemas']['SearchOut']
export type SearchInput = components['schemas']['SearchInput']
export type Node = { type: 'condition'; field: string; op: string; value: string | number | boolean | null | (string | number)[] } | { type: 'and' | 'or'; children: Node[] } | { type: 'not'; child: Node }
export type AST = { version: 1; root: Node }
export type Library = { id: string; name: string; mode: 'managed' | 'indexed'; root_path: string | null; watch_enabled: boolean; count: number; last_scan_at: string | null }
export type Collection = { id: string; name: string; kind: 'static' | 'smart'; query: string; ast: AST | null; revision: number }
export type Job = { id: string; kind: string; status: string; progress: { phase?: string; completed?: number; total?: number; failed?: number; errors?: { file?: string; error: string }[] }; result: { download?: string; failed?: number; errors?: { file?: string; error: string }[] }; error: string | null; created_at: string; requested_by: string | null }
export type Detail = Asset & { generation: Record<string, unknown>; notes: string; raw: { id: string; source: string; parser: string; parser_version: string; data: unknown }[]; conflicts: unknown[]; tag_sources: { name: string; source: string }[]; tokens: { token: string; weight: number; polarity: string; category: string; scope: string }[]; original: string; sha256: string; files: { path: string; present: boolean }[] }
export type Character = { index: number; name: string; prompt: string; negative: string; centers: unknown; enabled: boolean }
let csrf = ''
export const setCSRF = (token: string) => { csrf = token }
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  if (options.method && options.method !== 'GET') headers.set('X-CSRF-Token', csrf)
  const response = await fetch(`/api/v1${path}`, { credentials: 'same-origin', ...options, headers })
  if (!response.ok) {
    const data = await response.json().catch(() => ({ detail: response.statusText }))
    if (response.status === 401 && path !== '/auth/login' && path !== '/auth/me') window.dispatchEvent(new Event('session-expired'))
    throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail))
  }
  return response.json()
}
export const post = <T,>(path: string, body?: unknown) => api<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })
export async function copyText(text: string) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text)
  const area = document.createElement('textarea')
  area.value = text; area.style.position = 'fixed'; area.style.opacity = '0'
  document.body.appendChild(area); area.select()
  const copied = document.execCommand('copy'); area.remove()
  if (!copied) throw new Error('浏览器不允许自动复制，请手动选择文本复制')
}
