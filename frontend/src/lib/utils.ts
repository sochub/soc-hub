import { type ClassValue, clsx } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
    return twMerge(clsx(inputs))
}

/** Minutes as a compact duration: 45m, 3.2h, 2.1d; "—" when unknown. */
export function fmtDuration(min: number | null | undefined): string {
    if (min == null || !Number.isFinite(min)) return '—'
    if (min < 60) return `${Math.round(min)}m`
    if (min < 48 * 60) return `${(min / 60).toFixed(1)}h`
    return `${(min / 1440).toFixed(1)}d`
}
