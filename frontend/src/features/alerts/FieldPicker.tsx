import { useId, useState } from 'react';
import { cn } from '../../lib/utils';
import { fieldClass } from '../../components/ui';

const field = fieldClass();

/** Field box with its own suggestion list: opens below the box (a native select popup covers it on macOS), and any
 *  payload path can be typed. An exact known field lists everything, unlike a datalist that filters down to itself (#130). */
export default function FieldPicker({ value, groups, onChange, inputId }: { value: string; groups: [string, string[]][]; onChange: (v: string) => void; inputId?: string }) {
    const [open, setOpen] = useState(false);
    const [active, setActive] = useState(0);
    const id = useId();
    const q = groups.some(([, o]) => o.includes(value)) ? '' : value.toLowerCase();
    const shown = groups.map(([g, o]): [string, string[]] => [g, o.filter((f) => f.toLowerCase().includes(q))]).filter(([, o]) => o.length);
    const flat = shown.flatMap(([, o]) => o);
    const listed = open && flat.length > 0;
    const pick = (f: string) => { onChange(f); setOpen(false); };
    return (
        <div className="relative min-w-0">
            <input id={inputId} role="combobox" aria-label="Field" aria-autocomplete="list" aria-expanded={listed} aria-controls={id}
                aria-activedescendant={listed ? `${id}-${active}` : undefined} value={value} placeholder="field or payload.path" spellCheck={false}
                onFocus={() => { setOpen(true); setActive(0); }} onBlur={() => setOpen(false)}
                onChange={(e) => { onChange(e.target.value); setOpen(true); setActive(0); }}
                onKeyDown={(e) => {
                    if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && flat.length) {
                        e.preventDefault(); setOpen(true);
                        setActive((a) => (a + (e.key === 'ArrowDown' ? 1 : flat.length - 1)) % flat.length);
                    } else if (e.key === 'Enter' && listed) { e.preventDefault(); pick(flat[active]); }
                    else if (e.key === 'Escape') setOpen(false);
                }}
                className={cn(field, 'font-mono text-xs w-full')} />
            {listed && (
                <ul id={id} role="listbox" aria-label="Fields"
                    className="absolute z-30 left-0 top-full mt-1 min-w-full w-max max-w-[min(32rem,calc(100vw-2rem))] max-h-64 overflow-auto rounded-md border border-line bg-panel shadow-lg py-1">
                    {shown.map(([g, opts]) => (
                        <li key={g} role="presentation">
                            <p className="px-2.5 pt-1.5 pb-0.5 label-caps">{g}</p>
                            <ul role="group" aria-label={g}>
                                {opts.map((f) => {
                                    const i = flat.indexOf(f);
                                    return (
                                        <li key={f} id={`${id}-${i}`} role="option" aria-selected={i === active} title={f}
                                            onMouseDown={(e) => { e.preventDefault(); pick(f); }} onMouseEnter={() => setActive(i)}
                                            className={cn('px-2.5 py-1 font-mono text-xs truncate cursor-pointer', i === active ? 'bg-raised text-fg' : 'text-fg-2')}>{f}</li>
                                    );
                                })}
                            </ul>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
}

