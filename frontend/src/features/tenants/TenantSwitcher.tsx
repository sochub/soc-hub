import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, ChevronsUpDown, Building2, Loader2 } from 'lucide-react';
import { api } from '../../api/client';
import { switchTenant } from '../../api/auth';
import type { User, Tenant } from '../../types';
import { cn } from '../../lib/utils';

interface Option {
    id: number;
    name: string;
    role?: string;
}

export default function TenantSwitcher() {
    const qc = useQueryClient();
    const [open, setOpen] = useState(false);
    const [busy, setBusy] = useState(false);

    const { data: me } = useQuery({
        queryKey: ['currentUser'],
        queryFn: async () => (await api.get('/users/me')).data as User,
        staleTime: 5 * 60 * 1000,
    });

    const isSuper = !!me?.is_super_admin;

    // Super admins can switch into any tenant; load the full list for them.
    const { data: allTenants } = useQuery({
        queryKey: ['tenants', 'all'],
        queryFn: async () => (await api.get('/tenants/')).data as Tenant[],
        enabled: isSuper,
        staleTime: 5 * 60 * 1000,
    });

    if (!me) return null;

    const options: Option[] = isSuper
        ? (allTenants ?? []).map((t) => ({ id: t.id, name: t.name }))
        : (me.memberships ?? []).map((m) => ({ id: m.tenant_id, name: m.tenant_name, role: m.role }));

    // Nothing to switch between → don't render.
    if (isSuper ? options.length === 0 : options.length <= 1) return null;

    const active = options.find((o) => o.id === me.active_tenant_id);

    const choose = async (id: number) => {
        if (id === me.active_tenant_id) {
            setOpen(false);
            return;
        }
        setBusy(true);
        try {
            await switchTenant(id);
            setOpen(false);
            await qc.invalidateQueries();
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="relative">
            <p className="label-mono mb-1.5 px-1">{isSuper ? 'viewing tenant' : 'tenant'}</p>
            <button
                onClick={() => setOpen((o) => !o)}
                disabled={busy}
                aria-haspopup="listbox"
                aria-expanded={open}
                className="w-full flex items-center justify-between gap-2 px-2.5 py-2 border border-zinc-200 bg-white text-sm text-zinc-800 hover:border-zinc-300 hover:bg-zinc-50 transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent-500 disabled:opacity-60"
            >
                <span className="flex items-center gap-2 min-w-0">
                    <Building2 size={14} className="text-accent-600 shrink-0" />
                    <span className="truncate font-medium">{active?.name ?? 'Select a tenant'}</span>
                </span>
                {busy
                    ? <Loader2 size={14} className="animate-spin text-zinc-400 shrink-0" />
                    : <ChevronsUpDown size={14} className="text-zinc-400 shrink-0" />}
            </button>

            {open && (
                <ul
                    role="listbox"
                    className="absolute z-50 left-0 right-0 mt-1 max-h-72 overflow-auto border border-zinc-200 bg-white shadow-lg py-1 animate-fade-in"
                >
                    {options.map((o) => {
                        const isActive = o.id === me.active_tenant_id;
                        return (
                            <li key={o.id} role="option" aria-selected={isActive}>
                                <button
                                    onClick={() => choose(o.id)}
                                    className={cn(
                                        'w-full flex items-center justify-between gap-2 px-2.5 py-2 text-sm hover:bg-zinc-100 transition-colors',
                                        isActive ? 'text-accent-700 font-medium' : 'text-zinc-700',
                                    )}
                                >
                                    <span className="truncate">{o.name}</span>
                                    <span className="flex items-center gap-2 shrink-0">
                                        {o.role && <span className="font-mono text-[10px] uppercase text-zinc-400">{o.role}</span>}
                                        {isActive && <Check size={13} className="text-accent-600" />}
                                    </span>
                                </button>
                            </li>
                        );
                    })}
                </ul>
            )}
        </div>
    );
}
