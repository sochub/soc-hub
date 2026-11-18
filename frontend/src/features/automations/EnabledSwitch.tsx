import { cn } from '../../lib/utils';

/** On/off switch for a workflow's enabled state (list rows and the editor toolbar). */
export default function EnabledSwitch({ checked, label, disabled, title, onClick, children }: {
    checked: boolean; label: string; disabled?: boolean; title?: string; onClick: () => void; children?: React.ReactNode;
}) {
    return (
        <button type="button" role="switch" aria-checked={checked} aria-label={children ? undefined : label}
            title={title} disabled={disabled} onClick={onClick}
            className="inline-flex items-center gap-2 text-sm text-fg-2 disabled:opacity-50 disabled:cursor-not-allowed">
            <span aria-hidden className={cn('w-9 h-5 relative rounded-full transition-colors', checked ? 'bg-accent' : 'bg-line-strong')}>
                <span className={cn('absolute top-0.5 w-4 h-4 rounded-full bg-panel transition-all', checked ? 'left-[18px]' : 'left-0.5')} />
            </span>
            {children}
        </button>
    );
}
