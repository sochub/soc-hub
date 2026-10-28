import { useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';

interface Assignable { id: number; name: string; email: string }

/** Active analysts and admins of the tenant (who a case or task can be handed to). */
function useAssignable(enabled = true) {
    return useQuery({
        queryKey: ['assignable'],
        queryFn: async () => (await api.get('/users/assignable')).data as Assignable[],
        staleTime: 60_000, enabled,
    });
}

/** A plain select with "Unassigned"; the current value stays listed even if that person can't be picked any more. */
export default function AssigneeSelect({ value, currentName, onChange, disabled, label, className }: {
    value: number | null | undefined;
    currentName?: string | null;
    onChange: (id: number | null) => void;
    disabled?: boolean;
    label: string;
    className?: string;
}) {
    const { data: people = [] } = useAssignable(!disabled);
    const known = value == null || people.some((p) => p.id === value);
    return (
        <select aria-label={label} value={value ?? ''} disabled={disabled}
            onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}
            className={cn('bg-panel border border-line-strong rounded-sm px-1.5 py-0.5 text-xs text-fg-2 max-w-[12rem]',
                'focus:outline-hidden focus:border-accent disabled:opacity-60 disabled:cursor-default', className)}>
            <option value="">Unassigned</option>
            {!known && <option value={value!}>{currentName ?? `User #${value}`}</option>}
            {people.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
    );
}
