import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, keepPreviousData } from '@tanstack/react-query';
import { Search, ShieldAlert, Plus, Hash, Database, AlertOctagon, CornerDownLeft, type LucideIcon } from 'lucide-react';
import { getPage } from '../../api/paging';
import { cn } from '../../lib/utils';

interface Item { id: string; label: string; hint: string; icon: LucideIcon; to: string }
export interface PaletteNavItem { label: string; path: string; icon: LucideIcon }

const MAX_PER_GROUP = 6;

/** ⌘K / Ctrl+K: jump to pages, cases and observables, or start a new case. */
export default function CommandPalette({ nav }: { nav: PaletteNavItem[] }) {
    const navigate = useNavigate();
    const [open, setOpen] = useState(false);
    const [q, setQ] = useState('');
    const [active, setActive] = useState(0);

    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
                e.preventDefault();
                setOpen(o => !o);
                setQ(''); setActive(0);
            }
        };
        const onOpen = () => { setQ(''); setActive(0); setOpen(true); };
        window.addEventListener('keydown', onKey);
        window.addEventListener('palette:open', onOpen);
        return () => { window.removeEventListener('keydown', onKey); window.removeEventListener('palette:open', onOpen); };
    }, []);

    // Cases and observables are searched server-side (?q=), after a short pause in typing.
    const [dq, setDq] = useState('');
    useEffect(() => {
        const t = setTimeout(() => setDq(q.trim()), 200);
        return () => clearTimeout(t);
    }, [q]);
    const search = { q: dq, limit: MAX_PER_GROUP };
    const { data: cases = [] } = useQuery({
        queryKey: ['palette', 'cases', dq],
        queryFn: async () => (await getPage<{ id: number; title: string; status: string }>('/cases/', search)).items,
        enabled: open && dq.length >= 1, placeholderData: keepPreviousData,
    });
    const { data: artifacts = [] } = useQuery({
        queryKey: ['palette', 'artifacts', dq],
        queryFn: async () => (await getPage<{ id: number; value: string; artifact_type: string }>('/artifacts/', search)).items,
        enabled: open && dq.length >= 2, placeholderData: keepPreviousData, staleTime: 60_000,
    });
    const { data: iocs = [] } = useQuery({
        queryKey: ['palette', 'iocs', dq],
        queryFn: async () => (await getPage<{ id: number; value: string; ioc_type: string }>('/iocs/', search)).items,
        enabled: open && dq.length >= 2, placeholderData: keepPreviousData, staleTime: 60_000,
    });

    const items = useMemo<Item[]>(() => {
        const s = q.trim().toLowerCase();
        const has = (v: string) => v.toLowerCase().includes(s);
        const out: Item[] = [];
        const caseNo = s.replace(/^#/, '');
        if (/^\d+$/.test(caseNo)) out.push({ id: 'goto', label: `Open case #${caseNo}`, hint: 'jump', icon: Hash, to: `/cases/${caseNo}` });
        if (!s || has('new case')) out.push({ id: 'new', label: 'New case', hint: 'action', icon: Plus, to: '/cases?new=1' });
        out.push(...nav.filter(n => !s || has(n.label)).map(n => ({ id: `nav-${n.path}`, label: n.label, hint: 'page', icon: n.icon, to: n.path })));
        if (s) {
            out.push(...cases.slice(0, MAX_PER_GROUP)
                .map(c => ({ id: `case-${c.id}`, label: `#${c.id} ${c.title}`, hint: c.status.replace('_', ' '), icon: ShieldAlert, to: `/cases/${c.id}` })));
        }
        if (s.length >= 2) {
            out.push(...iocs.slice(0, MAX_PER_GROUP)
                .map(i => ({ id: `ioc-${i.id}`, label: i.value, hint: `ioc · ${i.ioc_type}`, icon: AlertOctagon, to: `/observables/iocs?q=${encodeURIComponent(i.value)}` })));
            out.push(...artifacts.slice(0, MAX_PER_GROUP)
                .map(a => ({ id: `art-${a.id}`, label: a.value, hint: `artifact · ${a.artifact_type}`, icon: Database, to: `/observables?q=${encodeURIComponent(a.value)}` })));
        }
        return out;
    }, [q, nav, cases, artifacts, iocs]);

    if (!open) return null;

    const go = (it?: Item) => { if (!it) return; setOpen(false); navigate(it.to); };
    const onKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'Escape') setOpen(false);
        else if (e.key === 'ArrowDown') { e.preventDefault(); setActive(a => Math.min(a + 1, items.length - 1)); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(a => Math.max(a - 1, 0)); }
        else if (e.key === 'Enter') { e.preventDefault(); go(items[active]); }
    };

    return (
        <div className="fixed inset-0 z-[60] flex items-start justify-center pt-[12vh] px-4 bg-fg/40"
            onMouseDown={() => setOpen(false)}>
            <div role="dialog" aria-modal="true" aria-label="Command palette"
                className="w-full max-w-xl bg-panel border border-line-strong rounded-md shadow-[0_12px_40px_-12px_rgb(0_0_0/0.35)] overflow-hidden animate-fade-in"
                onMouseDown={e => e.stopPropagation()}>
                <div className="flex items-center gap-3 px-4 border-b border-line focus-within:shadow-[inset_0_-2px_0_var(--color-accent)]">
                    <Search size={16} className="text-fg-3 shrink-0" />
                    <input autoFocus value={q} onChange={e => { setQ(e.target.value); setActive(0); }} onKeyDown={onKeyDown}
                        placeholder="Search cases, IOCs, pages… or #123"
                        aria-label="Search" role="combobox" aria-expanded="true" aria-controls="palette-list"
                        aria-activedescendant={items[active] ? `palette-${items[active].id}` : undefined}
                        className="flex-1 h-12 bg-transparent text-sm text-fg placeholder:text-muted outline-none" />
                    <kbd className="text-xs text-fg-3 border border-line rounded-sm px-1.5 py-0.5">Esc</kbd>
                </div>
                <ul id="palette-list" role="listbox" className="max-h-[50vh] overflow-y-auto py-1.5">
                    {items.length === 0 && <li className="px-4 py-6 text-center text-sm text-muted">No matches</li>}
                    {items.map((it, i) => {
                        const Icon = it.icon;
                        return (
                            <li key={it.id} id={`palette-${it.id}`} role="option" aria-selected={i === active}
                                onMouseMove={() => setActive(i)} onClick={() => go(it)}
                                className={cn('mx-1.5 px-3 h-9 flex items-center gap-3 rounded-md cursor-pointer text-sm',
                                    i === active ? 'bg-raised text-fg' : 'text-fg-2')}>
                                <Icon size={15} aria-hidden className={i === active ? 'text-accent' : 'text-fg-3'} />
                                <span className="truncate flex-1">{it.label}</span>
                                <span className="text-xs text-fg-3">{it.hint}</span>
                                {i === active && <CornerDownLeft size={12} aria-hidden className="text-fg-3" />}
                            </li>
                        );
                    })}
                </ul>
            </div>
        </div>
    );
}
