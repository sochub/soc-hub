import { useEffect, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { AnimatePresence, motion } from 'framer-motion';
import CopilotChat from './CopilotChat';

/** Extract a case id from the current path, or null when not on a case detail view. */
function caseIdFromPath(pathname: string): number | null {
    const match = pathname.match(/^\/cases\/(\d+)$/);
    return match ? parseInt(match[1], 10) : null;
}

/**
 * Copilot docked as a right-side panel under the top bar. No launcher of its own: the top bar's Copilot button
 * dispatches window event 'copilot:toggle', and ⌘J / Ctrl+J toggles it from anywhere.
 */
export default function CopilotWidget() {
    const location = useLocation();
    const [open, setOpen] = useState(false);
    const caseId = caseIdFromPath(location.pathname);
    const panelRef = useRef<HTMLElement>(null);
    const returnFocus = useRef<HTMLElement | null>(null);

    useEffect(() => {
        const toggle = () => setOpen(o => {
            if (!o) returnFocus.current = document.activeElement as HTMLElement | null;
            return !o;
        });
        const onKey = (e: KeyboardEvent) => {
            if ((e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === 'j') {
                e.preventDefault();
                toggle();
            }
        };
        window.addEventListener('copilot:toggle', toggle);
        window.addEventListener('keydown', onKey);
        return () => { window.removeEventListener('copilot:toggle', toggle); window.removeEventListener('keydown', onKey); };
    }, []);

    // Escape closes while focus is inside the panel; focus moves into the panel on open and back on close.
    useEffect(() => {
        if (open) {
            panelRef.current?.querySelector<HTMLElement>('textarea, button')?.focus();
            return;
        }
        returnFocus.current?.focus?.();
        returnFocus.current = null;
    }, [open]);

    return (
        <AnimatePresence>
            {open && (
                <motion.aside
                    ref={panelRef}
                    initial={{ x: '100%' }}
                    animate={{ x: 0 }}
                    exit={{ x: '100%' }}
                    transition={{ type: 'tween', duration: 0.18, ease: 'easeOut' }}
                    aria-label="Copilot"
                    onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); setOpen(false); } }}
                    className="fixed top-14 bottom-0 right-0 z-40 w-full sm:w-[400px] border-l border-line bg-surface shadow-lg"
                >
                    {/* key forces a fresh chat (and session fetch) when switching between case/general context */}
                    <CopilotChat key={caseId ?? 'general'} caseId={caseId} onClose={() => setOpen(false)} />
                </motion.aside>
            )}
        </AnimatePresence>
    );
}
