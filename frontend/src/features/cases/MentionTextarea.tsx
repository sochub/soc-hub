import { useEffect, useId, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';
import Avatar from '../../components/Avatar';
import { useTenantId } from '../notifications/api';
import { useCanRun } from '../enrichment/useCanRun';
import { applyEdit, parse, serialize, type Chip } from './mentionChips';

interface Mentionable { id: number; name: string; email: string; has_avatar?: boolean; avatar_version?: string | null }
interface Props {
    value: string;
    onChange: (serialized: string) => void;
    disabled?: boolean;
    placeholder?: string;
    rows?: number;
    className?: string;
}

const TRIGGER_RE = /(?:^|\s)@(\S{0,30})$/;

export default function MentionTextarea({ value, onChange, disabled, placeholder, rows = 3, className }: Props) {
    // display text, chips and the last serialized value we reported, kept together.
    const [st, setSt] = useState(() => ({ ...parse(value), emitted: value }));
    const { display, chips } = st;
    const ref = useRef<HTMLTextAreaElement>(null);
    const [caret, setCaret] = useState(0);
    const [dismissed, setDismissed] = useState(false);
    const [active, setActive] = useState(0);
    const [debounced, setDebounced] = useState<string | null>(null);
    const canMention = useCanRun();
    const { tenantId } = useTenantId();
    const listId = useId();

    // External resets (e.g. clearing after submit) re-seed the display (adjust state during render).
    if (value !== st.emitted) setSt({ ...parse(value), emitted: value });

    const trigger = !disabled && canMention && !dismissed ? TRIGGER_RE.exec(display.slice(0, caret)) : null;
    const query = trigger ? trigger[1] : null;

    useEffect(() => {
        const t = setTimeout(() => setDebounced(query), query === null ? 0 : 150);
        return () => clearTimeout(t);
    }, [query]);

    const { data: options = [] } = useQuery({
        queryKey: ['mentionable', tenantId, debounced],
        queryFn: async () => (await api.get('/users/mentionable', { params: { q: debounced } })).data as Mentionable[],
        enabled: debounced !== null,
        staleTime: 30_000,
    });
    // Only open (and only pick) from a list that belongs to the current query.
    const open = query !== null && debounced === query && options.length > 0;

    const emit = (nextDisplay: string, nextChips: Chip[]) => {
        const text = serialize(nextDisplay, nextChips);
        setSt({ display: nextDisplay, chips: nextChips, emitted: text });
        onChange(text);
    };

    const pick = (u: Mentionable) => {
        if (!trigger || !open) return;
        const atIdx = caret - trigger[1].length - 1;
        const name = u.name ?? '';
        const insert = `@${name} `;
        const delta = insert.length - (caret - atIdx);
        const next = display.slice(0, atIdx) + insert + display.slice(caret);
        const list: Chip[] = [];
        for (const c of chips) {
            const end = c.start + c.name.length + 1;
            if (end <= atIdx) list.push(c);
            else if (c.start >= caret) list.push({ ...c, start: c.start + delta });
        }
        if (name && !/[\]\n]/.test(name) && name.length <= 100) {
            list.push({ name, id: u.id, start: atIdx });
            list.sort((a, b) => a.start - b.start);
        }
        emit(next, list);
        const newCaret = atIdx + insert.length;
        setCaret(newCaret);
        setActive(0);
        requestAnimationFrame(() => {
            ref.current?.focus();
            ref.current?.setSelectionRange(newCaret, newCaret);
        });
    };

    const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if (!open) return;
        if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => (a + 1) % options.length); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => (a - 1 + options.length) % options.length); }
        else if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); pick(options[Math.min(active, options.length - 1)]); }
        else if (e.key === 'Escape') { e.preventDefault(); setDismissed(true); }
    };

    const syncCaret = (el: HTMLTextAreaElement) => setCaret(el.selectionStart ?? 0);
    const activeIdx = Math.min(active, Math.max(options.length - 1, 0));

    return (
        <div className="relative">
            <textarea
                ref={ref}
                value={display}
                disabled={disabled}
                placeholder={placeholder}
                rows={rows}
                role="combobox"
                aria-expanded={open}
                aria-controls={open ? listId : undefined}
                aria-autocomplete="list"
                aria-activedescendant={open ? `${listId}-${activeIdx}` : undefined}
                onChange={(e) => {
                    setDismissed(false);
                    setActive(0);
                    syncCaret(e.target);
                    emit(e.target.value, applyEdit(display, e.target.value, chips, e.target.selectionStart ?? undefined));
                }}
                onKeyDown={onKeyDown}
                onKeyUp={(e) => { if (!['Enter', 'Tab', 'Escape'].includes(e.key)) { setDismissed(false); syncCaret(e.currentTarget); } }}
                onClick={(e) => syncCaret(e.currentTarget)}
                onBlur={() => setDismissed(true)}
                className={className ?? 'w-full bg-white border border-zinc-200 rounded-lg px-3 py-2 text-sm text-zinc-900 focus:outline-hidden focus:ring-1 focus:ring-accent resize-none disabled:opacity-60'}
            />
            {open && (
                <ul
                    id={listId}
                    role="listbox"
                    aria-label="Mention a teammate"
                    className="absolute left-0 right-0 z-30 mt-1 max-h-48 overflow-auto bg-white border border-zinc-200 shadow-lg text-sm"
                >
                    {options.map((u, i) => (
                        <li
                            key={u.id}
                            id={`${listId}-${i}`}
                            role="option"
                            aria-selected={i === activeIdx}
                            onMouseDown={(e) => { e.preventDefault(); pick(u); }}
                            onMouseEnter={() => setActive(i)}
                            className={`px-3 py-1.5 cursor-pointer flex items-baseline gap-2 ${i === activeIdx ? 'bg-accent/10' : ''}`}
                        >
                            <Avatar userId={u.id} name={u.name} hasAvatar={u.has_avatar} version={u.avatar_version} size={20} className="self-center" />
                            <span className="font-medium text-zinc-900">{u.name ?? ''}</span>
                            <span className="text-xs text-zinc-500 truncate">{u.email}</span>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
}
