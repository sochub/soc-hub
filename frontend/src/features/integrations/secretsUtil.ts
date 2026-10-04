import { isAxiosError } from 'axios';

const LABEL = '(?!-)[a-z0-9-]{1,63}(?<!-)';
const TLD = '(?=[a-z0-9-]*[a-z])(?!-)[a-z0-9-]{2,63}(?<!-)';
const HOST = new RegExp(`^(?=.{1,253}$)(${LABEL}\\.)+${TLD}$`);
export const NAME_RE = /^[A-Z][A-Z0-9_]{1,63}$/;

/** Mirrors backend valid_host_pattern minus the tenant-allowlist exception.
 *  'allowlist' = IP or single label: only valid if on the tenant HTTP allowlist, the server decides. */
export function hostStatus(p: string): 'ok' | 'allowlist' | 'bad' {
    if (!p || p !== p.trim() || p !== p.toLowerCase()) return 'bad';
    if (p.startsWith('*.')) return HOST.test(p.slice(2)) ? 'ok' : 'bad';
    if (HOST.test(p)) return 'ok';
    if (p.includes(':') ? /^[0-9a-f:.]+$/.test(p) : /^\d+(\.\d+){3}$/.test(p) || /^[a-z0-9-]+$/.test(p)) return 'allowlist';
    return 'bad';
}

export const parseHosts = (text: string): string[] => text.split('\n').map((x) => x.trim()).filter(Boolean);

/** Error text from the secrets router: `detail` is a string or a list of `{msg}`. Never includes request values. */
export function errText(e: unknown, fb: string): string {
    const d: unknown = isAxiosError(e) ? e.response?.data?.detail : undefined;
    if (typeof d === 'string') return d;
    if (Array.isArray(d)) {
        const m = d.map((x) => (typeof x?.msg === 'string' ? x.msg : null)).filter(Boolean);
        if (m.length) return m.join('; ');
    }
    return fb;
}

export function suggestName(host: string, key: string): string {
    let n = `${host}_${key}`.toUpperCase().replace(/[^A-Z0-9]/g, '_');
    if (!/^[A-Z]/.test(n)) n = 'S_' + n;
    return n.slice(0, 64);
}

export const secretTemplate = (name: string) => `{{ secrets.${name} }}`;
