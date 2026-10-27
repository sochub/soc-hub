import { useParams, useLocation } from 'react-router-dom';
import Avatar from '../../components/Avatar';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { Shield, MessageSquare, ArrowLeft, Hash, Calendar, Plus, Globe, FileText, Mail, Server, Monitor, User as UserIcon, Edit, Link2, Unlink, ShieldAlert } from 'lucide-react';
import { Link } from 'react-router-dom';
import { cn } from '../../lib/utils';
import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import EditCaseModal from './EditCaseModal';
import PageContainer from '../../components/layout/PageContainer';
import Modal, { modalInput, modalLabel, btnPrimary, btnSecondary } from '../../components/layout/Modal';
import CaseTasks from './CaseTasks';
import CaseTimeline from './CaseTimeline';
import CaseAssets, { AddAssetForm } from './CaseAssets';
import CaseEvidence from './CaseEvidence';
import CaseReport from './CaseReport';
import TriagePanel from './TriagePanel';
import FollowButton from './FollowButton';
import WarRoomPill from './WarRoomPill';
import CaseTicket from './CaseTicket';
import CaseContainment from './CaseContainment';
import OpenWarRoomModal from './OpenWarRoomModal';
import WarRoomSuggestions from './WarRoomSuggestions';
import IncidentOverview from './IncidentOverview';
import GuestsPanel from '../guests/GuestsPanel';
import type { User, CaseEditPayload } from '../../types';
import { formatDateTime } from '../../utils/datetime';
import SLAPanel from './SLAPanel';
import CaseAutomation from '../automations/CaseAutomation';
import EnrichmentPanel from '../enrichment/EnrichmentPanel';
import { useCanRun } from '../enrichment/useCanRun';
import { useArtifactTypes } from '../artifacts/useArtifactTypes';
import ArtifactMindMap from '../artifacts/ArtifactMindMap';

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

const CASE_TABS = ['activity', 'tasks', 'artifacts', 'evidence', 'guests', 'report', 'network', 'audit', 'automation'] as const;
type CaseTab = typeof CASE_TABS[number];
const TAB_LABEL: Partial<Record<CaseTab, string>> = { artifacts: 'Artifacts & assets' };

