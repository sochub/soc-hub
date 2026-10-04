import { Fragment } from 'react';

import { MENTION_RE } from './mentionRe';

/** Renders comment text with @[Name](user:id) tokens as highlighted @Name (React text nodes only). */
export default function MentionText({ content }: { content: string }) {
    const parts: React.ReactNode[] = [];
    let last = 0;
    for (const m of content.matchAll(MENTION_RE)) {
        const start = m.index ?? 0;
        if (start > last) parts.push(<Fragment key={`t${last}`}>{content.slice(last, start)}</Fragment>);
        parts.push(
            <span key={`m${start}`} className="text-accent font-medium bg-accent/10 px-0.5">@{m[1]}</span>
        );
        last = start + m[0].length;
    }
    if (last < content.length) parts.push(<Fragment key={`t${last}`}>{content.slice(last)}</Fragment>);
    return <>{parts}</>;
}
