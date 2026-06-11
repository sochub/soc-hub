import { useState, useCallback } from 'react';
import { Plus, Trash2, AlertCircle, Link2 } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { api } from '../../api/client';

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

const ARTIFACT_TYPES = [
    { value: 'hash', label: 'File Hash (MD5/SHA1/SHA256)' },
    { value: 'ip', label: 'IP Address' },
    { value: 'domain', label: 'Domain' },
    { value: 'url', label: 'URL' },
    { value: 'email', label: 'Email Address' },
];

export default function ArtifactForm({ artifacts, setArtifacts }: ArtifactFormProps) {
    const [newArtifact, setNewArtifact] = useState({ type: 'hash', value: '', isolated: false });
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
            setNewArtifact({ type: 'hash', value: '', isolated: false });
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
            <div className="flex items-start gap-3 p-3 bg-white rounded-lg border border-zinc-200">
                <AlertCircle size={20} className="text-zinc-500 mt-0.5 flex-shrink-0" />
                <div className="text-sm text-zinc-500">
                    <p className="font-medium text-zinc-700 mb-1">Add Indicators of Compromise (IOCs)</p>
                    <p>Attach file hashes, IPs, domains, URLs, or email addresses to this case for tracking and analysis.</p>
                </div>
            </div>

            {/* Artifact List */}
            {artifacts.length > 0 && (
                <div className="space-y-2">
                    <p className="text-sm font-medium text-zinc-700">Added Artifacts ({artifacts.length})</p>
                    {artifacts.map((artifact, index) => (
                        <motion.div
                            key={index}
                            initial={{ opacity: 0, y: -10 }}
                            animate={{ opacity: 1, y: 0 }}
                            className="flex items-center gap-3 p-3 bg-zinc-100 rounded-lg border border-zinc-200"
                        >
                            <div className="flex-1">
                                <div className="flex items-center gap-2">
                                    <p className="text-xs text-zinc-400 uppercase font-semibold">
                                        {ARTIFACT_TYPES.find(t => t.value === artifact.type)?.label}
                                    </p>
                                    {artifact.isolated && (
                                        <span className="text-[10px] bg-amber-50 text-amber-700 border border-amber-200 px-1.5 py-0.5 rounded uppercase font-bold">
                                            Isolated
                                        </span>
                                    )}
                                </div>
                                <p className="text-sm text-zinc-800 font-mono mt-0.5">{artifact.value}</p>
                            </div>
                            <button
                                onClick={() => removeArtifact(index)}
                                className="p-2 text-zinc-500 hover:text-red-700 hover:bg-zinc-200 rounded-lg transition-colors"
                            >
                                <Trash2 size={16} />
                            </button>
                        </motion.div>
                    ))}
                </div>
            )}

            {/* Add New Artifact */}
            <div className="space-y-3">
                <p className="text-sm font-medium text-zinc-700">Add New Artifact</p>
                <div className="flex gap-3">
                    <select
                        value={newArtifact.type}
                        onChange={(e) => setNewArtifact({ ...newArtifact, type: e.target.value })}
                        className="bg-white border border-zinc-200 rounded-lg px-3 py-2 text-zinc-900 focus:outline-none focus:ring-2 focus:ring-white/50 focus:border-white/50"
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
                        className="flex-1 bg-white border border-zinc-200 rounded-lg px-4 py-2 text-zinc-900 focus:outline-none focus:ring-2 focus:ring-white/50 focus:border-white/50"
                    />
                    {!potentialMatch && (
                        <button
                            onClick={() => addArtifact(false)}
                            className="px-4 py-2 bg-accent-600 hover:bg-accent-700 text-white rounded-lg font-medium transition-colors flex items-center gap-2"
                        >
                            <Plus size={18} />
                            Add
                        </button>
                    )}
                </div>

                {/* Isolated checkbox */}
                <label className="flex items-center gap-2 text-sm text-zinc-500 cursor-pointer">
                    <input
                        type="checkbox"
                        checked={newArtifact.isolated}
                        onChange={(e) => setNewArtifact({ ...newArtifact, isolated: e.target.checked })}
                        className="rounded border-zinc-300 bg-white text-accent-600 focus:ring-accent-500"
                    />
                    Create as isolated (case-specific copy, won't be shared)
                </label>

                <AnimatePresence>
                    {potentialMatch && (
                        <motion.div
                            initial={{ opacity: 0, height: 0 }}
                            animate={{ opacity: 1, height: 'auto' }}
                            exit={{ opacity: 0, height: 0 }}
                            className="bg-blue-50 border border-blue-200 rounded-lg p-3 overflow-hidden"
                        >
                            <div className="flex items-start gap-3">
                                <Link2 size={18} className="text-accent-600 mt-0.5" />
                                <div>
                                    <p className="text-sm font-medium text-blue-200">Existing Artifact Found</p>
                                    <p className="text-xs text-blue-700 mt-1">
                                        This value already exists across {potentialMatch.case_count} case{potentialMatch.case_count !== 1 ? 's' : ''}.
                                    </p>
                                    <div className="flex gap-3 mt-3">
                                        <button
                                            onClick={linkExisting}
                                            className="text-xs bg-accent-500/20 hover:bg-accent-700/30 text-blue-200 px-3 py-1.5 rounded transition-colors flex items-center gap-1.5"
                                        >
                                            <Link2 size={12} />
                                            Link Existing
                                        </button>
                                        <button
                                            onClick={createSeparate}
                                            className="text-xs bg-zinc-200 hover:bg-zinc-300 text-zinc-700 px-3 py-1.5 rounded transition-colors"
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
