import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';
import { useTenantId } from '../notifications/api';
import { useCanRun } from '../enrichment/useCanRun';
import { MENTION_RE } from './mentionRe';

interface Mentionable { id: number; name: string; email: string }
interface Tracked { name: string; id: number }

interface Props {
    value: string;
    onChange: (serialized: string) => void;
    disabled?: boolean;
    placeholder?: string;
    rows?: number;
    className?: string;
}

/** Turn serialized text into visible text (@Name) plus the tracked mention list. */
function parse(serialized: string): { display: string; tracked: Tracked[] } {
    const tracked: Tracked[] = [];
    const display = serialized.replace(MENTION_RE, (_m, name: string, id: string) => {
        tracked.push({ name, id: Number(id) });
        return `@${name}`;
    });
    return { display, tracked };
}

/** Replace each tracked @Name still present, left to right, by its token. */
function serialize(display: string, tracked: Tracked[]): { text: string; kept: Tracked[] } {
    let out = '';
    let pos = 0;
    const kept: Tracked[] = [];
    for (const t of tracked) {
        const needle = `@${t.name}`;
        const at = display.indexOf(needle, pos);
        if (at === -1) continue;
        out += display.slice(pos, at) + `@[${t.name}](user:${t.id})`;
        pos = at + needle.length;
        kept.push(t);
    }
    return { text: out + display.slice(pos), kept };
}

const TRIGGER_RE = /(?:^|\s)@(\S{0,30})$/;

export default function MentionTextarea({ value, onChange, disabled, placeholder, rows = 3, className }: Props) {
    // display text, tracked mentions and the last serialized value we reported, kept together.
    const [st, setSt] = useState(() => ({ ...parse(value), emitted: value }));
    const { display, tracked } = st;
    const ref = useRef<HTMLTextAreaElement>(null);
    const [caret, setCaret] = useState(0);
    const [dismissed, setDismissed] = useState(false);
    const [active, setActive] = useState(0);
    const [debounced, setDebounced] = useState('');
    const canMention = useCanRun();
    const { tenantId } = useTenantId();

    // External resets (e.g. clearing after submit) re-seed the display (adjust state during render).
    if (value !== st.emitted) {
        const p = parse(value);
        setSt({ display: p.display, tracked: p.tracked, emitted: value });
    }

    const trigger = !disabled && canMention && !dismissed ? TRIGGER_RE.exec(display.slice(0, caret)) : null;
    const query = trigger ? trigger[1] : null;

    useEffect(() => {
        if (query === null) return;
        const t = setTimeout(() => setDebounced(query), 150);
        return () => clearTimeout(t);
    }, [query]);

    const { data: options = [] } = useQuery({
        queryKey: ['mentionable', tenantId, debounced],
        queryFn: async () => (await api.get('/users/mentionable', { params: { q: debounced } })).data as Mentionable[],
        enabled: query !== null,
        staleTime: 30_000,
    });
    const open = query !== null && options.length > 0;
    const listId = 'mention-list';

    const emit = (nextDisplay: string, nextTracked: Tracked[]) => {
        const { text, kept } = serialize(nextDisplay, nextTracked);
        setSt({ display: nextDisplay, tracked: kept, emitted: text });
        onChange(text);
    };

    const pick = (u: Mentionable) => {
        if (!trigger) return;
        const atIdx = caret - trigger[1].length - 1;
        const next = display.slice(0, atIdx) + `@${u.name} ` + display.slice(caret);
        const newCaret = atIdx + u.name.length + 2;
        // Insert into the tracked list in text order so left-to-right replacement stays aligned.
        const before = serialize(display.slice(0, atIdx), tracked).kept.length;
        const list = tracked.slice();
        const canTrack = !/[\]\n]/.test(u.name) && u.name.length <= 100;
        if (canTrack) list.splice(before, 0, { name: u.name, id: u.id });
        emit(next, list);
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
                aria-activedescendant={open ? `${listId}-${Math.min(active, options.length - 1)}` : undefined}
                onChange={(e) => {
                    setDismissed(false);
                    setActive(0);
                    syncCaret(e.target);
                    emit(e.target.value, tracked);
                }}
                onKeyDown={onKeyDown}
                onKeyUp={(e) => { if (!['Enter', 'Tab', 'Escape'].includes(e.key)) { setDismissed(false); syncCaret(e.currentTarget); } }}
                onClick={(e) => syncCaret(e.currentTarget)}
                onBlur={() => setDismissed(true)}
                className={className ?? 'w-full bg-white border border-zinc-200 rounded-lg px-3 py-2 text-sm text-zinc-900 focus:outline-none focus:ring-1 focus:ring-accent resize-none disabled:opacity-60'}
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
                            aria-selected={i === active}
                            onMouseDown={(e) => { e.preventDefault(); pick(u); }}
                            onMouseEnter={() => setActive(i)}
                            className={`px-3 py-1.5 cursor-pointer flex items-baseline gap-2 ${i === active ? 'bg-accent/10' : ''}`}
                        >
                            <span className="font-medium text-zinc-900">{u.name}</span>
                            <span className="text-xs text-zinc-500 truncate">{u.email}</span>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
}
