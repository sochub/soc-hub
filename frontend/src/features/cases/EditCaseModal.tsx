import { useState, useEffect } from 'react';
import type { CaseEditPayload } from '../../types';
import Modal, { modalInput, modalLabel, btnPrimary, btnSecondary } from '../../components/layout/Modal';

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
        status: '',
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
                status: caseData.status,
                source: caseData.source || 'user-reported',
                tags: caseData.tags ? caseData.tags.join(', ') : '',
                outcome: caseData.outcome || '',
                closing_note: caseData.closing_note || '',
                reason: ''
            });
        }
    }, [caseData, show]);

    const [localError, setLocalError] = useState<string | null>(null);
    const handleSubmit = () => {
        if (formData.status === 'closed' && !formData.outcome) {
            setLocalError('Pick an outcome to close the case.');
            return;
        }
        setLocalError(null);
        const { reason, ...rest } = formData;
        const changed = formData.status !== caseData.status || formData.severity !== caseData.severity;
        const tagsArray = formData.tags.split(',').map(t => t.trim()).filter(t => t);
        onSubmit({
            ...rest,
            ...(changed && reason.trim() ? { reason: reason.trim() } : {}),
            outcome: formData.outcome || null,
            closing_note: formData.closing_note || null,
            tags: tagsArray
        });
        // the parent closes the modal on success, so a failed save keeps the form and shows why
    };

    return (
        <Modal
            open={show}
            onClose={onClose}
            title={`Edit Case #${caseData.id}`}
            size="lg"
            footer={<>
                <button onClick={onClose} className={btnSecondary}>
                    Cancel
                </button>
                <button onClick={handleSubmit} disabled={isSubmitting} className={btnPrimary}>
                    {isSubmitting ? 'Saving...' : 'Save Changes'}
                </button>
            </>}
        >
            <div className="space-y-4">
                {(localError || error) && <p role="alert" className="text-sm text-severity-critical">{localError || error}</p>}
                <div>
                    <label className={modalLabel}>Title</label>
                    <input
                        type="text"
                        value={formData.title}
                        onChange={(e) => setFormData({ ...formData, title: e.target.value })}
                        className={modalInput}
                    />
                </div>

                <div>
                    <label className={modalLabel}>Description</label>
                    <textarea
                        value={formData.description}
                        onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                        className={`${modalInput} min-h-[120px]`}
                    />
                </div>

                <div className="grid grid-cols-2 gap-4">
                    <div>
                        <label className={modalLabel}>Severity</label>
                        <select
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
                        <label className={modalLabel}>Status</label>
                        <select
                            value={formData.status}
                            onChange={(e) => setFormData({ ...formData, status: e.target.value })}
                            className={modalInput}
                        >
                            <option value="new">New</option>
                            <option value="open">Open</option>
                            <option value="investigating">Investigating</option>
                            <option value="resolved">Resolved</option>
                            <option value="closed">Closed</option>
                        </select>
                    </div>
                </div>

                <div className="grid grid-cols-2 gap-4">
                    <div>
                        <label className={modalLabel}>Source</label>
                        <select
                            value={formData.source}
                            onChange={(e) => setFormData({ ...formData, source: e.target.value })}
                            className={modalInput}
                        >
                            <option value="user-reported">User Reported</option>
                            <option value="siem">SIEM</option>
                            <option value="email">Email</option>
                            <option value="phone">Phone</option>
                            <option value="other">Other</option>
                        </select>
                    </div>
                    <div>
                        <label className={modalLabel}>Tags</label>
                        <input
                            type="text"
                            value={formData.tags}
                            onChange={(e) => setFormData({ ...formData, tags: e.target.value })}
                            className={modalInput}
                            placeholder="Comma separated tags"
                        />
                    </div>
                </div>

                {(formData.status !== caseData.status || formData.severity !== caseData.severity) && (
                    <div>
                        <label className={modalLabel}>Reason <span className="text-muted font-normal">(optional, shown on the timeline and in the war room)</span></label>
                        <input
                            type="text"
                            value={formData.reason}
                            onChange={(e) => setFormData({ ...formData, reason: e.target.value })}
                            className={modalInput}
                            maxLength={300}
                        />
                    </div>
                )}

                <div>
                    <label className={modalLabel}>Outcome <span className="text-muted font-normal">(required to close)</span></label>
                    <select
                        value={formData.outcome}
                        onChange={(e) => setFormData({ ...formData, outcome: e.target.value })}
                        className={modalInput}
                    >
                        <option value="">Not set</option>
                        <option value="unknown">Unknown</option>
                        <option value="false_positive">False Positive</option>
                        <option value="true_positive_with_impact">True Positive With Impact</option>
                        <option value="true_positive_without_impact">True Positive Without Impact</option>
                        <option value="legitimate">Legitimate</option>
                    </select>
                </div>

                <div>
                    <label className={modalLabel}>Closing note <span className="text-muted font-normal">(required to close)</span></label>
                    <textarea
                        value={formData.closing_note}
                        onChange={(e) => setFormData({ ...formData, closing_note: e.target.value })}
                        className={`${modalInput} min-h-[80px]`}
                        maxLength={8000}
                    />
                </div>
            </div>
        </Modal>
    );
}
