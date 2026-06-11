import { useEffect, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { AnimatePresence, motion } from 'framer-motion';
import { Sparkles } from 'lucide-react';
import CopilotChat from './CopilotChat';

/** Extract a case id from the current path, or null when not on a case detail view. */
function caseIdFromPath(pathname: string): number | null {
    const match = pathname.match(/^\/cases\/(\d+)$/);
    return match ? parseInt(match[1], 10) : null;
}

export default function CopilotWidget() {
    const location = useLocation();
    const [open, setOpen] = useState(false);
    const caseId = caseIdFromPath(location.pathname);

    // Close on Escape.
    useEffect(() => {
        if (!open) return;
        const onKey = (e: KeyboardEvent) => {
            if (e.key === 'Escape') setOpen(false);
        };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [open]);

    return (
        <>
            {/* Launcher */}
            <motion.button
                onClick={() => setOpen(true)}
                initial={false}
                animate={{ scale: open ? 0 : 1, opacity: open ? 0 : 1 }}
                transition={{ duration: 0.15 }}
                aria-label="Open copilot"
                className="fixed bottom-6 right-6 z-40 w-14 h-14 rounded-2xl bg-gradient-to-tr from-brand-600 to-brand-400 text-slate-950 shadow-xl shadow-brand-900/30 flex items-center justify-center hover:shadow-brand-glow focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
            >
                <Sparkles size={24} />
            </motion.button>

            {/* Slide-over panel */}
            <AnimatePresence>
                {open && (
                    <>
                        <motion.div
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            exit={{ opacity: 0 }}
                            onClick={() => setOpen(false)}
                            className="fixed inset-0 z-40 bg-slate-950/50 backdrop-blur-sm"
                            aria-hidden="true"
                        />
                        <motion.aside
                            initial={{ x: '100%' }}
                            animate={{ x: 0 }}
                            exit={{ x: '100%' }}
                            transition={{ type: 'spring', stiffness: 320, damping: 34 }}
                            role="dialog"
                            aria-label="Copilot"
                            className="fixed top-0 right-0 z-50 h-full w-full max-w-md border-l border-slate-800/60 bg-slate-950 shadow-2xl"
                        >
                            {/* key forces a fresh chat (and session fetch) when switching between case/general context */}
                            <CopilotChat key={caseId ?? 'general'} caseId={caseId} onClose={() => setOpen(false)} />
                        </motion.aside>
                    </>
                )}
            </AnimatePresence>
        </>
    );
}
