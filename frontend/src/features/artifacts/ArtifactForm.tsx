import { useState, useCallback } from 'react';
import { Plus, Trash2, AlertCircle, Link2 } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { api } from '../../api/client';
import { useArtifactTypes } from './useArtifactTypes';
import { Badge, Button, Input, Select } from '../../components/ui';

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
            <div className="flex items-start gap-3 p-3 bg-panel rounded-md border border-line">
                <AlertCircle size={20} className="text-fg-3 mt-0.5 shrink-0" aria-hidden />
                <div className="text-sm text-fg-3">
                    <p className="font-medium text-fg-2 mb-1">Add indicators of compromise (IOCs)</p>
                    <p>Attach file hashes, IPs, domains, URLs, or email addresses to this case for tracking and analysis.</p>
                </div>
            </div>

            {/* Artifact List */}
            {artifacts.length > 0 && (
                <div className="space-y-2">
                    <p className="text-sm font-medium text-fg-2">Added artifacts <span className="num">({artifacts.length})</span></p>
                    {artifacts.map((artifact, index) => (
                        <motion.div
                            key={index}
                            initial={{ opacity: 0, y: -10 }}
                            animate={{ opacity: 1, y: 0 }}
                            className="flex items-center gap-3 p-3 bg-raised rounded-md border border-line"
                        >
                            <div className="flex-1">
                                <div className="flex items-center gap-2">
                                    <p className="text-xs text-fg-3 font-semibold">
                                        {ARTIFACT_TYPES.find(t => t.value === artifact.type)?.label}
                                    </p>
                                    {artifact.isolated && (
                                        <Badge variant="medium">Isolated</Badge>
                                    )}
                                </div>
                                <p className="text-sm text-fg font-mono mt-0.5">{artifact.value}</p>
                            </div>
                            <Button
                                variant="ghost" size="icon"
                                onClick={() => removeArtifact(index)}
                                className="hover:text-severity-critical"
                                aria-label={`Remove ${artifact.value}`}
                            >
                                <Trash2 size={16} />
                            </Button>
                        </motion.div>
                    ))}
                </div>
            )}

            {/* Add new artifact */}
            <div className="space-y-3">
                <p className="text-sm font-medium text-fg-2">Add new artifact</p>
                <div className="flex gap-3">
                    <Select
                        aria-label="Artifact type"
                        value={newArtifact.type}
                        onChange={(e) => setNewArtifact({ ...newArtifact, type: e.target.value })}
                        className="w-auto"
                    >
                        {ARTIFACT_TYPES.map(type => (
                            <option key={type.value} value={type.value}>{type.label}</option>
                        ))}
                    </Select>
                    <Input
                        aria-label="Artifact value"
                        value={newArtifact.value}
                        onChange={(e) => {
                            setNewArtifact({ ...newArtifact, value: e.target.value });
                            if (e.target.value.length > 3) checkArtifact(e.target.value);
                            else setPotentialMatch(null);
                        }}
                        onKeyDown={(e) => e.key === 'Enter' && !potentialMatch && addArtifact(false)}
                        placeholder="Enter artifact value..."
                        className="flex-1 min-w-0"
                    />
                    {!potentialMatch && (
                        <Button onClick={() => addArtifact(false)}>
                            <Plus size={16} aria-hidden />
                            Add
                        </Button>
                    )}
                </div>

                {/* Isolated checkbox */}
                <label className="flex items-center gap-2 text-sm text-fg-3 cursor-pointer">
                    <input
                        type="checkbox"
                        checked={newArtifact.isolated}
                        onChange={(e) => setNewArtifact({ ...newArtifact, isolated: e.target.checked })}
                    />
                    Create as isolated (case-specific copy, won't be shared)
                </label>

                <AnimatePresence>
                    {potentialMatch && (
                        <motion.div
                            initial={{ opacity: 0, height: 0 }}
                            animate={{ opacity: 1, height: 'auto' }}
                            exit={{ opacity: 0, height: 0 }}
                            className="bg-accent/10 border border-accent/40 rounded-md p-3 overflow-hidden"
                        >
                            <div className="flex items-start gap-3">
                                <Link2 size={18} className="text-accent mt-0.5" aria-hidden />
                                <div>
                                    <p className="text-sm font-medium text-accent">Existing artifact found</p>
                                    <p className="text-xs text-accent mt-1">
                                        This value already exists across {potentialMatch.case_count} case{potentialMatch.case_count !== 1 ? 's' : ''}.
                                    </p>
                                    <div className="flex gap-3 mt-3">
                                        <Button size="sm" onClick={linkExisting}>
                                            <Link2 size={12} aria-hidden />
                                            Link existing
                                        </Button>
                                        <Button size="sm" variant="secondary" onClick={createSeparate}>
                                            Create separate
                                        </Button>
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
