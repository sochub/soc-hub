interface Option { id: number; label: string }

/** Simple checkbox list used as a multi-select. */
export default function CheckList({ options, value, onChange, empty }: {
    options: Option[]; value: number[]; onChange: (v: number[]) => void; empty: string;
}) {
    if (!options.length) return <p className="text-xs text-fg-3">{empty}</p>;
    const toggle = (id: number) => onChange(value.includes(id) ? value.filter((x) => x !== id) : [...value, id]);
    return (
        <div className="max-h-40 overflow-y-auto border border-line rounded-lg divide-y divide-line">
            {options.map((o) => (
                <label key={o.id} className="flex items-center gap-2 px-3 py-1.5 text-sm text-fg cursor-pointer hover:bg-raised/60">
                    <input type="checkbox" checked={value.includes(o.id)} onChange={() => toggle(o.id)} className="accent-accent" />
                    {o.label}
                </label>
            ))}
        </div>
    );
}
