import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import { cn } from '../lib/utils';

interface AvatarProps {
    userId: number | null | undefined;
    name?: string | null;
    hasAvatar?: boolean;
    /** Opaque cache-buster; changes when the user's image changes. */
    version?: string | null;
    size?: number;
    className?: string;
}

function initials(name?: string | null): string {
    const parts = (name ?? '').trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return '?';
    return parts.map((p) => p[0]).join('').toUpperCase().slice(0, 2);
}

/** User avatar: authenticated blob fetch with an initials fallback. */
export default function Avatar({ userId, name, hasAvatar, version, size = 32, className }: AvatarProps) {
    const { data: blob } = useQuery({
        queryKey: ['avatar', userId, hasAvatar, version ?? null],
        queryFn: async () => (await api.get(`/users/${userId}/avatar`, { responseType: 'blob', params: version ? { v: version } : undefined })).data as Blob,
        enabled: !!userId && !!hasAvatar,
        staleTime: 5 * 60_000,
        retry: false,
    });
    const [url, setUrl] = useState<string | null>(null);
    useEffect(() => {
        // eslint-disable-next-line react-hooks/set-state-in-effect -- object URL lifecycle is tied to this effect
        if (!blob) { setUrl(null); return; }
        const u = URL.createObjectURL(blob);
        setUrl(u);
        return () => URL.revokeObjectURL(u);
    }, [blob]);

    const box = { width: size, height: size, fontSize: Math.max(10, Math.round(size * 0.38)) };
    if (url && hasAvatar) {
        return <img src={url} alt="" style={box} className={cn('object-cover shrink-0 bg-zinc-100', className)} />;
    }
    return (
        <span style={box} aria-hidden="true"
            className={cn('bg-zinc-900 text-white flex items-center justify-center font-mono font-semibold shrink-0', className)}>
            {initials(name)}
        </span>
    );
}
