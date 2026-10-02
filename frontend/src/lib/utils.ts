import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'
export const cn = (...inputs: ClassValue[]) => twMerge(clsx(inputs))
export const sizeLabel = (n: number) => n > 1024 ** 2 ? `${(n / 1024 ** 2).toFixed(1)} MB` : `${Math.round(n / 1024)} KB`
