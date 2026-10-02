import { useState, useEffect } from 'react';
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
    };
    onSubmit: (data: { title: string; description: string; severity: string; status: string; tags: string[]; source: string }) => void;
    isSubmitting: boolean;
}

export default function EditCaseModal({ show, onClose, caseData, onSubmit, isSubmitting }: EditCaseModalProps) {
    const [formData, setFormData] = useState({
        title: '',
        description: '',
        severity: '',
        status: '',
        source: '',
        tags: ''
    });

    useEffect(() => {
        if (caseData) {
            setFormData({
                title: caseData.title,
                description: caseData.description,
                severity: caseData.severity,
                status: caseData.status,
                source: caseData.source || 'user-reported',
                tags: caseData.tags ? caseData.tags.join(', ') : ''
            });
        }
    }, [caseData, show]);

    const handleSubmit = () => {
        const tagsArray = formData.tags.split(',').map(t => t.trim()).filter(t => t);
        onSubmit({
            ...formData,
            tags: tagsArray
        });
        onClose();
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
            </div>
        </Modal>
    );
}
