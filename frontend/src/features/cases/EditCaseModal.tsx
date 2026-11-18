import { useState, useEffect } from 'react';
import type { CaseEditPayload } from '../../types';
import Modal, { modalInput, modalLabel, btnPrimary, btnSecondary } from '../../components/layout/Modal';
import { CASE_OUTCOMES } from './outcomes';

interface EditCaseModalProps {
    show: boolean;
    onClose: () => void;
    caseData: {
        id: number;
        title: string;
        description: string;
        severity: string;
        status: string;
        source: string;
        tags: string[];
        outcome?: string | null;
        closing_note?: string | null;
    };
    onSubmit: (data: CaseEditPayload) => void;
    isSubmitting: boolean;
    error?: string | null;
}

export default function EditCaseModal({ show, onClose, caseData, onSubmit, isSubmitting, error }: EditCaseModalProps) {
    const [formData, setFormData] = useState({
        title: '',
        description: '',
        severity: '',
        source: '',
        tags: '',
        outcome: '',
        closing_note: '',
        reason: ''
    });

    useEffect(() => {
        if (caseData) {
            setFormData({
                title: caseData.title,
                description: caseData.description,
                severity: caseData.severity,
                source: caseData.source || 'user-reported',
                tags: caseData.tags ? caseData.tags.join(', ') : '',
                outcome: caseData.outcome || '',
                closing_note: caseData.closing_note || '',
                reason: ''
            });
        }
    }, [caseData, show]);

    const [localError, setLocalError] = useState<string | null>(null);
    const isClosed = caseData.status === 'closed';
    const handleSubmit = () => {
        if (isClosed && (!formData.outcome || !formData.closing_note.trim())) {
            setLocalError('A closed case needs an outcome and a closing note.');
            return;
        }
        setLocalError(null);
        // Status changes live in the header status chip and the Close case dialog, not here.
        const { reason, ...rest } = formData;
        const changed = formData.severity !== caseData.severity;
        const tagsArray = formData.tags.split(',').map(t => t.trim()).filter(t => t);
        onSubmit({
            ...rest,
            ...(changed && reason.trim() ? { reason: reason.trim() } : {}),
            ...(isClosed ? { outcome: formData.outcome || null, closing_note: formData.closing_note || null } : { outcome: undefined, closing_note: undefined }),
            tags: tagsArray
        });
        // the parent closes the modal on success, so a failed save keeps the form and shows why
    };

    return (
        <Modal
            open={show}
            onClose={onClose}
            title={`Edit case #${caseData.id}`}
            size="lg"
            footer={<>
                <button onClick={onClose} className={btnSecondary}>
                    Cancel
                </button>
                <button onClick={handleSubmit} disabled={isSubmitting} className={btnPrimary}>
                    {isSubmitting ? 'Saving…' : 'Save changes'}
                </button>
            </>}
        >
            <div className="space-y-4">
                {(localError || error) && <p role="alert" className="text-sm text-severity-critical">{localError || error}</p>}
                <div>
                    <label htmlFor="edit-case-title" className={modalLabel}>Title</label>
                    <input
                        id="edit-case-title"
                        type="text"
                        value={formData.title}
                        onChange={(e) => setFormData({ ...formData, title: e.target.value })}
                        className={modalInput}
                    />
                </div>

                <div>
                    <label htmlFor="edit-case-description" className={modalLabel}>Description</label>
                    <textarea
                        id="edit-case-description"
                        value={formData.description}
                        onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                        className={`${modalInput} min-h-[120px]`}
                    />
                </div>

                <div className="grid grid-cols-2 gap-4">
                    <div>
                        <label htmlFor="edit-case-severity" className={modalLabel}>Severity</label>
                        <select
                            id="edit-case-severity"
                            value={formData.severity}
                            onChange={(e) => setFormData({ ...formData, severity: e.target.value })}
                            className={modalInput}
                        >
                            <option value="info">Info</option>
                            <option value="low">Low</option>
                            <option value="medium">Medium</option>
                            <option value="high">High</option>
                            <option value="critical">Critical</option>
                        </select>
                    </div>
                    <div>
                        <label htmlFor="edit-case-source" className={modalLabel}>Source</label>
                        <select
                            id="edit-case-source"
                            value={formData.source}
                            onChange={(e) => setFormData({ ...formData, source: e.target.value })}
                            className={modalInput}
                        >
                            <option value="user-reported">User reported</option>
                            <option value="siem">SIEM</option>
                            <option value="email">Email</option>
                            <option value="phone">Phone</option>
                            <option value="other">Other</option>
                        </select>
                    </div>
                </div>

                <div>
                        <label htmlFor="edit-case-tags" className={modalLabel}>Tags</label>
                        <input
                            id="edit-case-tags"
                            type="text"
                            value={formData.tags}
                            onChange={(e) => setFormData({ ...formData, tags: e.target.value })}
                            className={modalInput}
                            placeholder="Comma separated tags"
                        />
                </div>

                {formData.severity !== caseData.severity && (
                    <div>
                        <label htmlFor="edit-case-reason" className={modalLabel}>Reason <span className="text-fg-3 font-normal">(optional, shown on the timeline and in the war room)</span></label>
                        <input
                            id="edit-case-reason"
                            type="text"
                            value={formData.reason}
                            onChange={(e) => setFormData({ ...formData, reason: e.target.value })}
                            className={modalInput}
                            maxLength={300}
                        />
                    </div>
                )}

                {isClosed && <>
                <div>
                    <label htmlFor="edit-case-outcome" className={modalLabel}>Outcome</label>
                    <select
                        id="edit-case-outcome"
                        value={formData.outcome}
                        onChange={(e) => setFormData({ ...formData, outcome: e.target.value })}
                        className={modalInput}
                    >
                        <option value="">Not set</option>
                        {CASE_OUTCOMES.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                </div>

                <div>
                    <label htmlFor="edit-case-closing_note" className={modalLabel}>Closing note</label>
                    <textarea
                        id="edit-case-closing_note"
                        value={formData.closing_note}
                        onChange={(e) => setFormData({ ...formData, closing_note: e.target.value })}
                        className={`${modalInput} min-h-[80px]`}
                        maxLength={8000}
                    />
                </div>
                </>}
            </div>
        </Modal>
    );
}
