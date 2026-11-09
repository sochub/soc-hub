import { useRef, useState } from 'react';
import type { ClipboardEvent, DragEvent } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';

const ACCEPTED = ['image/png', 'image/jpeg', 'image/gif', 'image/webp'];

const detail = (err: unknown) => {
    const d = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
    return typeof d === 'string' ? d : 'Image upload failed';
};

/** Replace the first occurrence of `from` in the textarea; dispatches `input` so React state follows. */
function replaceText(ta: HTMLTextAreaElement, from: string, to: string) {
    const i = ta.value.indexOf(from);
    if (i < 0) return;
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
    setter?.call(ta, ta.value.slice(0, i) + to + ta.value.slice(i + from.length));
    ta.dispatchEvent(new Event('input', { bubbles: true }));
}

/** Paste / drag-drop images into a markdown textarea: uploads inline and inserts an image link. */
export function usePasteImage(
    caseId: number,
    getTextarea: () => HTMLTextAreaElement | null,
    /** State-level swap used when the textarea unmounted mid-upload; returns false if `from` is no longer in the value. */
    replaceInValue?: (from: string, to: string) => boolean,
) {
    // Always call the latest closure: the one captured at upload start would hold a stale value.
    const fallback = useRef(replaceInValue);
    fallback.current = replaceInValue;
    const qc = useQueryClient();
    const [error, setError] = useState<string | null>(null);

    // Live textarea when still mounted (keeps caret/undo); otherwise the owner's state updater; else tell the user.
    const swap = (from: string, to: string) => {
        const live = getTextarea();
        if (live?.isConnected) { replaceText(live, from, to); return; }
        if (!fallback.current?.(from, to)) setError(`Editor closed before the image finished uploading; remove any "${from}" text left in the field.`);
    };

    const upload = async (file: File, ta: HTMLTextAreaElement) => {
        // Brackets/parens/newlines in a filename would break the markdown image syntax.
        const name = (file.name || 'image').replace(/[[\]()\n\r]/g, '_');
        const placeholder = `![uploading ${name}…]()`;
        ta.focus();
        document.execCommand('insertText', false, placeholder);
        try {
            const fd = new FormData();
            fd.append('file', file);
            fd.append('inline', 'true');
            const { data } = await api.post(`/cases/${caseId}/attachments`, fd, { headers: { 'Content-Type': 'multipart/form-data' } });
            qc.invalidateQueries({ queryKey: ['attachments', caseId] });
            swap(placeholder, `![${name}](/api/v1/cases/${caseId}/attachments/${data.id}/raw)`);
        } catch (e) {
            swap(placeholder, '');
            setError(detail(e));
        }
    };

    const handle = (files: File[], ev: { preventDefault: () => void }) => {
        const images = files.filter((f) => f.type.startsWith('image/'));
        if (!images.length) return; // not ours: let the browser handle it
        const ta = getTextarea();
        if (!ta) return;
        ev.preventDefault();
        setError(null);
        const ok = images.filter((f) => ACCEPTED.includes(f.type));
        if (ok.length < images.length) setError('Only PNG, JPEG, GIF or WebP images can be embedded.');
        ok.forEach((f) => { void upload(f, ta); });
    };

    return {
        onPaste: (ev: ClipboardEvent) => ev.clipboardData.getData('text/plain') ? undefined : handle(Array.from(ev.clipboardData.files), ev),
        onDrop: (ev: DragEvent) => handle(Array.from(ev.dataTransfer.files), ev),
        error,
    };
}
