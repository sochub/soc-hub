import { useState, useCallback } from 'react';
import { Plus, Trash2, AlertCircle, Link2 } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { api } from '../../api/client';
import { useArtifactTypes } from './useArtifactTypes';

interface ArtifactEntry {
    type: string;
    value: string;
    isolated: boolean;
    enrichment?: any;
}

interface ArtifactFormProps {
    artifacts: ArtifactEntry[];
    setArtifacts: (artifacts: ArtifactEntry[]) => void;
}

export default function ArtifactForm({ artifacts, setArtifacts }: ArtifactFormProps) {
    const { options: ARTIFACT_TYPES } = useArtifactTypes();
    const [newArtifact, setNewArtifact] = useState({ type: 'file_hash', value: '', isolated: false });
    const [potentialMatch, setPotentialMatch] = useState<any>(null);
    const checkArtifact = useCallback(async (value: string) => {
        if (!value.trim()) return;
        try {
            const response = await api.get(`/artifacts/search/?value=${encodeURIComponent(value)}`);
            if (response.data.length > 0) {
                setPotentialMatch(response.data[0]);
            } else {
                setPotentialMatch(null);
            }
        } catch (error) {
            console.error("Error searching artifacts:", error);
            setPotentialMatch(null);
        }
    }, []);

    const addArtifact = (isolated: boolean = false) => {
        if (newArtifact.value.trim()) {
            setArtifacts([...artifacts, { ...newArtifact, isolated }]);
            setNewArtifact({ type: 'file_hash', value: '', isolated: false });
            setPotentialMatch(null);
        }
    };

    const linkExisting = () => {
        // Link existing = not isolated (will reuse the shared artifact)
        addArtifact(false);
    };

    const createSeparate = () => {
        // Create separate = isolated copy
        addArtifact(true);
    };

    const removeArtifact = (index: number) => {
        setArtifacts(artifacts.filter((_, i) => i !== index));
    };

    return (
        <div className="space-y-4">
            <div className="flex items-start gap-3 p-3 bg-panel rounded-lg border border-line">
                <AlertCircle size={20} className="text-fg-3 mt-0.5 shrink-0" />
                <div className="text-sm text-fg-3">
                    <p className="font-medium text-fg-2 mb-1">Add Indicators of Compromise (IOCs)</p>
                    <p>Attach file hashes, IPs, domains, URLs, or email addresses to this case for tracking and analysis.</p>
                </div>
            </div>

            {/* Artifact List */}
            {artifacts.length > 0 && (
                <div className="space-y-2">
                    <p className="text-sm font-medium text-fg-2">Added Artifacts ({artifacts.length})</p>
                    {artifacts.map((artifact, index) => (
                        <motion.div
                            key={index}
                            initial={{ opacity: 0, y: -10 }}
                            animate={{ opacity: 1, y: 0 }}
                            className="flex items-center gap-3 p-3 bg-raised rounded-lg border border-line"
                        >
                            <div className="flex-1">
                                <div className="flex items-center gap-2">
                                    <p className="text-xs text-muted uppercase font-semibold">
                                        {ARTIFACT_TYPES.find(t => t.value === artifact.type)?.label}
                                    </p>
                                    {artifact.isolated && (
                                        <span className="text-[10px] bg-amber-500/10 text-amber-300 border border-amber-500/30 px-1.5 py-0.5 rounded-sm uppercase font-bold">
                                            Isolated
                                        </span>
                                    )}
                                </div>
                                <p className="text-sm text-fg font-mono mt-0.5">{artifact.value}</p>
                            </div>
                            <button
                                onClick={() => removeArtifact(index)}
                                className="p-2 text-fg-3 hover:text-red-300 hover:bg-raised-2 rounded-lg transition-colors"
                            >
                                <Trash2 size={16} />
                            </button>
                        </motion.div>
                    ))}
                </div>
            )}

            {/* Add New Artifact */}
            <div className="space-y-3">
                <p className="text-sm font-medium text-fg-2">Add New Artifact</p>
                <div className="flex gap-3">
                    <select
                        value={newArtifact.type}
                        onChange={(e) => setNewArtifact({ ...newArtifact, type: e.target.value })}
                        className="bg-panel border border-line-strong px-3 py-2 text-sm text-fg focus:outline-hidden focus:border-accent focus:ring-1 focus:ring-accent"
                    >
                        {ARTIFACT_TYPES.map(type => (
                            <option key={type.value} value={type.value}>{type.label}</option>
                        ))}
                    </select>
                    <input
                        type="text"
                        value={newArtifact.value}
                        onChange={(e) => {
                            setNewArtifact({ ...newArtifact, value: e.target.value });
                            if (e.target.value.length > 3) checkArtifact(e.target.value);
                            else setPotentialMatch(null);
                        }}
                        onKeyDown={(e) => e.key === 'Enter' && !potentialMatch && addArtifact(false)}
                        placeholder="Enter artifact value..."
                        className="flex-1 min-w-0 bg-panel border border-line-strong px-3 py-2 text-sm text-fg focus:outline-hidden focus:border-accent focus:ring-1 focus:ring-accent"
                    />
                    {!potentialMatch && (
                        <button
                            onClick={() => addArtifact(false)}
                            className="px-4 py-2 bg-accent hover:bg-accent-hover text-on-accent rounded-lg font-medium transition-colors flex items-center gap-2"
                        >
                            <Plus size={18} />
                            Add
                        </button>
                    )}
                </div>

                {/* Isolated checkbox */}
                <label className="flex items-center gap-2 text-sm text-fg-3 cursor-pointer">
                    <input
                        type="checkbox"
                        checked={newArtifact.isolated}
                        onChange={(e) => setNewArtifact({ ...newArtifact, isolated: e.target.checked })}
                        className="rounded-sm border-line-strong bg-panel text-accent focus:ring-accent"
                    />
                    Create as isolated (case-specific copy, won't be shared)
                </label>

                <AnimatePresence>
                    {potentialMatch && (
                        <motion.div
                            initial={{ opacity: 0, height: 0 }}
                            animate={{ opacity: 1, height: 'auto' }}
                            exit={{ opacity: 0, height: 0 }}
                            className="bg-accent/10 border border-accent/40 rounded-lg p-3 overflow-hidden"
                        >
                            <div className="flex items-start gap-3">
                                <Link2 size={18} className="text-accent mt-0.5" />
                                <div>
                                    <p className="text-sm font-medium text-accent">Existing Artifact Found</p>
                                    <p className="text-xs text-accent mt-1">
                                        This value already exists across {potentialMatch.case_count} case{potentialMatch.case_count !== 1 ? 's' : ''}.
                                    </p>
                                    <div className="flex gap-3 mt-3">
                                        <button
                                            onClick={linkExisting}
                                            className="text-xs bg-accent/20 hover:bg-accent-hover/30 text-accent px-3 py-1.5 rounded-sm transition-colors flex items-center gap-1.5"
                                        >
                                            <Link2 size={12} />
                                            Link Existing
                                        </button>
                                        <button
                                            onClick={createSeparate}
                                            className="text-xs bg-raised-2 hover:bg-line-strong text-fg-2 px-3 py-1.5 rounded-sm transition-colors"
                                        >
                                            Create Separate
                                        </button>
                                    </div>
                                </div>
                            </div>
                        </motion.div>
                    )}
                </AnimatePresence>
            </div>
        </div>
    );
}
