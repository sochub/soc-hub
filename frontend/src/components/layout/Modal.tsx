import { useEffect, useId, useRef, type ReactNode } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { X } from 'lucide-react';
import { cn } from '../../lib/utils';

const SIZES = { md: 'max-w-lg', lg: 'max-w-3xl', xl: 'max-w-5xl' } as const;

/** Class strings for call sites that can't use the components/ui primitives; they match Button/Input exactly. */
export const modalInput = 'w-full bg-panel border border-line-strong rounded-md px-3 py-2 text-sm text-fg placeholder:text-muted focus:outline-2 focus:-outline-offset-1 focus:outline-accent disabled:opacity-50';
export const modalLabel = 'block text-xs font-medium text-fg-2 mb-1.5';
export const btnPrimary = 'h-9 px-3.5 rounded-md bg-accent text-on-accent text-sm font-semibold hover:bg-accent-hover disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-1.5 whitespace-nowrap focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent';
export const btnSecondary = 'h-9 px-3.5 rounded-md border border-line-strong bg-panel text-fg text-sm hover:bg-raised disabled:opacity-50 inline-flex items-center gap-1.5 whitespace-nowrap focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent';
export const btnDanger = 'h-9 px-3.5 rounded-md border border-severity-critical/50 text-severity-critical text-sm font-medium hover:bg-severity-critical hover:text-white disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-1.5 whitespace-nowrap focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent';

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
                    className="fixed inset-0 z-100 bg-fg/40 flex items-start sm:items-center justify-center p-4 overflow-y-auto"
                    onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
                >
                    <div
                        ref={panelRef}
                        role="dialog" aria-modal="true" aria-labelledby={titleId}
                        className={cn('bg-panel border border-line-strong rounded-md shadow-[0_12px_40px_-12px_rgb(0_0_0/0.35)] w-full max-h-[85vh] flex flex-col text-fg', SIZES[size])}
                    >
                        <div className="flex items-center justify-between gap-4 px-5 py-3.5 border-b border-line shrink-0">
                            <h2 id={titleId} className="text-base font-semibold text-fg truncate">{title}</h2>
                            <button type="button" onClick={onClose} aria-label="Close dialog"
                                className="p-1.5 rounded-sm text-fg-3 hover:text-fg hover:bg-raised">
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
