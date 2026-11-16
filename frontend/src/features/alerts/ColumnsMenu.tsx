import { useEffect, useRef, useState } from 'react';
import { Columns3 } from 'lucide-react';
import { Button } from '../../components/ui';
import { ALERT_COLUMNS, type useAlertColumns } from './alertColumns';

export default function ColumnsMenu({ cols }: { cols: ReturnType<typeof useAlertColumns> }) {
    const [open, setOpen] = useState(false);
    const ref = useRef<HTMLDivElement>(null);
    useEffect(() => {
        if (!open) return;
        const onDown = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
        document.addEventListener('mousedown', onDown);
        return () => document.removeEventListener('mousedown', onDown);
    }, [open]);
    return (
        <div ref={ref} className="relative" onKeyDown={e => { if (e.key === 'Escape') setOpen(false); }}>
            <Button variant="secondary" size="sm" aria-haspopup="true" aria-expanded={open}
                onClick={() => setOpen(o => !o)}>
                <Columns3 size={14} />Columns
            </Button>
            {open && (
                <div role="group" aria-label="Visible columns"
                    className="absolute right-0 z-20 mt-1 w-48 rounded-lg border border-line bg-panel p-2 shadow-lg">
                    {ALERT_COLUMNS.map(c => (
                        <label key={c.key} className="flex items-center gap-2 px-2 py-1.5 text-sm text-fg-2 rounded-sm hover:bg-raised cursor-pointer">
                            <input type="checkbox" checked={cols.isVisible(c.key)} onChange={() => cols.toggle(c.key)} />
                            {c.label}
                        </label>
                    ))}
                    <button type="button" onClick={cols.reset} disabled={cols.isDefault}
                        className="mt-1 w-full border-t border-line px-2 pt-2 pb-1 text-left text-xs text-fg-3 hover:text-fg disabled:opacity-50 disabled:hover:text-fg-3">
                        Reset to default
                    </button>
                </div>
            )}
        </div>
    );
}
