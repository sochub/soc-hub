import { useEffect, useId, useRef, type ReactNode } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { X } from 'lucide-react';
import { cn } from '../../lib/utils';

const SIZES = { md: 'max-w-lg', lg: 'max-w-3xl', xl: 'max-w-5xl' } as const;

/** Shared light-theme class strings for modal form controls and footer buttons. */
export const modalInput = 'w-full bg-white border border-zinc-300 px-3 py-2 text-sm text-zinc-900 placeholder:text-zinc-400 focus:outline-hidden focus:border-accent-500 focus:ring-1 focus:ring-accent-500';
export const modalLabel = 'block text-xs font-medium text-zinc-600 mb-1.5';
export const btnPrimary = 'h-9 px-4 bg-accent-600 text-white text-sm font-medium hover:bg-accent-700 disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-1.5';
export const btnSecondary = 'h-9 px-4 border border-zinc-300 bg-white text-zinc-700 text-sm hover:bg-zinc-50 disabled:opacity-50 inline-flex items-center gap-1.5';
export const btnDanger = 'h-9 px-4 bg-red-600 text-white text-sm font-medium hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-1.5';

const FOCUSABLE = 'input:not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])';

interface ModalProps {
    open: boolean;
    onClose: () => void;
    title: ReactNode;
    size?: keyof typeof SIZES;
    footer?: ReactNode;
    children: ReactNode;
}

export default function Modal({ open, onClose, title, size = 'lg', footer, children }: ModalProps) {
    const titleId = useId();
    const panelRef = useRef<HTMLDivElement>(null);
    const onCloseRef = useRef(onClose);
    useEffect(() => { onCloseRef.current = onClose; });

    useEffect(() => {
        if (!open) return;
        const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onCloseRef.current(); };
        document.addEventListener('keydown', onKey);
        // Focus the first focusable element in the body (falls back to the close button).
        const t = window.setTimeout(() => {
            const panel = panelRef.current;
            if (!panel) return;
            const target = panel.querySelector<HTMLElement>(`[data-modal-body] :is(${FOCUSABLE})`)
                ?? panel.querySelector<HTMLElement>(FOCUSABLE);
            target?.focus();
        }, 0);
        return () => { document.removeEventListener('keydown', onKey); window.clearTimeout(t); };
    }, [open]);

    return (
        <AnimatePresence>
            {open && (
                <motion.div
                    initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.15 }}
                    className="fixed inset-0 z-100 bg-zinc-900/40 backdrop-blur-[2px] flex items-start sm:items-center justify-center p-4 overflow-y-auto"
                    onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
                >
                    <div
                        ref={panelRef}
                        role="dialog" aria-modal="true" aria-labelledby={titleId}
                        className={cn('bg-white border border-zinc-200 shadow-2xl w-full max-h-[85vh] flex flex-col text-zinc-900', SIZES[size])}
                    >
                        <div className="flex items-center justify-between gap-4 px-5 py-3.5 border-b border-zinc-200 shrink-0">
                            <h2 id={titleId} className="font-semibold text-zinc-900 truncate">{title}</h2>
                            <button type="button" onClick={onClose} aria-label="Close dialog"
                                className="p-1.5 text-zinc-400 hover:text-zinc-900 hover:bg-zinc-100">
                                <X size={16} />
                            </button>
                        </div>
                        <div data-modal-body className="min-h-0 overflow-y-auto p-5">{children}</div>
                        {footer && (
                            <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-zinc-200 bg-zinc-50 shrink-0">
                                {footer}
                            </div>
                        )}
                    </div>
                </motion.div>
            )}
        </AnimatePresence>
    );
}
