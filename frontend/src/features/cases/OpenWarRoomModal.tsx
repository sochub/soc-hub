import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import { api } from '../../api/client';
import Modal, { modalInput, modalLabel, btnPrimary, btnSecondary } from '../../components/layout/Modal';
import { previewName } from '../integrations/warRoomName';

interface Props { open: boolean; onClose: () => void; caseId: number; title: string; template: string | null }

export default function OpenWarRoomModal({ open, onClose, caseId, title, template }: Props) {
    const qc = useQueryClient();
    const [name, setName] = useState<string | null>(null);
    const [listen, setListen] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const prefilled = previewName(template ?? '', caseId, title);
    const value = name ?? prefilled;

    const create = useMutation({
        mutationFn: async () => (await api.post(`/cases/${caseId}/war-room`, { name: value.trim() || null, listen })).data,
        onSuccess: () => { qc.invalidateQueries({ queryKey: ['case'] }); setName(null); setError(null); onClose(); },
        onError: (e) => setError(isAxiosError(e) && typeof e.response?.data?.detail === 'string' ? e.response.data.detail : 'Could not create the channel.'),
    });
    const close = () => { setName(null); setError(null); onClose(); };

    return (
        <Modal open={open} onClose={close} title="Open war room" size="md"
            footer={<>
                <button type="button" className={btnSecondary} onClick={close}>Cancel</button>
                <button type="button" className={btnPrimary} disabled={create.isPending} onClick={() => create.mutate()}>
                    {create.isPending ? 'Creating…' : 'Create channel'}
                </button>
            </>}>
            <div className="space-y-4">
                <div>
                    <label className={modalLabel} htmlFor="war-room-name">Channel name</label>
                    <input id="war-room-name" className={modalInput} value={value} onChange={(e) => setName(e.target.value)} />
                </div>
                <label className="flex items-start gap-2 text-sm text-fg">
                    <input type="checkbox" className="mt-1" checked={listen} onChange={(e) => setListen(e.target.checked)} />
                    <span>SOC-bot listens to this channel
                        <span className="block text-xs text-fg-3">Suggests indicators and milestone times from the channel</span>
                    </span>
                </label>
                {error && <p role="alert" className="text-sm text-severity-critical">{error}</p>}
            </div>
        </Modal>
    );
}
