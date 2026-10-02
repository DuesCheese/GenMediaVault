import * as Dialog from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import type { ReactNode } from 'react'

export function Modal({ open, onOpenChange, title, description, children, wide = false }: { open: boolean; onOpenChange: (open: boolean) => void; title: string; description?: string; children: ReactNode; wide?: boolean }) {
  return <Dialog.Root open={open} onOpenChange={onOpenChange}><Dialog.Portal><Dialog.Overlay className="modal-overlay" /><Dialog.Content className={`modal ${wide ? 'modal-wide' : ''}`}>
    <div className="modal-heading"><div><Dialog.Title>{title}</Dialog.Title><Dialog.Description>{description || '查看和管理你的生成资产。'}</Dialog.Description></div><Dialog.Close className="icon-button" aria-label="关闭"><X size={20} /></Dialog.Close></div>
    {children}
  </Dialog.Content></Dialog.Portal></Dialog.Root>
}
