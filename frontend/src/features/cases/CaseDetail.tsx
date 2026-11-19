import { useParams, useLocation } from 'react-router-dom';
import Avatar from '../../components/Avatar';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { Shield, ArrowLeft, CheckCircle2, Hash, Calendar, Plus, Globe, FileText, Mail, Server, Monitor, User as UserIcon, Edit, Link2, Unlink, ShieldAlert, Trash2, Clock } from 'lucide-react';
import { Link } from 'react-router-dom';
import { cn } from '../../lib/utils';
import { useState } from 'react';
import { Button, Badge, Stamp, SeverityStamp, FolderTabs, Skeleton, Select, Input, Label, statusTone, TONE_TEXT } from '../../components/ui';
import EditCaseModal from './EditCaseModal';
import CloseCaseModal from './CloseCaseModal';
import { CASE_STATUSES, statusLabel } from './statuses';
import { outcomeLabel } from './outcomes';
import PageContainer from '../../components/layout/PageContainer';
import Modal from '../../components/layout/Modal';
import CaseTasks from './CaseTasks';
import CaseTimeline from './CaseTimeline';
import CaseAssets, { AddAssetForm } from './CaseAssets';
import CaseEvidence from './CaseEvidence';
import CaseReport from './CaseReport';
import TriagePanel from './TriagePanel';
import FollowButton from './FollowButton';
import AssigneeSelect from './AssigneeSelect';
import DeleteCaseModal from './DeleteCaseModal';
import WarRoomPill from './WarRoomPill';
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
import CaseTime from './CaseTime';
import EscalationBadge from '../escalation/EscalationBadge';
import EscalationActions from '../escalation/EscalationActions';
import EscalationHistory from '../escalation/EscalationHistory';
import { formatDuration, useCaseTime, useTimeHeartbeat } from './timeApi';

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

