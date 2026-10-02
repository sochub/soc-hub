import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChevronRight, ChevronLeft, Check } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import ArtifactForm from '../artifacts/ArtifactForm';
import { api } from '../../api/client';
import type { PlaybookSummary } from '../../types';
import Modal, { modalInput, modalLabel, btnPrimary, btnSecondary } from '../../components/layout/Modal';

interface NewCaseModalProps {
    show: boolean;
    onClose: () => void;
    onSubmit: (data: { title: string; description: string; severity: string; status: string; tags: string[]; source: string; artifacts: any[]; playbook_template_id?: string }) => void;
    isSubmitting: boolean;
}

export default function NewCaseModal({ show, onClose, onSubmit, isSubmitting }: NewCaseModalProps) {
    const [step, setStep] = useState<'basic' | 'artifacts' | 'review'>('basic');
    const [caseData, setCaseData] = useState({
        title: '',
        description: '',
        severity: 'medium',
        status: 'open',
        source: 'user-reported',
        tags: '',
        playbook_template_id: ''
    });
    const [artifacts, setArtifacts] = useState<Array<{ type: string; value: string; isolated: boolean; enrichment?: any }>>([]);

    const { data: playbooks = [] } = useQuery({
        queryKey: ['playbooks', 'mine'],
        queryFn: async () => (await api.get('/playbooks/')).data as PlaybookSummary[],
        enabled: show,
    });

    const handleClose = () => {
        setStep('basic');
        setCaseData({
            title: '',
            description: '',
            severity: 'medium',
            status: 'open',
            source: 'user-reported',
            tags: '',
            playbook_template_id: ''
        });
        setArtifacts([]);
        onClose();
    };

    const handleSubmit = () => {
        const tagsArray = caseData.tags.split(',').map(t => t.trim()).filter(t => t);
        onSubmit({
            ...caseData,
            tags: tagsArray,
            artifacts
        });
        handleClose();
    };

    const steps = [
        { id: 'basic', label: 'Basic Info' },
        { id: 'artifacts', label: 'Artifacts' },
        { id: 'review', label: 'Review' },
    ];

    const currentStepIndex = steps.findIndex(s => s.id === step);

    const footer = (
        <>
            {step !== 'basic' && (
                <button
                    onClick={() => setStep(step === 'review' ? 'artifacts' : 'basic')}
                    className={btnSecondary}
                >
                    <ChevronLeft size={16} />
                    Back
                </button>
            )}
            <div className="flex-1" />
            <button onClick={handleClose} className={btnSecondary}>
                Cancel
            </button>
            {step !== 'review' ? (
                <button
                    onClick={() => setStep(step === 'basic' ? 'artifacts' : 'review')}
                    disabled={step === 'basic' && (!caseData.title || !caseData.description)}
                    className={btnPrimary}
                >
                    Next
                    <ChevronRight size={16} />
                </button>
            ) : (
                <button onClick={handleSubmit} disabled={isSubmitting} className={btnPrimary}>
                    {isSubmitting ? 'Creating...' : 'Create Case'}
                </button>
            )}
        </>
    );

    return (
        <Modal open={show} onClose={handleClose} title="Create New Case" size="lg" footer={footer}>
            {/* Step Indicator */}
            <div className="flex items-center justify-between mb-8">
                {steps.map((s, index) => (
                    <div key={s.id} className="flex items-center flex-1">
                        <div className="flex items-center gap-3">
                            <div className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold transition-colors ${index < currentStepIndex ? 'bg-accent-600 text-white' :
                                index === currentStepIndex ? 'bg-accent-600 text-white' :
                                    'bg-zinc-100 text-zinc-400'
                                }`}>
                                {index < currentStepIndex ? <Check size={16} /> : index + 1}
                            </div>
                            <span className={`text-sm font-medium ${index <= currentStepIndex ? 'text-zinc-800' : 'text-zinc-400'
                                }`}>{s.label}</span>
                        </div>
                        {index < steps.length - 1 && (
                            <div className={`flex-1 h-0.5 mx-4 ${index < currentStepIndex ? 'bg-accent-600' : 'bg-zinc-100'
                                }`} />
                        )}
                    </div>
                ))}
            </div>

            {/* Step Content */}
            <div className="min-h-[300px]">
                <AnimatePresence mode="wait">
                    {step === 'basic' && (
                        <motion.div
                            key="basic"
                            initial={{ opacity: 0, x: 20 }}
                            animate={{ opacity: 1, x: 0 }}
                            exit={{ opacity: 0, x: -20 }}
                            className="space-y-4"
                        >
                            <div>
                                <label className={modalLabel}>Title</label>
                                <input
                                    type="text"
                                    value={caseData.title}
                                    onChange={(e) => setCaseData({ ...caseData, title: e.target.value })}
                                    className={modalInput}
                                    placeholder="e.g., Suspicious login attempts detected"
                                    required
                                />
                            </div>

                            <div>
                                <label className={modalLabel}>Description</label>
                                <textarea
                                    value={caseData.description}
                                    onChange={(e) => setCaseData({ ...caseData, description: e.target.value })}
                                    className={`${modalInput} min-h-[120px]`}
                                    placeholder="Describe the security incident..."
                                    required
                                />
                            </div>

                            <div>
                                <label className={modalLabel}>Severity</label>
                                <select
                                    value={caseData.severity}
                                    onChange={(e) => setCaseData({ ...caseData, severity: e.target.value })}
                                    className={modalInput}
                                >
                                    <option value="info">Info</option>
                                    <option value="low">Low</option>
                                    <option value="medium">Medium</option>
                                    <option value="high">High</option>
                                    <option value="critical">Critical</option>
                                </select>
                            </div>

                            <div className="grid grid-cols-2 gap-4">
                                <div>
                                    <label className={modalLabel}>Source</label>
                                    <select
                                        value={caseData.source}
                                        onChange={(e) => setCaseData({ ...caseData, source: e.target.value })}
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
                                    <label className={modalLabel}>Status</label>
                                    <select
                                        value={caseData.status}
                                        onChange={(e) => setCaseData({ ...caseData, status: e.target.value })}
                                        className={modalInput}
                                    >
                                        <option value="new">New</option>
                                        <option value="open">Open</option>
                                        <option value="in_progress">In Progress</option>
                                        <option value="pending">Pending</option>
                                        <option value="resolved">Resolved</option>
                                        <option value="closed">Closed</option>
                                    </select>
                                </div>
                            </div>

                            <div>
                                <label className={modalLabel}>Tags</label>
                                <input
                                    type="text"
                                    value={caseData.tags}
                                    onChange={(e) => setCaseData({ ...caseData, tags: e.target.value })}
                                    className={modalInput}
                                    placeholder="e.g. phishing, urgent, finance (comma separated)"
                                />
                            </div>

                            <div>
                                <label className={modalLabel}>Start from playbook <span className="text-zinc-400 font-normal">(optional)</span></label>
                                <select
                                    value={caseData.playbook_template_id}
                                    onChange={(e) => setCaseData({ ...caseData, playbook_template_id: e.target.value })}
                                    className={modalInput}
                                >
                                    <option value="">No playbook</option>
                                    {playbooks.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.task_count} tasks)</option>)}
                                </select>
                                {playbooks.length === 0 && (
                                    <p className="text-xs text-zinc-400 mt-1">Import playbooks from the Playbooks page to use them here.</p>
                                )}
                            </div>
                        </motion.div>
                    )}

                    {step === 'artifacts' && (
                        <motion.div
                            key="artifacts"
                            initial={{ opacity: 0, x: 20 }}
                            animate={{ opacity: 1, x: 0 }}
                            exit={{ opacity: 0, x: -20 }}
                        >
                            <ArtifactForm artifacts={artifacts} setArtifacts={setArtifacts} />
                        </motion.div>
                    )}

                    {step === 'review' && (
                        <motion.div
                            key="review"
                            initial={{ opacity: 0, x: 20 }}
                            animate={{ opacity: 1, x: 0 }}
                            exit={{ opacity: 0, x: -20 }}
                            className="space-y-4"
                        >
                            <div className="p-4 border border-zinc-200 bg-zinc-50">
                                <h3 className="text-sm font-semibold text-zinc-500 uppercase mb-3">Case Details</h3>
                                <div className="space-y-2">
                                    <div>
                                        <p className="text-xs text-zinc-400">Title</p>
                                        <p className="text-zinc-800">{caseData.title}</p>
                                    </div>
                                    <div>
                                        <p className="text-xs text-zinc-400">Description</p>
                                        <p className="text-zinc-800">{caseData.description}</p>
                                    </div>
                                    <div>
                                        <p className="text-xs text-zinc-400">Severity</p>
                                        <p className="text-zinc-800 capitalize">{caseData.severity}</p>
                                    </div>
                                </div>
                            </div>

                            {artifacts.length > 0 && (
                                <div className="p-4 border border-zinc-200 bg-zinc-50">
                                    <h3 className="text-sm font-semibold text-zinc-500 uppercase mb-3">
                                        Artifacts ({artifacts.length})
                                    </h3>
                                    <div className="space-y-2">
                                        {artifacts.map((artifact, index) => (
                                            <div key={index} className="flex items-center gap-3 p-2 bg-zinc-100 rounded">
                                                <span className="text-xs text-zinc-400 uppercase font-semibold">{artifact.type}</span>
                                                <span className="text-sm text-zinc-800 font-mono">{artifact.value}</span>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            )}
                        </motion.div>
                    )}
                </AnimatePresence>
            </div>
        </Modal>
    );
}
