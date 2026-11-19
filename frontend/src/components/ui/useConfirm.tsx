import { useCallback, useRef, useState } from 'react';
import Modal from '../layout/Modal';
import Button from './Button';

type Opts = { title: string; body?: React.ReactNode; confirmLabel?: string; danger?: boolean };

/**
 * In-app replacement for window.confirm():
 *   const { confirm, dialog } = useConfirm();
 *   if (await confirm({ title: 'Delete this rule?', danger: true })) remove.mutate(id);
 *   ...render {dialog} once in the component.
 */
export function useConfirm() {
    const [opts, setOpts] = useState<Opts | null>(null);
    const resolver = useRef<(v: boolean) => void>(() => {});
    const confirm = useCallback((o: Opts) => new Promise<boolean>((res) => { resolver.current = res; setOpts(o); }), []);
    const done = (v: boolean) => { resolver.current(v); setOpts(null); };
    const dialog = (
        <Modal open={!!opts} onClose={() => done(false)} title={opts?.title ?? ''} size="md"
            footer={<>
                <Button variant="secondary" onClick={() => done(false)}>Cancel</Button>
                <Button variant={opts?.danger ? 'danger' : 'primary'} onClick={() => done(true)}>{opts?.confirmLabel ?? (opts?.danger ? 'Delete' : 'Confirm')}</Button>
            </>}>
            {opts?.body ? <div className="text-sm text-fg-2">{opts.body}</div> : <p className="text-sm text-fg-2">This can't be undone.</p>}
        </Modal>
    );
    return { confirm, dialog };
}