// What the add dialog offers; the hint tells analysts which one they have.
const ITEM_KINDS = [
    ['artifact', 'Artifact', 'Evidence of the attacker: an IP, domain, URL, hash, email… Can be enriched with threat intel, linked to events and promoted to an IOC.'],
    ['asset', 'Asset', 'Something of yours the incident touched: a host, account or service. Track whether it is compromised.'],
] as const;
type ItemKind = typeof ITEM_KINDS[number][0];

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
    const [itemKind, setItemKind] = useState<ItemKind>('artifact');
    const { options: artifactTypeOptions } = useArtifactTypes();
    const [showEditModal, setShowEditModal] = useState(false);

    const updateCaseMutation = useMutation({
        mutationFn: async (data: CaseEditPayload) => {
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
    const canWarRoom = isAdmin || me?.role === 'analyst';
    const [showWarRoom, setShowWarRoom] = useState(false);
    const { data: slackCfg } = useQuery({
        queryKey: ['war-room-settings'],
        queryFn: async () => (await api.get('/slack/war-room-settings')).data as { configured: boolean; war_room_name_template: string | null },
        enabled: canWarRoom, retry: false,
    });
    // Viewers can't list guests (the API returns 403), so they don't get the tab.
    const canSeeGuests = isAdmin || me?.role === 'analyst';

    // Deep link from a notification: /cases/:id#event-<id> -> show Activity (once per hash); CaseTimeline scrolls to the note.
    const { hash } = useLocation();
    const [seenHash, setSeenHash] = useState(hash);
    if (seenHash !== hash) {
        setSeenHash(hash);
        if (/^#event-\d+$/.test(hash)) selectTab('activity');
    }

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
                                {caseData.slack_channel_id && <WarRoomPill caseData={caseData} canEdit={canWarRoom} />}
                                {!caseData.slack_channel_id && canWarRoom && slackCfg?.configured && (
                                    <button type="button" onClick={() => setShowWarRoom(true)}
                                        className="inline-flex items-center gap-1 rounded-sm border border-line-strong px-2.5 py-0.5 text-xs text-fg-2 hover:bg-raised">
                                        <Hash size={12} /> Open war room
                                    </button>
                                )}
                                {id && <CaseTicket caseId={parseInt(id)} canEdit={canWarRoom} />}
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

                    {id && canWarRoom && caseData.slack_channel_id && <WarRoomSuggestions caseId={parseInt(id)} />}
                    {id && <OpenWarRoomModal open={showWarRoom} onClose={() => setShowWarRoom(false)} caseId={parseInt(id)} title={caseData.title} template={slackCfg?.war_room_name_template ?? null} />}
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
                                <span className="capitalize">{TAB_LABEL[tab] ?? tab}</span>
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
                            <span className="mr-auto">The report has unsaved changes. Discard them and switch to <span className="capitalize font-medium">{TAB_LABEL[pendingTab] ?? pendingTab}</span>?</span>
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
                            {activeTab === 'activity' && id && (
                                <div className="space-y-6">
                                    <IncidentOverview caseData={caseData} tz={me?.timezone} canEdit={canSeeGuests}
                                        onOpenInvestigation={(eid) => document.getElementById(`case-event-${eid}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })} />
                                    <CaseTimeline caseId={parseInt(id)} activity={caseData.timeline_events ?? []} />
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
                                        <h3 className="text-sm font-semibold text-fg-2">Artifacts <span className="font-mono text-xs text-muted">{artifacts?.length ?? 0}</span></h3>
                                        {canSeeGuests && (
                                            <button
                                                onClick={() => setShowArtifactModal(true)}
                                                className="text-xs bg-accent text-on-accent hover:bg-accent-hover px-3 py-1.5 rounded-sm font-medium transition-colors flex items-center gap-2"
                                            >
                                                <Plus size={12} /> Add artifact or asset
                                            </button>
                                        )}
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
                                        <p className="text-sm text-muted">No artifacts yet. Add the IPs, domains, hashes and other evidence you find.</p>
                                    )}
                                    {id && <div className="pt-4"><CaseContainment caseId={parseInt(id)} canEdit={canSeeGuests} /></div>}
                                    {id && <div className="pt-4"><CaseAssets caseId={parseInt(id)} /></div>}
                                </div>
                            )}

                            {activeTab === 'network' && id && <ArtifactMindMap caseId={parseInt(id)} />}

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

            {/* Add artifact or asset */}
            <Modal
                open={showArtifactModal}
                onClose={() => setShowArtifactModal(false)}
                title={itemKind === 'artifact' ? 'Add artifact' : 'Add asset'}
                size="lg"
                footer={itemKind === 'artifact' && <>
                    <button onClick={() => setShowArtifactModal(false)} className={btnSecondary}>
                        Cancel
                    </button>
                    <button
                        onClick={() => addArtifactMutation.mutate(newArtifact)}
                        disabled={!newArtifact.value.trim() || addArtifactMutation.isPending}
                        className={btnPrimary}
                    >
                        {addArtifactMutation.isPending ? 'Adding...' : 'Add artifact'}
                    </button>
                </>}
            >
                <div className="space-y-4">
                    <div role="radiogroup" aria-label="What are you adding?" className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        {ITEM_KINDS.map(([k, label, hint]) => (
                            <button key={k} type="button" role="radio" aria-checked={itemKind === k} onClick={() => setItemKind(k)}
                                className={cn('text-left rounded-lg border p-3 transition-colors',
                                    itemKind === k ? 'border-accent bg-accent/10' : 'border-line hover:bg-raised')}>
                                <span className="text-sm font-medium text-fg">{label}</span>
                                <span className="mt-1 block text-xs text-fg-3">{hint}</span>
                            </button>
                        ))}
                    </div>
                    {itemKind === 'artifact' ? <>
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
                        {addArtifactMutation.isError && <p role="alert" className="text-xs text-severity-critical">Could not add the artifact.</p>}
                    </> : id && <AddAssetForm caseId={parseInt(id)} onDone={() => setShowArtifactModal(false)} />}
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
