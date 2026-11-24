/** Alert conditions: the field/op vocabulary shared by suppression rules and the Alerts list filter (backend:
 *  schemas/suppression.py Condition). Matching ignores case. */
export type Condition = { field: string; op: string; value: string };

export const OPS: [string, string][] = [
    ['equals', 'equals'], ['not_equals', 'is not'], ['contains', 'contains'],
    ['starts_with', 'starts with'], ['ends_with', 'ends with'], ['in_cidr', 'in CIDR'],
];
export const BASE_FIELDS = ['source', 'title', 'severity'];
/** Mirrors the backend FIELD_RE: a base field or payload.<path> of up to 8 keys. */
export const FIELD_RE = /^(source|title|severity|payload(\.[A-Za-z0-9_\-@$]{1,64}){1,8})$/;

export const opLabel = (op: string) => OPS.find(([k]) => k === op)?.[1] ?? op;
export const describe = (c: Condition) => `${c.field} ${opLabel(c.op)} "${c.value}"`;

/** Flatten an alert payload into payload.<path> = value pairs (scalars only) for the field picker. */
export function payloadPaths(obj: unknown, prefix = 'payload', out: [string, string][] = []): [string, string][] {
    if (out.length >= 40 || obj === null || obj === undefined) return out;
    if (typeof obj !== 'object') { out.push([prefix, String(obj)]); return out; }
    for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
        if (/^[A-Za-z0-9_\-@$]{1,64}$/.test(k)) payloadPaths(v, `${prefix}.${k}`, out);
    }
    return out;
}
