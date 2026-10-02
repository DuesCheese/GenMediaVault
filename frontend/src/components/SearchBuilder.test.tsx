import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SearchBuilder } from './SearchBuilder'
import type { AST } from '../api'

describe('SearchBuilder', () => {
  it('preserves nested OR when updating a value in an AND group', () => {
    const ast: AST = { version: 1, root: { type: 'and', children: [{ type: 'condition', field: 'model', op: 'eq', value: 'pony' }, { type: 'or', children: [{ type: 'condition', field: 'prompt', op: 'contains', value: 'white hair' }] }] } }
    const changed = vi.fn(); render(<SearchBuilder value={ast} onChange={changed} />)
    fireEvent.change(screen.getAllByLabelText('条件值')[0], { target: { value: 'flux' } })
    const next = changed.mock.calls[0][0]
    expect(next.root.children[0].value).toBe('flux')
    expect(next.root.children[1]).toEqual((ast.root as { children: unknown[] }).children[1])
    expect((ast.root as { children: { value: string }[] }).children[0].value).toBe('pony')
  })
  it('represents exclusion structurally without rewriting the underlying clause', () => {
    const ast: AST = { version: 1, root: { type: 'and', children: [{ type: 'condition', field: 'rating', op: 'gte', value: 4 }] } }
    const changed = vi.fn(); render(<SearchBuilder value={ast} onChange={changed} />)
    fireEvent.click(screen.getByText('排除本组'))
    expect(changed.mock.calls[0][0].root).toEqual({ type: 'not', child: ast.root })
  })
  it('keeps both range endpoints and numeric input as text until server validation', () => {
    const ast: AST = { version: 1, root: { type: 'condition', field: 'cfg', op: 'between', value: ['4', '6'] } }
    const changed = vi.fn(); render(<SearchBuilder value={ast} onChange={changed} />)
    fireEvent.change(screen.getByLabelText('范围终点'), { target: { value: '7.5' } })
    expect(changed.mock.calls[0][0].root.value).toEqual(['4', '7.5'])
  })
})
