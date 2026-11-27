import { useEffect, useId, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import { fieldClass } from '../../components/ui';
import { FIELD_RE } from './conditions';

const field = fieldClass();
type Suggestion = { value: string; count: number };

function useDebounced<T>(value: T, ms: number): T {
    const [v, setV] = useState(value);
    useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t); }, [value, ms]);
    return v;
}

/** Value box of a field filter with suggestions: the most common values of the chosen field among the alerts the
 *  list's other filters select (all of them, not the loaded page), narrowed as you type. Any text can still be
 *  typed. `scope()` returns those other filters as query params, evaluated at fetch time (relative time ranges). */
export default function ValuePicker({ fieldName, value, onChange, scope, scopeKey, inputId, placeholder, suggest = true }: {
    fieldName: string; value: string; onChange: (v: string) => void; scope: () => Record<string, string>; scopeKey: string;
    inputId?: string; placeholder?: string; suggest?: boolean;
}) {
    const [open, setOpen] = useState(false);
    const [active, setActive] = useState(-1);
    const id = useId();
    const q = useDebounced(value.trim(), 250);
    const enabled = suggest && open && FIELD_RE.test(fieldName);
    const { data = [], isFetching } = useQuery({
        queryKey: ['alerts', 'field-values', fieldName, q, scopeKey],
        enabled, staleTime: 30_000,
        queryFn: async () => (await api.get('/alerts/field-values', { params: { field: fieldName, ...(q && { q }), ...scope() } })).data as Suggestion[],
    });
    // The value already typed in full is not a suggestion worth showing on its own.
    const shown = enabled ? data.filter((s) => !(data.length === 1 && s.value.toLowerCase() === value.trim().toLowerCase())) : [];
    const listed = shown.length > 0;
    const pick = (v: string) => { onChange(v); setOpen(false); setActive(-1); };
    return (
        <div className="relative min-w-0">
            <input id={inputId} role="combobox" aria-autocomplete="list" aria-expanded={listed} aria-controls={id}
                aria-activedescendant={listed && active >= 0 ? `${id}-${active}` : undefined} value={value} placeholder={placeholder} spellCheck={false}
                autoComplete="off" onFocus={() => setOpen(true)} onBlur={() => setOpen(false)}
                onChange={(e) => { onChange(e.target.value); setOpen(true); setActive(-1); }}
                onKeyDown={(e) => {
                    if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && shown.length) {
                        e.preventDefault(); setOpen(true);
                        setActive((a) => (e.key === 'ArrowDown' ? (a + 1) % shown.length : (a <= 0 ? shown.length : a) - 1));
                    } else if (e.key === 'Enter' && listed && active >= 0) { e.preventDefault(); pick(shown[active].value); }
                    else if (e.key === 'Escape') setOpen(false);
                }}
                className={cn(field, 'w-full')} />
            {listed && (
                <ul id={id} role="listbox" aria-label={`Values of ${fieldName}`}
                    className="absolute z-30 left-0 top-full mt-1 min-w-full w-max max-w-[min(32rem,calc(100vw-2rem))] max-h-64 overflow-auto rounded-md border border-line bg-panel shadow-lg py-1">
                    {shown.map((s, i) => (
                        <li key={s.value} id={`${id}-${i}`} role="option" aria-selected={i === active} title={s.value}
                            onMouseDown={(e) => { e.preventDefault(); pick(s.value); }} onMouseEnter={() => setActive(i)}
                            className={cn('flex items-center justify-between gap-4 px-2.5 py-1 text-xs cursor-pointer', i === active ? 'bg-raised text-fg' : 'text-fg-2')}>
                            <span className="font-mono truncate">{s.value}</span>
                            <span className="num text-fg-3 shrink-0">{s.count.toLocaleString()} {s.count === 1 ? 'alert' : 'alerts'}</span>
                        </li>
                    ))}
                </ul>
            )}
            {enabled && !listed && !isFetching && q === value.trim() && (
                <p className="absolute z-30 left-0 top-full mt-1 rounded-md border border-line bg-panel shadow-lg px-2.5 py-1.5 text-xs text-fg-3 whitespace-nowrap">
                    {value.trim() ? 'No stored value contains this. You can still add it.' : 'No values for this field in the current filters.'}
                </p>
            )}
        </div>
    );
}
