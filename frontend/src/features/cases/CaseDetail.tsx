import { useParams, useLocation } from 'react-router-dom';
import Avatar from '../../components/Avatar';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { Shield, Database, MessageSquare, ArrowLeft, Hash, Calendar, Plus, X, Globe, FileText, Mail, Server, Monitor, User as UserIcon, Edit, Trash2, Check, Pencil, Link2, Unlink, Eye, EyeOff, ShieldAlert, CheckSquare } from 'lucide-react';
import { Link } from 'react-router-dom';
import { cn } from '../../lib/utils';
import { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import EditCaseModal from './EditCaseModal';
import PageContainer from '../../components/layout/PageContainer';
import Modal, { modalInput, modalLabel, btnPrimary, btnSecondary } from '../../components/layout/Modal';
import CaseTasks from './CaseTasks';
import CaseTimeline from './CaseTimeline';
import CaseAssets from './CaseAssets';
import CaseEvidence from './CaseEvidence';
import CaseReport from './CaseReport';
import TriagePanel from './TriagePanel';
import FollowButton from './FollowButton';
import MentionTextarea from './MentionTextarea';
import { stripMentions } from './mentionChips';
import { Markdown, MarkdownField } from '../../components/Markdown';
import IncidentOverview from './IncidentOverview';
import GuestsPanel from '../guests/GuestsPanel';
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
    hostname: Monitor,
    username: UserIcon,
};

// status_change/severity_change/task are written by the system when the case or a task actually changes.
const EVENT_TYPES = ['comment', 'investigation', 'containment', 'remediation', 'other'];

const CASE_TABS = ['activity', 'timeline', 'tasks', 'artifacts', 'assets', 'evidence', 'guests', 'report', 'network', 'audit', 'automation'] as const;
type CaseTab = typeof CASE_TABS[number];

