import { useRef, useState } from 'react';
import { Bold, Code, Heading, Italic, Link, List, ListOrdered, Quote, SquareCode, Strikethrough } from 'lucide-react';
import ReactMarkdown, { defaultUrlTransform } from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { cn } from '../lib/utils';
import { MENTION_RE } from '../features/cases/mentionRe';

// Mention tokens become `[@Name](user:id)` links, rendered as chips; raw HTML is never rendered.
const keepMentions = (url: string) => (url.startsWith('user:') ? url : defaultUrlTransform(url));

export function Markdown({ content, className }: { content: string; className?: string }) {
    const src = content.replace(MENTION_RE, (_m, name: string, id: string) => `[@${name.replace(/[\\[\]*_`]/g, '\\$&')}](user:${id})`);
    return (
        <div className={cn('copilot-markdown break-words [&_p]:whitespace-pre-wrap', className)}>
            <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                urlTransform={keepMentions}
                components={{
                    a: ({ href, children }) => href?.startsWith('user:')
                        ? <span className="text-accent font-medium bg-accent/10 px-0.5">{children}</span>
                        : <a href={href} target="_blank" rel="noopener noreferrer nofollow">{children}</a>,
                }}
            >{src}</ReactMarkdown>
        </div>
    );
}

// ponytail: execCommand('insertText') fires a real input event, so any textarea (incl. MentionTextarea's
// chip tracking) sees a normal edit and Ctrl+Z works; it is deprecated but has no replacement for this yet.
function insert(ta: HTMLTextAreaElement, from: number, to: number, text: string, selStart: number, selEnd: number) {
    ta.focus();
    ta.setSelectionRange(from, to);
    document.execCommand('insertText', false, text);
    ta.setSelectionRange(from + selStart, from + selEnd);
}

function wrap(ta: HTMLTextAreaElement, before: string, after = before, placeholder = 'text') {
    const { selectionStart: s, selectionEnd: e, value } = ta;
    const sel = value.slice(s, e) || placeholder;
    insert(ta, s, e, before + sel + after, before.length, before.length + sel.length);
}

function prefixLines(ta: HTMLTextAreaElement, prefix: (i: number) => string) {
    const { selectionStart: s, selectionEnd: e, value } = ta;
    const from = value.lastIndexOf('\n', s - 1) + 1;
    const text = value.slice(from, e).split('\n').map((l, i) => prefix(i) + l).join('\n');
    insert(ta, from, e, text, text.length, text.length);
}

const ACTIONS: { icon: typeof Bold; label: string; key?: string; run: (ta: HTMLTextAreaElement) => void }[] = [
    { icon: Heading, label: 'Heading', run: (ta) => prefixLines(ta, () => '### ') },
    { icon: Bold, label: 'Bold', key: 'b', run: (ta) => wrap(ta, '**') },
    { icon: Italic, label: 'Italic', key: 'i', run: (ta) => wrap(ta, '_') },
    { icon: Strikethrough, label: 'Strikethrough', run: (ta) => wrap(ta, '~~') },
    { icon: Code, label: 'Inline code', key: 'e', run: (ta) => wrap(ta, '`', '`', 'code') },
    { icon: SquareCode, label: 'Code block', run: (ta) => wrap(ta, '```\n', '\n```', 'code') },
    { icon: Link, label: 'Link', key: 'k', run: (ta) => wrap(ta, '[', '](https://)', 'link text') },
    { icon: Quote, label: 'Quote', run: (ta) => prefixLines(ta, () => '> ') },
    { icon: List, label: 'Bulleted list', run: (ta) => prefixLines(ta, () => '- ') },
    { icon: ListOrdered, label: 'Numbered list', run: (ta) => prefixLines(ta, (i) => `${i + 1}. `) },
];

/** Write/Preview tabs and a formatting toolbar around any textarea; the child keeps owning the value. */
export function MarkdownField({ value, children }: { value: string; children: React.ReactNode }) {
    const [preview, setPreview] = useState(false);
    const box = useRef<HTMLDivElement>(null);
    const textarea = () => box.current?.querySelector('textarea');
    const tab = (on: boolean) => cn('px-2 py-0.5 rounded-sm', on ? 'bg-raised text-fg' : 'text-muted hover:text-fg-2');
    const onKeyDown = (ev: React.KeyboardEvent) => {
        if (!(ev.metaKey || ev.ctrlKey) || ev.shiftKey || ev.altKey || !(ev.target instanceof HTMLTextAreaElement)) return;
        const action = ACTIONS.find((a) => a.key === ev.key.toLowerCase());
        if (action) { ev.preventDefault(); action.run(ev.target); }
    };
    return (
        <div className="space-y-1">
            <div className="flex flex-wrap items-center gap-1 text-[11px]">
                <button type="button" className={tab(!preview)} onClick={() => setPreview(false)}>Write</button>
                <button type="button" className={tab(preview)} onClick={() => setPreview(true)}>Preview</button>
                {!preview && (
                    <div className="flex items-center gap-0.5 ml-2" role="toolbar" aria-label="Formatting">
                        {ACTIONS.map(({ icon: Icon, label, key, run }) => {
                            const title = key ? `${label} (⌘/Ctrl+${key.toUpperCase()})` : label;
                            return (
                                <button key={label} type="button" title={title} aria-label={label}
                                    onMouseDown={(ev) => ev.preventDefault()}
                                    onClick={() => { const ta = textarea(); if (ta) run(ta); }}
                                    className="p-1 rounded-sm text-muted hover:text-fg hover:bg-raised">
                                    <Icon size={13} />
                                </button>
                            );
                        })}
                    </div>
                )}
                <span className="ml-auto text-muted">Markdown supported</span>
            </div>
            {preview
                ? <div className="min-h-[76px] bg-panel border border-line rounded-lg px-3 py-2 text-sm text-fg-2">
                    {value.trim() ? <Markdown content={value} /> : <span className="text-muted">Nothing to preview</span>}
                  </div>
                : <div ref={box} onKeyDown={onKeyDown}>{children}</div>}
        </div>
    );
}
