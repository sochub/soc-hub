import { useQuery } from '@tanstack/react-query';
import { ChevronDown, UserRound } from 'lucide-react';
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

/** The rounded pill shared by the task controls (assignee, ticket, Run) so they line up at one height. */
export const PILL = 'inline-flex items-center gap-1 h-6 px-2.5 rounded-full border border-line-strong text-xs text-fg-2 whitespace-nowrap';

/** A pill-shaped select with "Unassigned"; the current value stays listed even if that person can't be picked any more. */
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
    const shown = value == null ? 'Unassigned' : people.find((p) => p.id === value)?.name ?? currentName ?? `User #${value}`;
    // The native select covers the whole pill (transparent), so a click anywhere on it opens the list.
    return (
        <span className={cn(PILL, 'relative bg-panel hover:bg-raised focus-within:border-accent focus-within:ring-1 focus-within:ring-accent',
            disabled ? 'opacity-60' : 'cursor-pointer', className)}>
            <UserRound size={12} className="shrink-0 text-muted" aria-hidden />
            <span className={cn('max-w-[11rem] truncate', value == null ? 'text-muted' : 'text-fg-2')} aria-hidden>{shown}</span>
            <ChevronDown size={12} className="shrink-0 text-muted" aria-hidden />
            <select aria-label={label} value={value ?? ''} disabled={disabled}
                onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}
                className="absolute inset-0 w-full h-full opacity-0 cursor-pointer disabled:cursor-default">
                <option value="">Unassigned</option>
                {!known && <option value={value!}>{currentName ?? `User #${value}`}</option>}
                {people.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
        </span>
    );
}
