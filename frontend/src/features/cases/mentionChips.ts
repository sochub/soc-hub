import { MENTION_RE } from './mentionRe';

/** A picked mention, tracked by its position in the visible text (range [start, start + 1 + name.length)). */
export interface Chip { name: string; id: number; start: number }

/** Turn serialized text into visible text (@Name) plus position-tracked chips. */
export function parse(serialized: string): { display: string; chips: Chip[] } {
    const chips: Chip[] = [];
    let display = '';
    let last = 0;
    for (const m of serialized.matchAll(MENTION_RE)) {
        const at = m.index ?? 0;
        display += serialized.slice(last, at);
        chips.push({ name: m[1], id: Number(m[2]), start: display.length });
        display += `@${m[1]}`;
        last = at + m[0].length;
    }
    return { display: display + serialized.slice(last), chips };
}

/** Replace exactly each chip's range by its token; everything else (incl. hand-typed @Name) stays plain. */
export function serialize(display: string, chips: Chip[]): string {
    let out = '';
    let pos = 0;
    for (const c of chips) {
        out += display.slice(pos, c.start) + `@[${c.name}](user:${c.id})`;
        pos = c.start + c.name.length + 1;
    }
    return out + display.slice(pos);
}

/** Serialized text with every mention token reduced to plain `@Name` (for non-comment entries). */
export function stripMentions(serialized: string): string {
    return serialized.replace(MENTION_RE, (_m, name: string) => `@${name}`);
}

const WORD = /[A-Za-z0-9_]/;

/**
 * Shift or drop chips for an edit old -> next, found by common prefix/suffix.
 * `caret` is the textarea selectionStart after the edit; it bounds where the edit can have started
 * so that inserting a character equal to the one before a chip (typing "@" before "@Alice") is not
 * mistaken for an edit inside the chip.
 */
export function applyEdit(oldText: string, next: string, chips: Chip[], caret?: number): Chip[] {
    const max = Math.min(oldText.length, next.length);
    let p = 0;
    while (p < max && oldText[p] === next[p]) p++;
    let s = 0;
    while (s < max - p && oldText[oldText.length - 1 - s] === next[next.length - 1 - s]) s++;
    if (caret !== undefined) {
        const inserted = next.length - p - s;
        const editStart = Math.max(0, caret - inserted);
        if (editStart < p) {
            p = editStart;
            s = 0;
            while (s < max - p && oldText[oldText.length - 1 - s] === next[next.length - 1 - s]) s++;
        }
    }
    const removedEnd = oldText.length - s;
    const delta = next.length - oldText.length;
    const out: Chip[] = [];
    for (const c of chips) {
        const end = c.start + c.name.length + 1;
        let start: number;
        if (end <= p) {
            start = c.start;
            // text typed right after the name ("@Al" + "ice") turns the chip into a different word
            if (end === p && WORD.test(next[end] ?? '')) continue;
        } else if (c.start >= removedEnd) start = c.start + delta;
        else continue; // the edit touches this chip
        if (next.slice(start, start + c.name.length + 1) === `@${c.name}`) out.push({ ...c, start });
    }
    return out;
}
