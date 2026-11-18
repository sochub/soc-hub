import { useRef } from 'react';
import { NavLink } from 'react-router-dom';
import { cn } from '../../lib/utils';

export interface TabItem<T extends string> { value: T; label: React.ReactNode; count?: number }

/**
 * Section tabs: plain labels on the row rule; the active one is underlined in the accent.
 * Proper ARIA tablist with arrow-key / Home / End movement. Render the panel yourself with id `${idPrefix}-panel`.
 */
export function FolderTabs<T extends string>({ items, value, onChange, label, idPrefix = 'tabs', className, children }: {
    items: TabItem<T>[]; value: T; onChange: (v: T) => void; label: string; idPrefix?: string; className?: string; children?: React.ReactNode;
}) {
    const ref = useRef<HTMLDivElement>(null);
    const onKey = (e: React.KeyboardEvent, i: number) => {
        const n = items.length;
        const j = e.key === 'ArrowRight' ? (i + 1) % n : e.key === 'ArrowLeft' ? (i - 1 + n) % n : e.key === 'Home' ? 0 : e.key === 'End' ? n - 1 : -1;
        if (j < 0) return;
        e.preventDefault();
        onChange(items[j].value);
        ref.current?.querySelectorAll<HTMLButtonElement>('[role=tab]')[j]?.focus();
    };
    return (
        <div className={cn('flex items-end gap-2 border-b border-line', className)}>
            <div ref={ref} role="tablist" aria-label={label} className="flex items-end gap-0.5 overflow-x-auto">
                {items.map((t, i) => (
                    <button key={t.value} type="button" role="tab" id={`${idPrefix}-${t.value}`}
                        aria-selected={value === t.value} aria-controls={`${idPrefix}-panel`} tabIndex={value === t.value ? 0 : -1}
                        onClick={() => onChange(t.value)} onKeyDown={(e) => onKey(e, i)} className="folder-tab">
                        {t.label}
                        {t.count != null && <span className="num text-xs font-normal text-fg-3">{t.count}</span>}
                    </button>
                ))}
            </div>
            {children && <div className="ml-auto pb-1.5 flex items-center gap-2">{children}</div>}
        </div>
    );
}

/** Folder tabs that are routes (hubs). NavLink sets aria-current on the active one. */
export function FolderTabLinks({ items, label, className, children }: {
    items: { to: string; label: React.ReactNode; end?: boolean }[]; label: string; className?: string; children?: React.ReactNode;
}) {
    return (
        <div className={cn('flex items-end gap-2 border-b border-line', className)}>
            <nav aria-label={label} className="flex items-end gap-0.5 overflow-x-auto">
                {items.map((t) => <NavLink key={t.to} to={t.to} end={t.end} className="folder-tab">{t.label}</NavLink>)}
            </nav>
            {children && <div className="ml-auto pb-1.5 flex items-center gap-2">{children}</div>}
        </div>
    );
}
