import { useEffect, useId, useRef, type ReactNode } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { X } from 'lucide-react';
import { cn } from '../../lib/utils';

const SIZES = { md: 'max-w-lg', lg: 'max-w-3xl', xl: 'max-w-5xl' } as const;

/** Shared light-theme class strings for modal form controls and footer buttons. */
export const modalInput = 'w-full bg-panel border border-line-strong px-3 py-2 text-sm text-fg placeholder:text-muted focus:outline-hidden focus:border-accent focus:ring-1 focus:ring-accent';
export const modalLabel = 'block text-xs font-medium text-fg-2 mb-1.5';
export const btnPrimary = 'h-9 px-4 rounded-full bg-brand-gradient text-sm font-semibold hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-1.5';
export const btnSecondary = 'h-9 px-4 rounded-full border border-line-strong bg-panel text-fg-2 text-sm hover:bg-surface disabled:opacity-50 inline-flex items-center gap-1.5';
export const btnDanger = 'h-9 px-4 rounded-full bg-red-600 text-white text-sm font-medium hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-1.5';

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
                    className="fixed inset-0 z-100 bg-black/60 backdrop-blur-[2px] flex items-start sm:items-center justify-center p-4 overflow-y-auto"
                    onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
                >
                    <div
                        ref={panelRef}
                        role="dialog" aria-modal="true" aria-labelledby={titleId}
                        className={cn('bg-panel border border-line shadow-2xl w-full max-h-[85vh] flex flex-col text-fg', SIZES[size])}
                    >
                        <div className="flex items-center justify-between gap-4 px-5 py-3.5 border-b border-line shrink-0">
                            <h2 id={titleId} className="font-semibold text-fg truncate">{title}</h2>
                            <button type="button" onClick={onClose} aria-label="Close dialog"
                                className="p-1.5 text-muted hover:text-fg hover:bg-raised">
                                <X size={16} />
                            </button>
                        </div>
                        <div data-modal-body className="min-h-0 overflow-y-auto p-5">{children}</div>
                        {footer && (
                            <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-line bg-surface shrink-0">
                                {footer}
                            </div>
                        )}
                    </div>
                </motion.div>
            )}
        </AnimatePresence>
    );
}
