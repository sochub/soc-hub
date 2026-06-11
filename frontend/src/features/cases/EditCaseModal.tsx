import { useState, useEffect } from 'react';
import { X } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

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
        <AnimatePresence>
            {show && (
                <motion.div
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4"
                    onClick={onClose}
                >
                    <motion.div
                        initial={{ scale: 0.95, opacity: 0 }}
                        animate={{ scale: 1, opacity: 1 }}
                        exit={{ scale: 0.95, opacity: 0 }}
                        onClick={(e) => e.stopPropagation()}
                        className="glass-panel p-6 rounded-xl max-w-2xl w-full border border-zinc-200"
                    >
                        <div className="flex items-center justify-between mb-6">
                            <h2 className="text-xl font-bold text-zinc-900">Edit Case #{caseData.id}</h2>
                            <button
                                onClick={onClose}
                                className="p-2 text-zinc-500 hover:text-zinc-900 hover:bg-zinc-100 rounded-lg transition-colors"
                            >
                                <X size={20} />
                            </button>
                        </div>

                        <div className="space-y-4">
                            <div>
                                <label className="block text-sm font-medium text-zinc-700 mb-2">Title</label>
                                <input
                                    type="text"
                                    value={formData.title}
                                    onChange={(e) => setFormData({ ...formData, title: e.target.value })}
                                    className="w-full bg-white border border-zinc-200 rounded-lg px-4 py-2 text-zinc-900 focus:outline-none focus:ring-2 focus:ring-white/50 focus:border-white/50"
                                />
                            </div>

                            <div>
                                <label className="block text-sm font-medium text-zinc-700 mb-2">Description</label>
                                <textarea
                                    value={formData.description}
                                    onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                                    className="w-full bg-white border border-zinc-200 rounded-lg px-4 py-2 text-zinc-900 focus:outline-none focus:ring-2 focus:ring-white/50 focus:border-white/50 min-h-[120px]"
                                />
                            </div>

                            <div className="grid grid-cols-2 gap-4">
                                <div>
                                    <label className="block text-sm font-medium text-zinc-700 mb-2">Severity</label>
                                    <select
                                        value={formData.severity}
                                        onChange={(e) => setFormData({ ...formData, severity: e.target.value })}
                                        className="w-full bg-white border border-zinc-200 rounded-lg px-4 py-2 text-zinc-900 focus:outline-none focus:ring-2 focus:ring-white/50 focus:border-white/50"
                                    >
                                        <option value="info">Info</option>
                                        <option value="low">Low</option>
                                        <option value="medium">Medium</option>
                                        <option value="high">High</option>
                                        <option value="critical">Critical</option>
                                    </select>
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-zinc-700 mb-2">Status</label>
                                    <select
                                        value={formData.status}
                                        onChange={(e) => setFormData({ ...formData, status: e.target.value })}
                                        className="w-full bg-white border border-zinc-200 rounded-lg px-4 py-2 text-zinc-900 focus:outline-none focus:ring-2 focus:ring-white/50 focus:border-white/50"
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
                                    <label className="block text-sm font-medium text-zinc-700 mb-2">Source</label>
                                    <select
                                        value={formData.source}
                                        onChange={(e) => setFormData({ ...formData, source: e.target.value })}
                                        className="w-full bg-white border border-zinc-200 rounded-lg px-4 py-2 text-zinc-900 focus:outline-none focus:ring-2 focus:ring-white/50 focus:border-white/50"
                                    >
                                        <option value="user-reported">User Reported</option>
                                        <option value="siem">SIEM</option>
                                        <option value="email">Email</option>
                                        <option value="phone">Phone</option>
                                        <option value="other">Other</option>
                                    </select>
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-zinc-700 mb-2">Tags</label>
                                    <input
                                        type="text"
                                        value={formData.tags}
                                        onChange={(e) => setFormData({ ...formData, tags: e.target.value })}
                                        className="w-full bg-white border border-zinc-200 rounded-lg px-4 py-2 text-zinc-900 focus:outline-none focus:ring-2 focus:ring-white/50 focus:border-white/50"
                                        placeholder="Comma separated tags"
                                    />
                                </div>
                            </div>

                            <div className="flex justify-end gap-3 pt-6 mt-6 border-t border-zinc-200">
                                <button
                                    onClick={onClose}
                                    className="px-4 py-2 bg-zinc-100 hover:bg-zinc-200 text-zinc-700 rounded-lg font-medium transition-colors"
                                >
                                    Cancel
                                </button>
                                <button
                                    onClick={handleSubmit}
                                    disabled={isSubmitting}
                                    className="px-4 py-2 bg-white hover:bg-zinc-200 text-black rounded-lg font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                                >
                                    {isSubmitting ? 'Saving...' : 'Save Changes'}
                                </button>
                            </div>
                        </div>
                    </motion.div>
                </motion.div>
            )}
        </AnimatePresence>
    );
}
