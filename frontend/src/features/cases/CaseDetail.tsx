import { useParams, useLocation } from 'react-router-dom';
import Avatar from '../../components/Avatar';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { Shield, Database, MessageSquare, ArrowLeft, Hash, Calendar, Plus, X, Globe, FileText, Mail, Server, Edit, Trash2, Check, Pencil, Link2, Unlink } from 'lucide-react';
import { Link } from 'react-router-dom';
import { cn } from '../../lib/utils';
import { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import EditCaseModal from './EditCaseModal';
import PageContainer from '../../components/layout/PageContainer';
import Modal, { modalInput, modalLabel, btnPrimary, btnSecondary } from '../../components/layout/Modal';
import CaseTasks from './CaseTasks';
import CaseEvidence from './CaseEvidence';
import CaseReport from './CaseReport';
import TriagePanel from './TriagePanel';
import FollowButton from './FollowButton';
import MentionTextarea from './MentionTextarea';
import { stripMentions } from './mentionChips';
import MentionText from './MentionText';
import type { User } from '../../types';
import { formatDateTime } from '../../utils/datetime';
import SLAPanel from './SLAPanel';
import CaseAutomation from '../automations/CaseAutomation';
import EnrichmentPanel from '../enrichment/EnrichmentPanel';
import { useCanRun } from '../enrichment/useCanRun';
import { useArtifactTypes } from '../artifacts/useArtifactTypes';

const ARTIFACT_ICONS: Record<string, any> = {
    hash: FileText,
    file_hash: FileText,
    ip: Server,
    domain: Globe,
    url: Globe,
    email: Mail,
};

const EVENT_TYPES = ['comment', 'status_change', 'artifact_added', 'investigation', 'containment', 'remediation', 'other'];

const CASE_TABS = ['timeline', 'tasks', 'artifacts', 'evidence', 'report', 'network', 'audit', 'automation'] as const;
type CaseTab = typeof CASE_TABS[number];

export default function CaseDetail() {
    const { id } = useParams();
    const queryClient = useQueryClient();
    const [activeTab, setActiveTab] = useState<CaseTab>('timeline');
    // The Report tab reports unsaved edits; switching away asks first.
    const [reportDirty, setReportDirty] = useState(false);
    const [pendingTab, setPendingTab] = useState<CaseTab | null>(null);
    const selectTab = (tab: CaseTab) => {
        if (tab === activeTab) return;
        if (activeTab === 'report' && reportDirty) { setPendingTab(tab); return; }
        setPendingTab(null);
        setActiveTab(tab);
    };
    // CaseDetail stays mounted across :id changes; drop the previous case's report guard state.
    const [guardCaseId, setGuardCaseId] = useState(id);
    if (guardCaseId !== id) {
        setGuardCaseId(id);
        setReportDirty(false);
        setPendingTab(null);
    }
    const [showArtifactModal, setShowArtifactModal] = useState(false);
    const [intelOpen, setIntelOpen] = useState<number | null>(null);
    const canRunIntel = useCanRun();
    const [newArtifact, setNewArtifact] = useState({ type: 'file_hash', value: '' });
    const { options: artifactTypeOptions } = useArtifactTypes();
    const [showEditModal, setShowEditModal] = useState(false);

    // Timeline state
    const [showAddEvent, setShowAddEvent] = useState(false);
    const [newEvent, setNewEvent] = useState({ event_type: 'comment', content: '' });
    const [editingEventId, setEditingEventId] = useState<number | null>(null);
    const [editEventData, setEditEventData] = useState({ event_type: '', content: '' });

    const updateCaseMutation = useMutation({
        mutationFn: async (data: { title: string; description: string; severity: string; status: string; tags: string[]; source: string }) => {
            if (!id) return;
            const response = await api.put(`/cases/${id}`, data);
            return response.data;
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['case', id] });
            setShowEditModal(false);
        }
    });

    const { data: caseData, isLoading } = useQuery({
        queryKey: ['case', id],
        queryFn: async () => {
            const response = await api.get(`/cases/${id}`);
            return response.data;
        }
    });

    const { data: me } = useQuery({
        queryKey: ['currentUser'],
        queryFn: async () => (await api.get('/users/me')).data as User,
        staleTime: 300_000,
    });
    const isAdmin = !!me && (me.role === 'admin' || !!me.is_super_admin);
    // Mirrors the backend rule for editing/deleting timeline entries: admin, or the analyst who wrote it.
    const canModify = (event: { user_id: number | null }) =>
        isAdmin || (!!me && me.role === 'analyst' && event.user_id === me.id);

    // Deep link from a notification: /cases/:id#event-<id> -> scroll to and briefly highlight the event.
    const { hash } = useLocation();
    const handledHash = useRef<string | null>(null);
    const switchedFor = useRef<string | null>(null);
    const eventsReady = !!caseData;
    useEffect(() => {
        if (!eventsReady || !/^#event-\d+$/.test(hash) || handledHash.current === hash) return;
        if (activeTab !== 'timeline') {
            if (switchedFor.current !== hash) { switchedFor.current = hash; selectTab('timeline'); } // once, so the user can leave again
            return;
        }
        const el = document.getElementById(hash.slice(1));
        if (!el) return;
        handledHash.current = hash; // once per hash value
        el.scrollIntoView({ block: 'center' });
        el.classList.add('ring-2', 'ring-accent');
        setTimeout(() => el.classList.remove('ring-2', 'ring-accent'), 2000);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- selectTab only closes over state already in deps
    }, [eventsReady, hash, activeTab, caseData?.timeline_events?.length]);

    const { data: artifacts, isLoading: isLoadingArtifacts } = useQuery({
        queryKey: ['artifacts', id],
        queryFn: async () => {
            const response = await api.get(`/artifacts/case/${id}`);
            return response.data;
        },
        enabled: !!id
    });

    const addArtifactMutation = useMutation({
        mutationFn: async (data: { type: string; value: string; isolated?: boolean }) => {
            const response = await api.post('/artifacts/', {
                case_id: Number(id),
                artifact_type: data.type,
                value: data.value,
                isolated: data.isolated ?? false,
            });
            return response.data;
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['artifacts', id] });
            queryClient.invalidateQueries({ queryKey: ['artifacts', 'all'] });
            queryClient.invalidateQueries({ queryKey: ['case', id] });
            setShowArtifactModal(false);
            setNewArtifact({ type: 'file_hash', value: '' });
        }
    });

    const removeArtifactMutation = useMutation({
        mutationFn: async (artifactId: number) => {
            await api.delete(`/artifacts/${artifactId}/case/${id}`);
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['artifacts', id] });
            queryClient.invalidateQueries({ queryKey: ['artifacts', 'all'] });
            queryClient.invalidateQueries({ queryKey: ['case', id] });
        }
    });

    const { data: auditLogs, isLoading: isLoadingAuditLogs } = useQuery({
        queryKey: ['audit-logs', 'case', id],
        queryFn: async () => {
            const response = await api.get(`/audit-logs/case/${id}`);
            return response.data;
        },
        enabled: !!id
    });

    // Timeline mutations
    const addTimelineEventMutation = useMutation({
        mutationFn: async (data: { event_type: string; content: string }) => {
            const response = await api.post(`/cases/${id}/timeline`, data);
            return response.data;
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['case', id] });
            setShowAddEvent(false);
            setNewEvent({ event_type: 'comment', content: '' });
        }
    });

    const updateTimelineEventMutation = useMutation({
        mutationFn: async ({ eventId, data }: { eventId: number; data: { event_type: string; content: string } }) => {
            const response = await api.put(`/cases/${id}/timeline/${eventId}`, data);
            return response.data;
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['case', id] });
            setEditingEventId(null);
        }
    });

    const deleteTimelineEventMutation = useMutation({
        mutationFn: async (eventId: number) => {
            await api.delete(`/cases/${id}/timeline/${eventId}`);
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['case', id] });
        }
    });

    if (isLoading) {
        return (
            <PageContainer>
                <div className="flex items-center justify-center h-[50vh]">
                    <div className="flex flex-col items-center gap-4">
                        <div className="w-10 h-10 rounded-full border-4 border-zinc-200 border-t-accent-600 animate-spin" />
                    </div>
                </div>
            </PageContainer>
        );
    }

    if (!caseData) return <PageContainer><div className="text-red-700 font-mono">ERROR: CASE_NOT_FOUND</div></PageContainer>;

    const severityColor = caseData.severity === 'critical' ? 'text-severity-critical bg-red-50 border-red-200' :
        caseData.severity === 'high' ? 'text-severity-high bg-orange-50 border-orange-200' :
            caseData.severity === 'medium' ? 'text-severity-medium bg-amber-50 border-amber-200' :
                caseData.severity === 'low' ? 'text-severity-low bg-blue-50 border-blue-200' :
                    'text-zinc-600 bg-zinc-100 border-zinc-200';

    const storyEvents = caseData?.timeline_events?.map((event: any) => ({
        ...event,
        icon: event.event_type === 'artifact_added' ? Database :
            event.event_type === 'status_change' ? Shield :
                event.event_type === 'comment' ? MessageSquare : Calendar
    })) || [];

    const startEditing = (event: any) => {
        setEditingEventId(event.id);
        setEditEventData({ event_type: event.event_type, content: event.content });
    };

    return (
        <PageContainer>
            <div className="min-w-0 flex flex-col bg-white rounded-2xl border border-zinc-200 relative">
                {/* Top Decoration */}
                <div className="absolute top-0 left-0 right-0 h-1 rounded-t-2xl bg-gradient-to-r from-accent-500 via-accent-400 to-accent-500 opacity-50" />

                {/* Header */}
                <div className="p-6 pb-2">
                    <div className="flex items-center gap-4 mb-6">
                        <Link to="/" className="p-2 rounded-lg text-zinc-500 hover:text-zinc-900 hover:bg-zinc-100 transition-colors">
                            <ArrowLeft size={20} />
                        </Link>
                        <div className="flex-1">
                            <div className="flex items-center gap-3 mb-1">
                                <h1 className="text-2xl font-bold text-zinc-900 tracking-tight">{caseData.title}</h1>
                                <span className={cn(
                                    "px-2.5 py-0.5 rounded text-xs uppercase font-bold border tracking-wider",
                                    severityColor
                                )}>
                                    {caseData.severity}
                                </span>
                            </div>
                            <div className="flex items-center gap-4 text-xs font-mono text-zinc-400">
                                <span className="flex items-center gap-1.5">
                                    <Hash size={12} />
                                    ID: {String(caseData.id).padStart(4, '0')}
                                </span>
                                <span className="flex items-center gap-1.5">
                                    <Calendar size={12} />
                                    {formatDateTime(caseData.created_at, me?.timezone)}
                                </span>
                                <span className="flex items-center gap-1.5">
                                    <Shield size={12} />
                                    Owner: System
                                </span>
                            </div>
                        </div>
                        {id && <FollowButton caseId={parseInt(id)} ready={!!me} isOwner={!!me && caseData.owner_id === me.id} />}
                        <button
                            onClick={() => setShowEditModal(true)}
                            className="p-2 text-zinc-500 hover:text-zinc-900 hover:bg-zinc-100 rounded-lg transition-colors"
                        >
                            <Edit size={20} />
                        </button>
                    </div>

                    <div className="glass-panel p-5 rounded-xl mb-6 bg-white">
                        <h3 className="text-xs font-bold text-zinc-400 mb-2 uppercase tracking-wider flex items-center gap-2">
                            <MessageSquare size={12} />
                            Initial Report
                        </h3>
                        <p className="text-zinc-700 leading-relaxed text-sm">{caseData.description}</p>
                    </div>

                    {id && <TriagePanel caseId={parseInt(id)} />}
                    <SLAPanel caseData={caseData} />

                </div>

                {/* Tabs (sticky while the page scrolls inside <main>) */}
                <div className="sticky top-0 z-10 bg-white px-6 pt-3">
                    <div className="flex border-b border-zinc-200 relative space-x-6">
                        {CASE_TABS.map((tab) => (
                            <button
                                key={tab}
                                onClick={() => selectTab(tab)}
                                className={cn(
                                    "pb-3 text-sm font-medium transition-colors relative focus:outline-none",
                                    activeTab === tab ? "text-accent-700" : "text-zinc-400 hover:text-zinc-700"
                                )}
                            >
                                <span className="capitalize">{tab}</span>
                                {activeTab === tab && (
                                    <motion.div
                                        layoutId="activeTab"
                                        className="absolute bottom-[-1px] left-0 right-0 h-0.5 bg-accent-600"
                                    />
                                )}
                            </button>
                        ))}
                    </div>
                    {pendingTab && (
                        <div role="alert" className="flex flex-wrap items-center gap-2 text-xs text-amber-800 bg-amber-50 border border-amber-200 px-3 py-2 mt-2">
                            <span className="mr-auto">The report has unsaved changes. Discard them and switch to <span className="capitalize font-medium">{pendingTab}</span>?</span>
                            <button onClick={() => { setReportDirty(false); setActiveTab(pendingTab); setPendingTab(null); }}
                                className="px-2 py-1 bg-red-600 text-white rounded hover:bg-red-700">Discard</button>
                            <button onClick={() => setPendingTab(null)}
                                className="px-2 py-1 bg-white border border-zinc-300 text-zinc-700 rounded hover:bg-zinc-50">Keep editing</button>
                        </div>
                    )}
                </div>

                {/* Tab Content */}
                <div className="flex-1 min-h-[50vh] p-6 pt-2">
                    <AnimatePresence mode="wait">
                        <motion.div
                            key={activeTab}
                            initial={{ opacity: 0, y: 10 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0, y: -10 }}
                            transition={{ duration: 0.2 }}
                        >
                            {activeTab === 'timeline' && (
                                <div className="relative space-y-8 my-4">
                                    {/* Add Event Button */}
                                    <div className="flex justify-end mb-2">
                                        <button
                                            onClick={() => setShowAddEvent(true)}
                                            className="text-xs bg-zinc-100 hover:bg-zinc-200 text-zinc-700 px-3 py-1.5 rounded transition-colors flex items-center gap-2"
                                        >
                                            <Plus size={12} /> Add Event
                                        </button>
                                    </div>

                                    {/* Add Event Form */}
                                    <AnimatePresence>
                                        {showAddEvent && (
                                            <motion.div
                                                // Opacity only: a height animation needs overflow-hidden, which clips the @mention list.
                                                initial={{ opacity: 0, y: -4 }}
                                                animate={{ opacity: 1, y: 0 }}
                                                exit={{ opacity: 0, y: -4 }}
                                            >
                                                <div className="glass-panel p-4 rounded-xl border border-zinc-200 bg-white space-y-3">
                                                    <div className="flex items-center justify-between">
                                                        <h4 className="text-sm font-semibold text-zinc-800">New Timeline Event</h4>
                                                        <button onClick={() => setShowAddEvent(false)} className="text-zinc-400 hover:text-zinc-900">
                                                            <X size={16} />
                                                        </button>
                                                    </div>
                                                    <select
                                                        value={newEvent.event_type}
                                                        onChange={(e) => {
                                                            const t = e.target.value;
                                                            setNewEvent((n) => ({ ...n, event_type: t, content: t === 'comment' ? n.content : stripMentions(n.content) }));
                                                        }}
                                                        className="w-full bg-white border border-zinc-200 rounded-lg px-3 py-2 text-sm text-zinc-900 focus:outline-none focus:ring-1 focus:ring-white/50"
                                                    >
                                                        {EVENT_TYPES.map(t => (
                                                            <option key={t} value={t}>{t.replace('_', ' ')}</option>
                                                        ))}
                                                    </select>
                                                    {newEvent.event_type === 'comment' ? (
                                                        <MentionTextarea
                                                            value={newEvent.content}
                                                            onChange={(content) => setNewEvent((n) => ({ ...n, content }))}
                                                            placeholder="Describe the event... (type @ to mention a teammate)"
                                                        />
                                                    ) : (
                                                    <textarea
                                                        value={newEvent.content}
                                                        onChange={(e) => setNewEvent({ ...newEvent, content: e.target.value })}
                                                        placeholder="Describe the event..."
                                                        rows={3}
                                                        className="w-full bg-white border border-zinc-200 rounded-lg px-3 py-2 text-sm text-zinc-900 focus:outline-none focus:ring-1 focus:ring-white/50 resize-none"
                                                    />
                                                    )}
                                                    <div className="flex justify-end gap-2">
                                                        <button
                                                            onClick={() => setShowAddEvent(false)}
                                                            className="px-3 py-1.5 text-xs text-zinc-500 hover:text-zinc-900 transition-colors"
                                                        >
                                                            Cancel
                                                        </button>
                                                        <button
                                                            onClick={() => addTimelineEventMutation.mutate(newEvent)}
                                                            disabled={!newEvent.content.trim() || addTimelineEventMutation.isPending}
                                                            className="px-3 py-1.5 text-xs bg-white text-black rounded font-medium hover:bg-zinc-200 disabled:opacity-50 transition-colors"
                                                        >
                                                            {addTimelineEventMutation.isPending ? 'Adding...' : 'Add Event'}
                                                        </button>
                                                    </div>
                                                </div>
                                            </motion.div>
                                        )}
                                    </AnimatePresence>

                                    <div className="absolute left-4 top-0 bottom-0 w-0.5 bg-zinc-100" />
                                    {storyEvents.map((event: any, i: number) => {
                                        const Icon = event.icon;
                                        const isEditing = editingEventId === event.id;

                                        return (
                                            <div key={event.id || i} id={`event-${event.id}`}
                                                className="relative pl-12 group scroll-mt-24">
                                                <div className="absolute left-[-22px] top-0 p-1.5 rounded-full bg-white border border-zinc-200 group-hover:border-white/50 group-hover:shadow-[0_0_10px_rgba(255,255,255,0.2)] transition-all z-10">
                                                    <Icon size={14} className="text-zinc-500 group-hover:text-zinc-900 transition-colors" />
                                                </div>

                                                <div className="flex flex-col gap-1">
                                                    <div className="flex items-center gap-2">
                                                        <span className="text-xs font-mono text-zinc-400">{formatDateTime(event.created_at, me?.timezone)}</span>
                                                        <span className="text-[10px] font-mono text-zinc-400 bg-zinc-100 px-1.5 py-0.5 rounded">
                                                            {event.event_type?.replace('_', ' ')}
                                                        </span>
                                                        {event.user && (
                                                            <span className="inline-flex items-center gap-1.5 text-[10px] font-mono text-zinc-400">
                                                                <Avatar userId={event.user.id} name={event.user.full_name || event.user.email}
                                                                    hasAvatar={event.user.has_avatar} version={event.user.avatar_version} size={18} />
                                                                by {event.user.full_name || event.user.email}
                                                            </span>
                                                        )}
                                                    </div>

                                                    {isEditing ? (
                                                        <div className="glass-panel p-4 rounded-xl border border-blue-200 bg-white space-y-2">
                                                            <select
                                                                value={editEventData.event_type}
                                                                onChange={(e) => {
                                                                    const t = e.target.value;
                                                                    setEditEventData((d) => ({ ...d, event_type: t, content: t === 'comment' ? d.content : stripMentions(d.content) }));
                                                                }}
                                                                className="w-full bg-white border border-zinc-200 rounded-lg px-3 py-1.5 text-sm text-zinc-900 focus:outline-none focus:ring-1 focus:ring-white/50"
                                                            >
                                                                {EVENT_TYPES.map(t => (
                                                                    <option key={t} value={t}>{t.replace('_', ' ')}</option>
                                                                ))}
                                                            </select>
                                                            {editEventData.event_type === 'comment' ? (
                                                                <MentionTextarea
                                                                    value={editEventData.content}
                                                                    onChange={(content) => setEditEventData((d) => ({ ...d, content }))}
                                                                />
                                                            ) : (
                                                            <textarea
                                                                value={editEventData.content}
                                                                onChange={(e) => setEditEventData({ ...editEventData, content: e.target.value })}
                                                                rows={3}
                                                                className="w-full bg-white border border-zinc-200 rounded-lg px-3 py-2 text-sm text-zinc-900 focus:outline-none focus:ring-1 focus:ring-white/50 resize-none"
                                                            />
                                                            )}
                                                            <div className="flex justify-end gap-2">
                                                                <button
                                                                    onClick={() => setEditingEventId(null)}
                                                                    className="px-2 py-1 text-xs text-zinc-500 hover:text-zinc-900 transition-colors"
                                                                >
                                                                    Cancel
                                                                </button>
                                                                <button
                                                                    onClick={() => updateTimelineEventMutation.mutate({ eventId: event.id, data: editEventData })}
                                                                    disabled={!editEventData.content.trim() || updateTimelineEventMutation.isPending}
                                                                    className="px-2 py-1 text-xs bg-white text-black rounded font-medium hover:bg-zinc-200 disabled:opacity-50 flex items-center gap-1 transition-colors"
                                                                >
                                                                    <Check size={12} /> Save
                                                                </button>
                                                            </div>
                                                        </div>
                                                    ) : (
                                                        <div className="glass-panel p-4 rounded-xl border border-zinc-200 bg-white group-hover:bg-white transition-colors relative">
                                                            <p className="text-zinc-700 text-sm leading-relaxed pr-16 whitespace-pre-wrap">
                                                                {event.event_type === 'comment' ? <MentionText content={event.content} /> : event.content}
                                                            </p>
                                                            {/* Edit/Delete buttons - visible on hover */}
                                                            <div className="absolute top-2 right-2 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                                                                {canModify(event) && (
                                                                <button
                                                                    onClick={() => startEditing(event)}
                                                                    className="p-1.5 text-zinc-400 hover:text-accent-600 hover:bg-zinc-100 rounded transition-colors"
                                                                    title="Edit event"
                                                                >
                                                                    <Pencil size={12} />
                                                                </button>
                                                                )}
                                                                {canModify(event) && (
                                                                <button
                                                                    onClick={() => {
                                                                        if (confirm('Delete this timeline event?')) {
                                                                            deleteTimelineEventMutation.mutate(event.id);
                                                                        }
                                                                    }}
                                                                    className="p-1.5 text-zinc-400 hover:text-red-700 hover:bg-zinc-100 rounded transition-colors"
                                                                    title="Delete event"
                                                                >
                                                                    <Trash2 size={12} />
                                                                </button>
                                                                )}
                                                            </div>
                                                        </div>
                                                    )}
                                                </div>
                                            </div>
                                        );
                                    })}
                                    {(!storyEvents || storyEvents.length === 0) && !showAddEvent && (
                                        <div className="pl-12 text-zinc-400 text-sm italic">No events recorded yet.</div>
                                    )}
                                </div>
                            )}

                            {activeTab === 'tasks' && id && (
                                <CaseTasks caseId={parseInt(id)} />
                            )}

                            {activeTab === 'evidence' && id && <CaseEvidence caseId={parseInt(id)} />}

                            {activeTab === 'report' && id && <CaseReport key={id} caseId={parseInt(id)} onDirtyChange={setReportDirty} />}

                            {activeTab === 'automation' && id && <CaseAutomation caseId={parseInt(id)} />}

                            {activeTab === 'artifacts' && (
                                <div className="space-y-4">
                                    <div className="flex justify-between items-center mb-4">
                                        <h3 className="text-sm font-semibold text-zinc-700">Case Artifacts</h3>
                                        <button
                                            onClick={() => setShowArtifactModal(true)}
                                            className="text-xs bg-zinc-100 hover:bg-zinc-200 text-zinc-700 px-3 py-1.5 rounded transition-colors flex items-center gap-2"
                                        >
                                            <Plus size={12} /> Add Artifact
                                        </button>
                                    </div>

                                    {isLoadingArtifacts ? (
                                        <div className="text-center py-8 text-zinc-400">Loading artifacts...</div>
                                    ) : artifacts && artifacts.length > 0 ? (
                                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                            {artifacts.map((artifact: any) => {
                                                const Icon = ARTIFACT_ICONS[artifact.artifact_type] || FileText;
                                                const isShared = !artifact.isolated && (artifact.case_ids?.length || 0) > 1;
                                                return (
                                                    <div key={artifact.id} className="glass-panel p-4 rounded-lg bg-white border border-zinc-200 flex items-start gap-3 group/artifact">
                                                        <div className="p-2 bg-zinc-100 rounded-md">
                                                            <Icon size={16} className="text-accent-600" />
                                                        </div>
                                                        <div className="min-w-0 flex-1">
                                                            <div className="flex items-center gap-2 mb-0.5">
                                                                <p className="text-xs text-zinc-400 uppercase font-bold tracking-wider">{artifact.artifact_type}</p>
                                                                {isShared && (
                                                                    <span className="text-[10px] bg-blue-50 text-accent-600 border border-blue-200 px-1.5 py-0.5 rounded uppercase font-bold flex items-center gap-1">
                                                                        <Link2 size={10} />
                                                                        Shared ({artifact.case_ids.length})
                                                                    </span>
                                                                )}
                                                                {artifact.isolated && (
                                                                    <span className="text-[10px] bg-amber-50 text-amber-700 border border-amber-200 px-1.5 py-0.5 rounded uppercase font-bold">
                                                                        Isolated
                                                                    </span>
                                                                )}
                                                            </div>
                                                            <p className="text-sm text-zinc-800 font-mono truncate">{artifact.value}</p>
                                                            <p className="text-xs text-zinc-400 mt-2">
                                                                Added {new Date(artifact.created_at).toLocaleDateString()}
                                                            </p>
                                                            <button onClick={() => setIntelOpen(intelOpen === artifact.id ? null : artifact.id)}
                                                                aria-expanded={intelOpen === artifact.id} className="mt-2 text-xs text-accent-600 hover:underline">Intel</button>
                                                            {intelOpen === artifact.id && <div className="mt-2"><EnrichmentPanel kind="artifact" id={artifact.id} canRun={canRunIntel} /></div>}
                                                        </div>
                                                        <button
                                                            onClick={() => {
                                                                if (confirm('Remove this artifact from the case?')) {
                                                                    removeArtifactMutation.mutate(artifact.id);
                                                                }
                                                            }}
                                                            className="p-1.5 text-zinc-400 hover:text-red-700 hover:bg-zinc-100 rounded transition-colors opacity-0 group-hover/artifact:opacity-100"
                                                            title="Remove from case"
                                                        >
                                                            <Unlink size={14} />
                                                        </button>
                                                    </div>
                                                );
                                            })}
                                        </div>
                                    ) : (
                                        <div className="glass-panel p-4 rounded-lg border-dashed border-2 border-zinc-200 flex flex-col items-center justify-center text-zinc-400 py-12">
                                            <Database size={24} className="mb-2 opacity-50" />
                                            <p className="text-sm">No artifacts associated with this case.</p>
                                        </div>
                                    )}
                                </div>
                            )}

                            {activeTab === 'network' && (
                                <div className="flex items-center justify-center h-48 text-zinc-400">
                                    <p className="text-sm">Network graph visualization unavailable.</p>
                                </div>
                            )}

                            {activeTab === 'audit' && (
                                <div className="relative space-y-4 my-4">
                                    <div className="absolute left-4 top-0 bottom-0 w-0.5 bg-zinc-100" />
                                    {isLoadingAuditLogs ? (
                                        <div className="pl-12 text-zinc-400 text-sm">Loading audit logs...</div>
                                    ) : auditLogs && auditLogs.length > 0 ? (
                                        auditLogs.map((log: any, i: number) => (
                                            <div key={log.id || i} className="relative pl-12 group">
                                                <div className="absolute left-[-22px] top-0 p-1.5 rounded-full bg-white border border-zinc-200 group-hover:border-white/50 group-hover:shadow-[0_0_10px_rgba(255,255,255,0.2)] transition-all z-10">
                                                    <Shield size={14} className="text-zinc-500 group-hover:text-zinc-900 transition-colors" />
                                                </div>

                                                <div className="flex flex-col gap-1">
                                                    <div className="flex items-center gap-2">
                                                        <span className="text-xs font-mono text-zinc-400">{formatDateTime(log.created_at, me?.timezone)}</span>
                                                        <span className={cn(
                                                            "text-xs font-bold px-2 py-0.5 rounded uppercase",
                                                            log.action === 'create' ? "bg-green-50 text-green-700 border border-green-200" :
                                                                log.action === 'update' ? "bg-blue-50 text-accent-600 border border-blue-200" :
                                                                    "bg-red-50 text-red-700 border border-red-200"
                                                        )}>
                                                            {log.action}
                                                        </span>
                                                    </div>
                                                    <div className="glass-panel p-4 rounded-xl border border-zinc-200 bg-white group-hover:bg-white transition-colors">
                                                        <p className="text-zinc-700 text-sm leading-relaxed mb-2">
                                                            {log.entity_type === 'case' ? 'Case' : 'Artifact'} {log.action}d
                                                        </p>
                                                        {log.changes && Object.keys(log.changes).length > 0 && (
                                                            <div className="mt-2 space-y-1">
                                                                {Object.entries(log.changes).map(([field, change]: [string, any]) => (
                                                                    <div key={field} className="text-xs font-mono">
                                                                        <span className="text-zinc-400">{field}:</span>{' '}
                                                                        <span className="text-red-700">{JSON.stringify(change.from)}</span>
                                                                        {' → '}
                                                                        <span className="text-green-700">{JSON.stringify(change.to)}</span>
                                                                    </div>
                                                                ))}
                                                            </div>
                                                        )}
                                                    </div>
                                                </div>
                                            </div>
                                        ))
                                    ) : (
                                        <div className="pl-12 text-zinc-400 text-sm italic">No audit logs recorded yet.</div>
                                    )}
                                </div>
                            )}
                        </motion.div>
                    </AnimatePresence>
                </div>
            </div>

            {/* The copilot is now the global floating widget (auto-scopes to this case). */}

            {/* Add Artifact Modal */}
            <Modal
                open={showArtifactModal}
                onClose={() => setShowArtifactModal(false)}
                title="Add New Artifact"
                size="lg"
                footer={<>
                    <button onClick={() => setShowArtifactModal(false)} className={btnSecondary}>
                        Cancel
                    </button>
                    <button
                        onClick={() => addArtifactMutation.mutate(newArtifact)}
                        disabled={!newArtifact.value || addArtifactMutation.isPending}
                        className={btnPrimary}
                    >
                        {addArtifactMutation.isPending ? 'Adding...' : 'Add Artifact'}
                    </button>
                </>}
            >
                <div className="space-y-4">
                    <div>
                        <label className={modalLabel}>Type</label>
                        <select
                            value={newArtifact.type}
                            onChange={(e) => setNewArtifact({ ...newArtifact, type: e.target.value })}
                            className={modalInput}
                        >
                            {artifactTypeOptions.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                        </select>
                    </div>
                    <div>
                        <label className={modalLabel}>Value</label>
                        <input
                            type="text"
                            value={newArtifact.value}
                            onChange={(e) => setNewArtifact({ ...newArtifact, value: e.target.value })}
                            className={cn(modalInput, 'font-mono')}
                            placeholder="Enter value..."
                        />
                    </div>
                </div>
            </Modal>

            {/* Edit Case Modal */}
            <EditCaseModal
                show={showEditModal}
                onClose={() => setShowEditModal(false)}
                caseData={caseData}
                onSubmit={(data) => updateCaseMutation.mutate(data)}
                isSubmitting={updateCaseMutation.isPending}
            />
        </PageContainer>
    );
}
