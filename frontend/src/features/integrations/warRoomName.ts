export const DEFAULT_TEMPLATE = 'inc-{id}-{title}';
// mirrors backend app/services/war_room_naming.py
const clean = (s: string) => s.toLowerCase().replace(/[^a-z0-9_-]/g, '-').replace(/-{2,}/g, '-').replace(/^-+|-+$/g, '');
export function previewName(tpl: string, id: number, title: string): string {
    const d = new Date();
    const date = `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}`;
    const build = (t: string) => clean((tpl.trim() || DEFAULT_TEMPLATE).replace(/\{(id|title|date)\}/g, (_, k) => (k === 'id' ? String(id).padStart(4, '0') : k === 'date' ? date : t)));
    let slug = clean(title), name = build(slug);
    while (name.length > 80 && slug) { slug = slug.slice(0, Math.max(0, slug.length - (name.length - 80))).replace(/-+$/, ''); name = build(slug); }
    return name.slice(0, 80).replace(/^-+|-+$/g, '');
}