type CaseTab = 'activity' | 'tasks' | 'artifacts' | 'evidence' | 'report' | 'network' | 'time' | 'guests' | 'audit' | 'automation';
// Six tabs carry the investigation; the rest sit behind "More" so a one-person team sees less chrome.
const PRIMARY_TABS: CaseTab[] = ['activity', 'tasks', 'artifacts', 'evidence', 'report', 'network'];
const MORE_TABS: CaseTab[] = ['time', 'guests', 'audit', 'automation'];
const TAB_LABEL: Record<CaseTab, string> = {
    activity: 'Activity', tasks: 'Tasks', artifacts: 'Artifacts & assets', evidence: 'Evidence', report: 'Report',
    network: 'Network', time: 'Time', guests: 'Guests', audit: 'Audit', automation: 'Automation',
};

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
    const [showClose, setShowClose] = useState(false);
    const [descOpen, setDescOpen] = useState(false);
    const [confirmRemove, setConfirmRemove] = useState<number | null>(null);
    const [artifactNote, setArtifactNote] = useState<{ id: number; text: string } | null>(null);

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
    const setStatus = useMutation({
        mutationFn: async (status: string) => api.put(`/cases/${id}`, { status }),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['case', id] });
            queryClient.invalidateQueries({ queryKey: ['cases'] });
            queryClient.invalidateQueries({ queryKey: ['audit-logs', 'case', id] });
        },
    });
    const editError = (() => {
        const d = (updateCaseMutation.error as { response?: { data?: { detail?: unknown } } } | null)?.response?.data?.detail;
        if (!updateCaseMutation.isError) return null;
        return typeof d === 'string' ? d : 'Could not save the case.';
    })();

    const { data: caseData, isLoading, isError, error: caseError } = useQuery({
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
    const [showDelete, setShowDelete] = useState(false);
    const reassign = useMutation({
        mutationFn: async (owner_id: number | null) => api.put(`/cases/${id}`, { owner_id }),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['case', id] });
            queryClient.invalidateQueries({ queryKey: ['cases'] });
            queryClient.invalidateQueries({ queryKey: ['audit-logs', 'case', id] });
        },
    });
    const { data: slackCfg } = useQuery({
        queryKey: ['war-room-settings'],
        queryFn: async () => (await api.get('/slack/war-room-settings')).data as { configured: boolean; war_room_name_template: string | null },
        enabled: canWarRoom, retry: false,
    });
    // Viewers can't list guests (the API returns 403), so they don't get the tab.
    const canSeeGuests = isAdmin || me?.role === 'analyst';
    useTimeHeartbeat(id ? parseInt(id) : undefined, canSeeGuests);
    const { data: timeData } = useCaseTime(id ? parseInt(id) : 0, !!id && canSeeGuests);

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
        onSuccess: (d, artifactId) => {
            queryClient.invalidateQueries({ queryKey: ['iocs'] });
            setArtifactNote({ id: artifactId, text: d.created ? 'Promoted to IOC' : 'Already an IOC' });
        },
        onError: (_e, artifactId) => setArtifactNote({ id: artifactId, text: 'Could not promote to IOC' }),
    });

    const removeArtifactMutation = useMutation({
        mutationFn: async (artifactId: number) => {
            await api.delete(`/artifacts/${artifactId}/case/${id}`);
        },
        onSuccess: () => {
            setConfirmRemove(null);
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
                <Skeleton rows={8} />
            </PageContainer>
        );
    }

    // An error wins over cached data: a case from another organization (or a deleted one) must not linger.
    if (isError || !caseData) {
        const status = (caseError as { response?: { status?: number } } | null)?.response?.status;
        const notFound = !isError || status === 404 || status === 403;
        return (
            <PageContainer>
                <div className="py-16 text-center space-y-2">
                    <p className="text-fg">{notFound ? 'Case not found.' : 'Could not load this case.'}</p>
                    <p className="text-sm text-fg-3">{notFound ? 'It may have been deleted, or it belongs to another organization.' : 'Check your connection and try again.'}</p>
                    <Link to="/cases" className="inline-block text-sm text-accent hover:underline">Back to cases</Link>
                </div>
            </PageContainer>
        );
    }

    const isClosed = caseData.status === 'closed';
    const changeStatus = (status: string) => {
        if (status === 'closed') { setShowClose(true); return; }
        setStatus.mutate(status);
    };
    const visibleTabs: CaseTab[] = [...PRIMARY_TABS, ...(MORE_TABS.includes(activeTab) ? [activeTab] : [])];

    return (
        <PageContainer>
            <div className="min-w-0 flex flex-col bg-panel rounded-md border border-line relative">
                {/* Header */}
                <div className="p-6 pb-2">
                    <div className="flex flex-wrap items-center gap-4 mb-4">
                        <Link to="/cases" aria-label="Back to cases" className="p-2 rounded-lg text-fg-3 hover:text-fg hover:bg-raised transition-colors focus-visible:outline-2 focus-visible:outline-accent">
                            <ArrowLeft size={20} />
                        </Link>
                        {/* Wide enough that a long title stays on one or two lines; the actions wrap below it instead. */}
                        <div className="flex-1 min-w-[min(100%,36rem)]">
                            <div className="mb-1">
                                <h1 className="text-2xl font-semibold text-fg">{caseData.title}</h1>
                            </div>
                            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-fg-3">
                                <span className="flex items-center gap-1.5 whitespace-nowrap">
                                    <Hash size={12} aria-hidden />
                                    <span className="num">{caseData.id}</span>
                                </span>
                                <span className="flex items-center gap-1.5 whitespace-nowrap">
                                    <Calendar size={12} aria-hidden />
                                    {formatDateTime(caseData.created_at, me?.timezone)}
                                </span>
                                {canSeeGuests && !!timeData?.total_seconds && (
                                    <span className="flex items-center gap-1.5" title="Time spent on this case">
                                        <Clock size={12} aria-hidden />
                                        {formatDuration(timeData.total_seconds)}
                                    </span>
                                )}
                                <span className="flex items-center gap-1.5 whitespace-nowrap">
                                    <Shield size={12} aria-hidden />
                                    Owner:
                                    {canWarRoom ? (
                                        <AssigneeSelect label="Case owner" value={caseData.owner_id} currentName={caseData.owner_name}
                                            disabled={reassign.isPending} onChange={(v) => reassign.mutate(v)} />
                                    ) : <span>{caseData.owner_name ?? 'Unassigned'}</span>}
                                    {reassign.isError && <span role="alert" className="text-severity-critical">Could not reassign</span>}
                                </span>
                                <SLAPanel caseData={caseData} />
                            </div>
                        </div>
                        {/* Status first: "where is this case at" is the first question on every visit. */}
                        <div className="flex flex-wrap items-center gap-2">
                            {canWarRoom ? (
                                <select aria-label="Case status" value={caseData.status} disabled={setStatus.isPending}
                                    onChange={(e) => changeStatus(e.target.value)}
                                    className={cn('stamp h-8 cursor-pointer bg-panel pr-1 hover:bg-raised', TONE_TEXT[statusTone(caseData.status)])}>
                                    {CASE_STATUSES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
                                </select>
                            ) : (
                                <Stamp tone={statusTone(caseData.status)} className="h-8">{statusLabel(caseData.status)}</Stamp>
                            )}
                            <SeverityStamp severity={caseData.severity} className="h-8" />
                            {caseData.escalation && <EscalationBadge e={caseData.escalation} />}
                            <EscalationActions caseData={caseData} me={me} />
                            {caseData.slack_channel_id && <WarRoomPill caseData={caseData} canEdit={canWarRoom} />}
                            {!caseData.slack_channel_id && canWarRoom && slackCfg?.configured && (
                                <Button size="sm" variant="secondary" onClick={() => setShowWarRoom(true)}>
                                    <Hash size={12} aria-hidden /> Open war room
                                </Button>
                            )}
                            {id && <FollowButton caseId={parseInt(id)} ready={!!me} isOwner={!!me && caseData.owner_id === me.id} />}
                            {canWarRoom && !isClosed && (
                                <Button size="sm" onClick={() => setShowClose(true)}>
                                    <CheckCircle2 size={13} aria-hidden /> Close case
                                </Button>
                            )}
                            {/* Edit and delete wrap as one group; delete sits apart behind a rule (destructive actions stay isolated). */}
                            <span className="inline-flex items-center gap-1">
                                <Button variant="ghost" size="icon" onClick={() => setShowEditModal(true)} aria-label="Edit case" title="Edit case" className="h-8 w-8">
                                    <Edit size={16} />
                                </Button>
                                {isAdmin && (
                                    <span className="ml-1 pl-2 border-l border-line">
                                        <Button variant="ghost" size="icon" onClick={() => setShowDelete(true)} aria-label="Delete case" title="Delete case" className="h-8 w-8 hover:text-severity-critical">
                                            <Trash2 size={16} />
                                        </Button>
                                    </span>
                                )}
                            </span>
                        </div>
                    </div>
                    {setStatus.isError && <p role="alert" className="mb-3 text-sm text-severity-critical">Could not change the status. Try again, or use Edit to see what is blocking it.</p>}
                    {id && isAdmin && <DeleteCaseModal caseId={parseInt(id)} title={caseData.title} open={showDelete} onClose={() => setShowDelete(false)} />}
                    {id && <CloseCaseModal caseId={parseInt(id)} open={showClose} onClose={() => setShowClose(false)} onClosed={() => setShowClose(false)} />}

                    {isClosed && (
                        <div className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-md border border-line bg-surface px-4 py-3 text-sm">
                            <Stamp tone="neutral" className="animate-stamp text-sm px-2 -rotate-2">Closed</Stamp>
                            {caseData.outcome && <span className="text-fg">as <span className="font-medium">{outcomeLabel(caseData.outcome)}</span></span>}
                            {caseData.closing_note && <span className="text-fg-2 truncate max-w-xl" title={caseData.closing_note}>{caseData.closing_note}</span>}
                            <button type="button" onClick={() => selectTab('report')} className="ml-auto text-accent hover:underline rounded-sm focus-visible:outline-2 focus-visible:outline-accent">Open report</button>
                        </div>
                    )}

                    {caseData.description && (
                        <div className="mb-4 max-w-[75ch]">
                            <p className={cn('text-fg-2 leading-relaxed text-sm whitespace-pre-line', !descOpen && 'line-clamp-3')}>{caseData.description}</p>
                            {caseData.description.length > 240 && (
                                <button type="button" onClick={() => setDescOpen(!descOpen)} aria-expanded={descOpen}
                                    className="mt-1 text-xs text-accent hover:underline rounded-sm focus-visible:outline-2 focus-visible:outline-accent">
                                    {descOpen ? 'Show less' : 'Show full initial report'}
                                </button>
                            )}
                        </div>
                    )}

                    {id && canWarRoom && caseData.slack_channel_id && <WarRoomSuggestions caseId={parseInt(id)} />}
                    {id && <OpenWarRoomModal open={showWarRoom} onClose={() => setShowWarRoom(false)} caseId={parseInt(id)} title={caseData.title} template={slackCfg?.war_room_name_template ?? null} />}
                    {id && <TriagePanel caseId={parseInt(id)} />}
                    {id && <EscalationHistory caseId={parseInt(id)} />}

                </div>

                {/* Tabs (sticky while the page scrolls inside <main>) */}
                <div className="sticky top-0 z-10 bg-panel px-6 pt-3">
                    <FolderTabs label="Case sections" idPrefix="case-tab" value={activeTab} onChange={selectTab}
                        items={visibleTabs.map((t) => ({ value: t, label: TAB_LABEL[t] }))}>
                        {/* ponytail: native select as the overflow menu; keyboard and screen readers come for free */}
                        <select aria-label="More case sections" value=""
                            onChange={(e) => e.target.value && selectTab(e.target.value as CaseTab)}
                            className="shrink-0 [field-sizing:content] bg-transparent text-sm text-fg-3 hover:text-fg-2 cursor-pointer rounded-sm">
                            <option value="">More…</option>
                            {MORE_TABS.filter((t) => (t !== 'guests' && t !== 'time') || canSeeGuests).map((t) => (
                                <option key={t} value={t}>{TAB_LABEL[t]}</option>
                            ))}
                        </select>
                    </FolderTabs>
                    {pendingTab && (
                        <div role="alert" className="flex flex-wrap items-center gap-2 text-xs text-fg bg-severity-medium/10 border border-severity-medium/30 rounded-md px-3 py-2 mt-2">
                            <span className="mr-auto">The report has unsaved changes. Discard them and switch to <span className="font-medium">{TAB_LABEL[pendingTab]}</span>?</span>
                            <Button size="sm" variant="danger" onClick={() => { setReportDirty(false); setActiveTab(pendingTab); setPendingTab(null); }}>Discard</Button>
                            <Button size="sm" variant="secondary" onClick={() => setPendingTab(null)}>Keep editing</Button>
                        </div>
                    )}
                </div>

                {/* Tab Content */}
                <div id="case-tab-panel" role="tabpanel" aria-labelledby={`case-tab-${activeTab}`} className="flex-1 min-h-[50vh] p-6 pt-4">
                    <div key={activeTab}>
                            {activeTab === 'activity' && id && (
                                <div className="space-y-6">
                                    <IncidentOverview caseData={caseData} tz={me?.timezone} canEdit={canSeeGuests}
                                        onOpenInvestigation={(eid) => document.getElementById(`case-event-${eid}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })} />
                                    <CaseTimeline caseId={parseInt(id)} activity={caseData.timeline_events ?? []} />
                                </div>
                            )}

                            {activeTab === 'time' && id && canSeeGuests && <CaseTime caseId={parseInt(id)} />}

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
                                        <h2 className="text-sm font-semibold text-fg">Artifacts <span className="num text-xs font-normal text-fg-3">{artifacts?.length ?? 0}</span></h2>
                                        {canSeeGuests && (
                                            <Button size="sm" onClick={() => setShowArtifactModal(true)}>
                                                <Plus size={12} aria-hidden /> Add artifact or asset
                                            </Button>
                                        )}
                                    </div>

                                    {isLoadingArtifacts ? (
                                        <Skeleton rows={3} />
                                    ) : artifacts && artifacts.length > 0 ? (
                                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                            {artifacts.map((artifact: any) => {
                                                const Icon = ARTIFACT_ICONS[artifact.artifact_type] || FileText;
                                                const isShared = !artifact.isolated && (artifact.case_ids?.length || 0) > 1;
                                                return (
                                                    <div key={artifact.id} className="p-4 rounded-md bg-panel border border-line flex items-start gap-3 group/artifact">
                                                        <div className="p-2 bg-raised rounded-md">
                                                            <Icon size={16} className="text-accent" aria-hidden />
                                                        </div>
                                                        <div className="min-w-0 flex-1">
                                                            <div className="flex items-center gap-2 mb-0.5">
                                                                <p className="label-caps">{artifact.artifact_type}</p>
                                                                {isShared && (
                                                                    <Badge variant="brand">
                                                                        <Link2 size={10} aria-hidden />
                                                                        Shared ({artifact.case_ids.length})
                                                                    </Badge>
                                                                )}
                                                                {artifact.isolated && (
                                                                    <Badge variant="medium">Isolated</Badge>
                                                                )}
                                                            </div>
                                                            <p className="text-sm text-fg font-mono truncate">{artifact.value}</p>
                                                            <p className="text-xs text-fg-3 mt-2">
                                                                Added {formatDateTime(artifact.created_at, me?.timezone)}
                                                            </p>
                                                            {artifactNote?.id === artifact.id && <p role="status" className="text-xs text-fg-2 mt-1">{artifactNote?.text}</p>}
                                                            <button onClick={() => setIntelOpen(intelOpen === artifact.id ? null : artifact.id)}
                                                                aria-expanded={intelOpen === artifact.id} className="mt-2 text-xs text-accent hover:underline rounded-sm focus-visible:outline-2 focus-visible:outline-accent">Threat intel</button>
                                                            {intelOpen === artifact.id && <div className="mt-2"><EnrichmentPanel kind="artifact" id={artifact.id} canRun={canRunIntel} /></div>}
                                                        </div>
                                                        {/* Hidden until hover on mouse devices; always shown on touch and when focused. */}
                                                        <div className="flex items-center gap-0.5 opacity-0 group-hover/artifact:opacity-100 group-focus-within/artifact:opacity-100 [@media(hover:none)]:opacity-100">
                                                            {confirmRemove === artifact.id ? (
                                                                <span className="flex items-center gap-1.5 text-xs">
                                                                    <span className="text-fg-2">Remove?</span>
                                                                    <button type="button" onClick={() => removeArtifactMutation.mutate(artifact.id)} disabled={removeArtifactMutation.isPending}
                                                                        className="px-1.5 py-0.5 rounded-sm text-severity-critical hover:bg-raised">Remove</button>
                                                                    <button type="button" onClick={() => setConfirmRemove(null)}
                                                                        className="px-1.5 py-0.5 rounded-sm text-fg-3 hover:bg-raised">Cancel</button>
                                                                </span>
                                                            ) : <>
                                                                <button type="button"
                                                                    onClick={() => promoteArtifactMutation.mutate(artifact.id)}
                                                                    className="p-1.5 text-fg-3 hover:text-accent hover:bg-raised rounded-sm transition-colors focus-visible:outline-2 focus-visible:outline-accent"
                                                                    aria-label={`Promote ${artifact.value} to IOC`} title="Promote to IOC"
                                                                >
                                                                    <ShieldAlert size={14} />
                                                                </button>
                                                                <button type="button"
                                                                    onClick={() => setConfirmRemove(artifact.id)}
                                                                    className="p-1.5 text-fg-3 hover:text-severity-critical hover:bg-raised rounded-sm transition-colors focus-visible:outline-2 focus-visible:outline-accent"
                                                                    aria-label={`Remove ${artifact.value} from case`} title="Remove from case"
                                                                >
                                                                    <Unlink size={14} />
                                                                </button>
                                                            </>}
                                                        </div>
                                                    </div>
                                                );
                                            })}
                                        </div>
                                    ) : (
                                        <p className="text-sm text-fg-3">No artifacts yet. Add the IPs, domains, hashes and other evidence you find.</p>
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
                                        <div className="pl-12"><Skeleton rows={3} /></div>
                                    ) : auditLogs && auditLogs.length > 0 ? (
                                        auditLogs.map((log: any, i: number) => (
                                            <div key={log.id || i} className="relative pl-12 group">
                                                <div className="absolute left-[-22px] top-0 p-1.5 rounded-full bg-panel border border-line group-hover:border-line-strong transition-colors z-10">
                                                    <Shield size={14} className="text-fg-3 group-hover:text-fg transition-colors" />
                                                </div>

                                                <div className="flex flex-col gap-1">
                                                    <div className="flex items-center gap-2">
                                                        <span className="text-xs num text-fg-3">{formatDateTime(log.created_at, me?.timezone)}</span>
                                                        <span className={cn(
                                                            "text-xs font-medium px-1.5 py-0.5 rounded-sm border",
                                                            log.action === 'create' || log.action.endsWith('_added') || log.action === 'task_completed' ? "bg-lime/10 text-emerald-700 dark:text-emerald-300 border-lime/30" :
                                                                log.action.includes('remove') || log.action.includes('delete') ? "bg-severity-critical/10 text-severity-critical border-severity-critical/30" :
                                                                    "bg-accent/10 text-accent border-accent/30"
                                                        )}>
                                                            {log.action.replace(/_/g, ' ')}
                                                        </span>
                                                        <span className="inline-flex items-center gap-1.5 text-xs text-fg-3">
                                                            {log.user ? <>
                                                                <Avatar userId={log.user.id} name={log.user.full_name || log.user.email} size={18} />
                                                                by {log.user.full_name || log.user.email}
                                                            </> : `by ${log.actor || log.user_email || 'system'}`}
                                                        </span>
                                                    </div>
                                                    {log.changes && Object.keys(log.changes).length > 0 && (
                                                        <div className="p-3 rounded-md border border-line bg-panel space-y-1">
                                                            {Object.entries(log.changes).map(([field, change]: [string, any]) => (
                                                                <div key={field} className="text-xs font-mono break-words">
                                                                    <span className="text-fg-3">{field.replace(/_/g, ' ')}:</span>{' '}
                                                                    {change && typeof change === 'object' && 'to' in change ? <>
                                                                        <span className="text-severity-critical line-through decoration-severity-critical/50">{JSON.stringify(change.from)}</span>
                                                                        {' → '}
                                                                        <span className="text-emerald-700 dark:text-emerald-300">{JSON.stringify(change.to)}</span>
                                                                    </> : <span className="text-fg-2">{typeof change === 'string' ? change : JSON.stringify(change)}</span>}
                                                                </div>
                                                            ))}
                                                        </div>
                                                    )}
                                                </div>
                                            </div>
                                        ))
                                    ) : (
                                        <div className="pl-12 text-fg-3 text-sm">No audit entries for this case yet.</div>
                                    )}
                                </div>
                            )}
                    </div>
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
                    <Button variant="secondary" onClick={() => setShowArtifactModal(false)}>Cancel</Button>
                    <Button onClick={() => addArtifactMutation.mutate(newArtifact)}
                        disabled={!newArtifact.value.trim() || addArtifactMutation.isPending}>
                        {addArtifactMutation.isPending ? 'Adding…' : 'Add artifact'}
                    </Button>
                </>}
            >
                <div className="space-y-4">
                    <div role="radiogroup" aria-label="What are you adding?" className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        {ITEM_KINDS.map(([k, label, hint]) => (
                            <button key={k} type="button" role="radio" aria-checked={itemKind === k} onClick={() => setItemKind(k)}
                                className={cn('text-left rounded-md border p-3 transition-colors',
                                    itemKind === k ? 'border-accent bg-accent/10' : 'border-line hover:bg-raised')}>
                                <span className="text-sm font-medium text-fg">{label}</span>
                                <span className="mt-1 block text-xs text-fg-3">{hint}</span>
                            </button>
                        ))}
                    </div>
                    {itemKind === 'artifact' ? <>
                        <div>
                            <Label htmlFor="new-artifact-type">Type</Label>
                            <Select id="new-artifact-type"
                                value={newArtifact.type}
                                onChange={(e) => setNewArtifact({ ...newArtifact, type: e.target.value })}
                            >
                                {artifactTypeOptions.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                            </Select>
                        </div>
                        <div>
                            <Label htmlFor="new-artifact-value">Value</Label>
                            <Input id="new-artifact-value"
                                type="text"
                                value={newArtifact.value}
                                onChange={(e) => setNewArtifact({ ...newArtifact, value: e.target.value })}
                                className="font-mono"
                                placeholder="e.g. 203.0.113.7 or evil.example"
                            />
                        </div>
                        {addArtifactMutation.isError && <p role="alert" className="text-xs text-severity-critical">Could not add the artifact. Check the value matches the type and try again.</p>}
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