export default function CaseDetail() {
    const { id } = useParams();
    const queryClient = useQueryClient();
    const [activeTab, setActiveTab] = useState<CaseTab>('activity');
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
    const [shareError, setShareError] = useState<string | null>(null);
    const [newEvent, setNewEvent] = useState({ event_type: 'comment', content: '' });
    const [editingEventId, setEditingEventId] = useState<number | null>(null);
    const [editEventData, setEditEventData] = useState({ event_type: '', content: '' });

    const updateCaseMutation = useMutation({
        mutationFn: async (data: { title: string; description: string; severity: string; status: string; tags: string[]; source: string; outcome: string | null; closing_note: string | null }) => {
            if (!id) return;
            const response = await api.put(`/cases/${id}`, data);
            return response.data;
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['case', id] });
            queryClient.invalidateQueries({ queryKey: ['audit-logs', 'case', id] });
            setShowEditModal(false);
        },
    });
    const editError = (() => {
        const d = (updateCaseMutation.error as { response?: { data?: { detail?: unknown } } } | null)?.response?.data?.detail;
        if (!updateCaseMutation.isError) return null;
        return typeof d === 'string' ? d : 'Could not save the case.';
    })();

    const shareEvent = useMutation({
        mutationFn: async ({ eventId, shared }: { eventId: number; shared: boolean }) =>
            api.put(`/cases/${id}/timeline/${eventId}/share`, { shared }),
        onSuccess: () => { setShareError(null); queryClient.invalidateQueries({ queryKey: ['case', id] }); },
        onError: (err: unknown) => {
            const d = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
            setShareError(typeof d === 'string' ? d : 'Could not change sharing.');
        },
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
    // Viewers can't list guests (the API returns 403), so they don't get the tab.
    const canSeeGuests = isAdmin || me?.role === 'analyst';
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
        if (activeTab !== 'activity') {
            if (switchedFor.current !== hash) { switchedFor.current = hash; selectTab('activity'); } // once, so the user can leave again
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

    const promoteArtifactMutation = useMutation({
        mutationFn: async (artifactId: number) => (await api.post(`/artifacts/${artifactId}/promote`, { case_id: Number(id) })).data,
        onSuccess: (d) => {
            queryClient.invalidateQueries({ queryKey: ['iocs'] });
            if (!d.created) alert('Already an IOC');
        },
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
                        <div className="w-10 h-10 rounded-full border-4 border-line border-t-accent animate-spin" />
                    </div>
                </div>
            </PageContainer>
        );
    }

    if (!caseData) return <PageContainer><div className="text-red-300 font-mono">ERROR: CASE_NOT_FOUND</div></PageContainer>;

    const severityColor = caseData.severity === 'critical' ? 'text-severity-critical bg-red-500/10 border-red-500/30' :
        caseData.severity === 'high' ? 'text-severity-high bg-orange-500/10 border-orange-500/30' :
            caseData.severity === 'medium' ? 'text-severity-medium bg-amber-500/10 border-amber-500/30' :
                caseData.severity === 'low' ? 'text-severity-low bg-accent/10 border-accent/40' :
                    'text-fg-2 bg-raised border-line';

    const storyEvents = caseData?.timeline_events?.map((event: any) => ({
        ...event,
        icon: event.event_type === 'artifact_added' ? Database :
            event.event_type === 'status_change' ? Shield :
                event.event_type === 'task' ? CheckSquare :
                (event.event_type === 'comment' || event.event_type === 'guest_comment') ? MessageSquare : Calendar
    })) || [];

    const startEditing = (event: any) => {
        setEditingEventId(event.id);
        setEditEventData({ event_type: event.event_type, content: event.content });
    };

    return (
        <PageContainer>
            <div className="min-w-0 flex flex-col bg-panel rounded-2xl border border-line relative">
                {/* Top Decoration */}
                <div className="absolute top-0 left-0 right-0 h-1 rounded-t-2xl bg-linear-to-r from-accent via-accent to-accent opacity-50" />

                {/* Header */}
                <div className="p-6 pb-2">
                    <div className="flex items-center gap-4 mb-6">
                        <Link to="/" className="p-2 rounded-lg text-fg-3 hover:text-fg hover:bg-raised transition-colors">
                            <ArrowLeft size={20} />
                        </Link>
                        <div className="flex-1">
                            <div className="flex items-center gap-3 mb-1">
                                <h1 className="text-2xl font-bold text-fg tracking-tight">{caseData.title}</h1>
                                <span className={cn(
                                    "px-2.5 py-0.5 rounded-sm text-xs uppercase font-bold border tracking-wider",
                                    severityColor
                                )}>
                                    {caseData.severity}
                                </span>
                                {caseData.outcome && (
                                    <span className="px-2.5 py-0.5 rounded-sm text-xs uppercase font-bold border border-line text-fg-2 tracking-wider">
                                        {caseData.outcome.replace(/_/g, ' ')}
                                    </span>
                                )}
                            </div>
                            <div className="flex items-center gap-4 text-xs font-mono text-muted">
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
                            className="p-2 text-fg-3 hover:text-fg hover:bg-raised rounded-lg transition-colors"
                        >
                            <Edit size={20} />
                        </button>
                    </div>

                    <div className="glass-panel p-5 rounded-xl mb-6 bg-panel">
                        <h3 className="text-xs font-bold text-muted mb-2 uppercase tracking-wider flex items-center gap-2">
                            <MessageSquare size={12} />
                            Initial Report
                        </h3>
                        <p className="text-fg-2 leading-relaxed text-sm">{caseData.description}</p>
                    </div>

                    {id && <TriagePanel caseId={parseInt(id)} />}
                    <SLAPanel caseData={caseData} />

                </div>

                {/* Tabs (sticky while the page scrolls inside <main>) */}
                <div className="sticky top-0 z-10 bg-panel px-6 pt-3">
                    <div className="flex border-b border-line relative space-x-6">
                        {CASE_TABS.filter((tab) => tab !== 'guests' || canSeeGuests).map((tab) => (
                            <button
                                key={tab}
                                onClick={() => selectTab(tab)}
                                className={cn(
                                    "pb-3 text-sm font-medium transition-colors relative focus:outline-hidden",
                                    activeTab === tab ? "text-accent" : "text-muted hover:text-fg-2"
                                )}
                            >
                                <span className="capitalize">{tab}</span>
                                {activeTab === tab && (
                                    <motion.div
                                        layoutId="activeTab"
                                        className="absolute -bottom-px left-0 right-0 h-0.5 bg-accent"
                                    />
                                )}
                            </button>
                        ))}
                    </div>
                    {pendingTab && (
                        <div role="alert" className="flex flex-wrap items-center gap-2 text-xs text-amber-300 bg-amber-500/10 border border-amber-500/30 px-3 py-2 mt-2">
                            <span className="mr-auto">The report has unsaved changes. Discard them and switch to <span className="capitalize font-medium">{pendingTab}</span>?</span>
                            <button onClick={() => { setReportDirty(false); setActiveTab(pendingTab); setPendingTab(null); }}
                                className="px-2 py-1 bg-red-600 text-white rounded-sm hover:bg-red-700">Discard</button>
                            <button onClick={() => setPendingTab(null)}
                                className="px-2 py-1 bg-panel border border-line-strong text-fg-2 rounded-sm hover:bg-surface">Keep editing</button>
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
                            {activeTab === 'activity' && shareError && <p role="alert" className="mb-3 text-sm text-severity-critical">{shareError}</p>}
                            {activeTab === 'timeline' && id && <CaseTimeline caseId={parseInt(id)} activity={caseData?.timeline_events ?? []} />}
                            {activeTab === 'assets' && id && <CaseAssets caseId={parseInt(id)} />}
                            {activeTab === 'activity' && (
                                <IncidentOverview caseData={caseData} tz={me?.timezone} canEdit={canSeeGuests}
                                    onOpenInvestigation={(eid) => {
                                        selectTab('timeline');
                                        // The Timeline tab mounts after the tab switch; scroll once its row exists.
                                        setTimeout(() => document.getElementById(`case-event-${eid}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 400);
                                    }} />
                            )}
                            {activeTab === 'activity' && (
                                <div className="relative space-y-8 my-4">
                                    {/* Add Event Button */}
                                    <div className="flex justify-end mb-2">
                                        <button
                                            onClick={() => setShowAddEvent(true)}
                                            className="text-xs bg-raised hover:bg-raised-2 text-fg-2 px-3 py-1.5 rounded-sm transition-colors flex items-center gap-2"
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
                                                <div className="glass-panel p-4 rounded-xl border border-line bg-panel space-y-3">
                                                    <div className="flex items-center justify-between">
                                                        <h4 className="text-sm font-semibold text-fg">New Timeline Event</h4>
                                                        <button onClick={() => setShowAddEvent(false)} className="text-muted hover:text-fg">
                                                            <X size={16} />
                                                        </button>
                                                    </div>
                                                    <select
                                                        value={newEvent.event_type}
                                                        onChange={(e) => {
                                                            const t = e.target.value;
                                                            setNewEvent((n) => ({ ...n, event_type: t, content: t === 'comment' ? n.content : stripMentions(n.content) }));
                                                        }}
                                                        className="w-full bg-panel border border-line rounded-lg px-3 py-2 text-sm text-fg focus:outline-hidden focus:ring-1 focus:ring-white/50"
                                                    >
                                                        {EVENT_TYPES.map(t => (
                                                            <option key={t} value={t}>{t.replace('_', ' ')}</option>
                                                        ))}
                                                    </select>
                                                    <MarkdownField value={newEvent.content}>
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
                                                        className="w-full bg-panel border border-line rounded-lg px-3 py-2 text-sm text-fg focus:outline-hidden focus:ring-1 focus:ring-white/50 resize-none"
                                                    />
                                                    )}
</MarkdownField>
                                                    <div className="flex justify-end gap-2">
                                                        <button
                                                            onClick={() => setShowAddEvent(false)}
                                                            className="px-3 py-1.5 text-xs text-fg-3 hover:text-fg transition-colors"
                                                        >
                                                            Cancel
                                                        </button>
                                                        <button
                                                            onClick={() => addTimelineEventMutation.mutate(newEvent)}
                                                            disabled={!newEvent.content.trim() || addTimelineEventMutation.isPending}
                                                            className="px-3 py-1.5 text-xs bg-accent text-on-accent rounded-sm font-medium hover:bg-accent-hover disabled:opacity-50 transition-colors"
                                                        >
                                                            {addTimelineEventMutation.isPending ? 'Adding...' : 'Add Event'}
                                                        </button>
                                                    </div>
                                                </div>
                                            </motion.div>
                                        )}
                                    </AnimatePresence>

                                    <div className="absolute left-4 top-0 bottom-0 w-0.5 bg-raised" />
                                    {storyEvents.map((event: any, i: number) => {
                                        const Icon = event.icon;
                                        const isEditing = editingEventId === event.id;

                                        return (
                                            <div key={event.id || i} id={`event-${event.id}`}
                                                className="relative pl-12 group scroll-mt-24">
                                                <div className="absolute left-[-22px] top-0 p-1.5 rounded-full bg-panel border border-line group-hover:border-white/50 group-hover:shadow-[0_0_10px_rgba(255,255,255,0.2)] transition-all z-10">
                                                    <Icon size={14} className="text-fg-3 group-hover:text-fg transition-colors" />
                                                </div>

                                                <div className="flex flex-col gap-1">
                                                    <div className="flex items-center gap-2">
                                                        <span className="text-xs font-mono text-muted">{formatDateTime(event.created_at, me?.timezone)}</span>
                                                        <span className="text-[10px] font-mono text-muted bg-raised px-1.5 py-0.5 rounded-sm">
                                                            {event.event_type?.replace('_', ' ')}
                                                        </span>
                                                        {event.guest_email && (
                                                            <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-500/15 text-amber-300"
                                                                title="Written by an external guest — treat attachments as untrusted">Guest · {event.guest_email}</span>
                                                        )}
                                                        {event.via === 'mcp' && (
                                                            <span className="text-[10px] font-mono text-muted bg-raised px-1.5 py-0.5 rounded-sm"
                                                                title="Written by an MCP client acting for this user">via MCP{event.via_client ? ` · ${event.via_client}` : ''}</span>
                                                        )}
                                                        {event.event_type !== 'guest_invited' && (isAdmin || me?.role === 'analyst') && (
                                                            <button type="button" onClick={() => shareEvent.mutate({ eventId: event.id, shared: !event.shared_with_guests })}
                                                                aria-pressed={!!event.shared_with_guests}
                                                                aria-label={event.shared_with_guests ? 'Stop sharing with guests' : 'Share with guests'}
                                                                title={event.shared_with_guests ? 'Shared with guests — click to stop sharing' : 'Share with guests'}
                                                                className={event.shared_with_guests ? 'text-accent' : 'text-muted hover:text-fg-2'}>
                                                                {event.shared_with_guests ? <Eye size={13} /> : <EyeOff size={13} />}
                                                            </button>
                                                        )}
                                                        {event.shared_with_guests && <span className="text-[10px] font-medium text-accent">Shared</span>}
                                                        {event.user && (
                                                            <span className="inline-flex items-center gap-1.5 text-[10px] font-mono text-muted">
                                                                <Avatar userId={event.user.id} name={event.user.full_name || event.user.email}
                                                                    hasAvatar={event.user.has_avatar} version={event.user.avatar_version} size={18} />
                                                                by {event.user.full_name || event.user.email}
                                                            </span>
                                                        )}
                                                    </div>

                                                    {isEditing ? (
                                                        <div className="glass-panel p-4 rounded-xl border border-accent/40 bg-panel space-y-2">
                                                            <select
                                                                value={editEventData.event_type}
                                                                onChange={(e) => {
                                                                    const t = e.target.value;
                                                                    setEditEventData((d) => ({ ...d, event_type: t, content: t === 'comment' ? d.content : stripMentions(d.content) }));
                                                                }}
                                                                className="w-full bg-panel border border-line rounded-lg px-3 py-1.5 text-sm text-fg focus:outline-hidden focus:ring-1 focus:ring-white/50"
                                                            >
                                                                {EVENT_TYPES.map(t => (
                                                                    <option key={t} value={t}>{t.replace('_', ' ')}</option>
                                                                ))}
                                                            </select>
                                                            <MarkdownField value={editEventData.content}>
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
                                                                className="w-full bg-panel border border-line rounded-lg px-3 py-2 text-sm text-fg focus:outline-hidden focus:ring-1 focus:ring-white/50 resize-none"
                                                            />
                                                            )}
</MarkdownField>
                                                            <div className="flex justify-end gap-2">
                                                                <button
                                                                    onClick={() => setEditingEventId(null)}
                                                                    className="px-2 py-1 text-xs text-fg-3 hover:text-fg transition-colors"
                                                                >
                                                                    Cancel
                                                                </button>
                                                                <button
                                                                    onClick={() => updateTimelineEventMutation.mutate({ eventId: event.id, data: editEventData })}
                                                                    disabled={!editEventData.content.trim() || updateTimelineEventMutation.isPending}
                                                                    className="px-2 py-1 text-xs bg-accent text-on-accent rounded-sm font-medium hover:bg-accent-hover disabled:opacity-50 flex items-center gap-1 transition-colors"
                                                                >
                                                                    <Check size={12} /> Save
                                                                </button>
                                                            </div>
                                                        </div>
                                                    ) : (
                                                        <div className="glass-panel p-4 rounded-xl border border-line bg-panel group-hover:bg-panel transition-colors relative">
                                                            <Markdown content={event.content} className="text-fg-2 text-sm pr-16" />
                                                            {/* Edit/Delete buttons - visible on hover */}
                                                            <div className="absolute top-2 right-2 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                                                                {canModify(event) && (
                                                                <button
                                                                    onClick={() => startEditing(event)}
                                                                    className="p-1.5 text-muted hover:text-accent hover:bg-raised rounded-sm transition-colors"
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
                                                                    className="p-1.5 text-muted hover:text-red-300 hover:bg-raised rounded-sm transition-colors"
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
                                        <div className="pl-12 text-muted text-sm italic">No events recorded yet.</div>
                                    )}
                                </div>
                            )}

                            {activeTab === 'tasks' && id && (
                                <CaseTasks caseId={parseInt(id)} />
                            )}

                            {activeTab === 'evidence' && id && <CaseEvidence caseId={parseInt(id)} />}
                            {activeTab === 'guests' && id && canSeeGuests && <GuestsPanel caseId={parseInt(id)} guestSummary={caseData?.guest_summary} isAdmin={isAdmin} />}

                            {activeTab === 'report' && id && <CaseReport key={id} caseId={parseInt(id)} onDirtyChange={setReportDirty} />}

                            {activeTab === 'automation' && id && <CaseAutomation caseId={parseInt(id)} />}

                            {activeTab === 'artifacts' && (
                                <div className="space-y-4">
                                    <div className="flex justify-between items-center mb-4">
                                        <h3 className="text-sm font-semibold text-fg-2">Case Artifacts</h3>
                                        <button
                                            onClick={() => setShowArtifactModal(true)}
                                            className="text-xs bg-raised hover:bg-raised-2 text-fg-2 px-3 py-1.5 rounded-sm transition-colors flex items-center gap-2"
                                        >
                                            <Plus size={12} /> Add Artifact
                                        </button>
                                    </div>

                                    {isLoadingArtifacts ? (
                                        <div className="text-center py-8 text-muted">Loading artifacts...</div>
                                    ) : artifacts && artifacts.length > 0 ? (
                                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                            {artifacts.map((artifact: any) => {
                                                const Icon = ARTIFACT_ICONS[artifact.artifact_type] || FileText;
                                                const isShared = !artifact.isolated && (artifact.case_ids?.length || 0) > 1;
                                                return (
                                                    <div key={artifact.id} className="glass-panel p-4 rounded-lg bg-panel border border-line flex items-start gap-3 group/artifact">
                                                        <div className="p-2 bg-raised rounded-md">
                                                            <Icon size={16} className="text-accent" />
                                                        </div>
                                                        <div className="min-w-0 flex-1">
                                                            <div className="flex items-center gap-2 mb-0.5">
                                                                <p className="text-xs text-muted uppercase font-bold tracking-wider">{artifact.artifact_type}</p>
                                                                {isShared && (
                                                                    <span className="text-[10px] bg-accent/10 text-accent border border-accent/40 px-1.5 py-0.5 rounded-sm uppercase font-bold flex items-center gap-1">
                                                                        <Link2 size={10} />
                                                                        Shared ({artifact.case_ids.length})
                                                                    </span>
                                                                )}
                                                                {artifact.isolated && (
                                                                    <span className="text-[10px] bg-amber-500/10 text-amber-300 border border-amber-500/30 px-1.5 py-0.5 rounded-sm uppercase font-bold">
                                                                        Isolated
                                                                    </span>
                                                                )}
                                                            </div>
                                                            <p className="text-sm text-fg font-mono truncate">{artifact.value}</p>
                                                            <p className="text-xs text-muted mt-2">
                                                                Added {new Date(artifact.created_at).toLocaleDateString()}
                                                            </p>
                                                            <button onClick={() => setIntelOpen(intelOpen === artifact.id ? null : artifact.id)}
                                                                aria-expanded={intelOpen === artifact.id} className="mt-2 text-xs text-accent hover:underline">Intel</button>
                                                            {intelOpen === artifact.id && <div className="mt-2"><EnrichmentPanel kind="artifact" id={artifact.id} canRun={canRunIntel} /></div>}
                                                        </div>
                                                        <button
                                                            onClick={() => promoteArtifactMutation.mutate(artifact.id)}
                                                            className="p-1.5 text-muted hover:text-accent hover:bg-raised rounded-sm transition-colors opacity-0 group-hover/artifact:opacity-100"
                                                            title="Promote to IOC"
                                                        >
                                                            <ShieldAlert size={14} />
                                                        </button>
                                                        <button
                                                            onClick={() => {
                                                                if (confirm('Remove this artifact from the case?')) {
                                                                    removeArtifactMutation.mutate(artifact.id);
                                                                }
                                                            }}
                                                            className="p-1.5 text-muted hover:text-red-300 hover:bg-raised rounded-sm transition-colors opacity-0 group-hover/artifact:opacity-100"
                                                            title="Remove from case"
                                                        >
                                                            <Unlink size={14} />
                                                        </button>
                                                    </div>
                                                );
                                            })}
                                        </div>
                                    ) : (
                                        <div className="glass-panel p-4 rounded-lg border-dashed border-2 border-line flex flex-col items-center justify-center text-muted py-12">
                                            <Database size={24} className="mb-2 opacity-50" />
                                            <p className="text-sm">No artifacts associated with this case.</p>
                                        </div>
                                    )}
                                </div>
                            )}

                            {activeTab === 'network' && (
                                <div className="flex items-center justify-center h-48 text-muted">
                                    <p className="text-sm">Network graph visualization unavailable.</p>
                                </div>
                            )}

                            {activeTab === 'audit' && (
                                <div className="relative space-y-4 my-4">
                                    <div className="absolute left-4 top-0 bottom-0 w-0.5 bg-raised" />
                                    {isLoadingAuditLogs ? (
                                        <div className="pl-12 text-muted text-sm">Loading audit logs...</div>
                                    ) : auditLogs && auditLogs.length > 0 ? (
                                        auditLogs.map((log: any, i: number) => (
                                            <div key={log.id || i} className="relative pl-12 group">
                                                <div className="absolute left-[-22px] top-0 p-1.5 rounded-full bg-panel border border-line group-hover:border-white/50 group-hover:shadow-[0_0_10px_rgba(255,255,255,0.2)] transition-all z-10">
                                                    <Shield size={14} className="text-fg-3 group-hover:text-fg transition-colors" />
                                                </div>

                                                <div className="flex flex-col gap-1">
                                                    <div className="flex items-center gap-2">
                                                        <span className="text-xs font-mono text-muted">{formatDateTime(log.created_at, me?.timezone)}</span>
                                                        <span className={cn(
                                                            "text-xs font-bold px-2 py-0.5 rounded-sm uppercase",
                                                            log.action === 'create' || log.action.endsWith('_added') || log.action === 'task_completed' ? "bg-green-500/10 text-green-300 border border-green-500/30" :
                                                                log.action.includes('remove') || log.action.includes('delete') ? "bg-red-500/10 text-red-300 border border-red-500/30" :
                                                                    "bg-accent/10 text-accent border border-accent/40"
                                                        )}>
                                                            {log.action.replace(/_/g, ' ')}
                                                        </span>
                                                        <span className="inline-flex items-center gap-1.5 text-[10px] font-mono text-muted">
                                                            {log.user ? <>
                                                                <Avatar userId={log.user.id} name={log.user.full_name || log.user.email} size={18} />
                                                                by {log.user.full_name || log.user.email}
                                                            </> : `by ${log.actor || log.user_email || 'system'}`}
                                                        </span>
                                                    </div>
                                                    {log.changes && Object.keys(log.changes).length > 0 && (
                                                        <div className="glass-panel p-4 rounded-xl border border-line bg-panel group-hover:bg-panel transition-colors space-y-1">
                                                            {Object.entries(log.changes).map(([field, change]: [string, any]) => (
                                                                <div key={field} className="text-xs font-mono break-words">
                                                                    <span className="text-muted">{field.replace(/_/g, ' ')}:</span>{' '}
                                                                    {change && typeof change === 'object' && 'to' in change ? <>
                                                                        <span className="text-red-300 line-through decoration-red-300/50">{JSON.stringify(change.from)}</span>
                                                                        {' → '}
                                                                        <span className="text-green-300">{JSON.stringify(change.to)}</span>
                                                                    </> : <span className="text-fg-2">{typeof change === 'string' ? change : JSON.stringify(change)}</span>}
                                                                </div>
                                                            ))}
                                                        </div>
                                                    )}
                                                </div>
                                            </div>
                                        ))
                                    ) : (
                                        <div className="pl-12 text-muted text-sm italic">No audit logs recorded yet.</div>
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
                onClose={() => { setShowEditModal(false); updateCaseMutation.reset(); }}
                caseData={caseData}
                error={editError}
                onSubmit={(data) => updateCaseMutation.mutate(data)}
                isSubmitting={updateCaseMutation.isPending}
            />
        </PageContainer>
    );
}
