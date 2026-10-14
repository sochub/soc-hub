import { useState, useRef, useEffect } from 'react';
import { Send, Bot, User as UserIcon, Loader2, Sparkles, Terminal, RotateCcw, X } from 'lucide-react';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { motion } from 'framer-motion';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import CopilotActionCard, { CopilotSuggestionChip } from './CopilotActionCard';
import type { CopilotAction } from '../../types';

interface Message {
    id: number;
    session_id: number;
    role: 'user' | 'assistant';
    content: string;
    action?: CopilotAction | null;
    suggestions?: CopilotAction[] | null;
    created_at: string;
}

interface Session {
    id: number;
    case_id: number | null;
    messages: Message[];
}

interface CopilotChatProps {
    /** When set (> 0), the copilot is scoped to that case; otherwise it runs in general mode. */
    caseId?: number | null;
    /** When provided, renders a close button in the header (used by the slide-over widget). */
    onClose?: () => void;
}

export default function CopilotChat({ caseId, onClose }: CopilotChatProps) {
    const isCase = !!caseId && caseId > 0;
    const [input, setInput] = useState('');
    const messagesEndRef = useRef<HTMLDivElement>(null);
    const queryClient = useQueryClient();

    const sessionKey = isCase ? ['copilot-session', caseId] : ['copilot-session', 'general'];

    const { data: aiInfo } = useQuery({
        queryKey: ['ai-info'],
        queryFn: async () => (await api.get('/ai/info')).data as { provider: string | null; model: string | null },
        staleTime: 60_000,
    });
    const { data: session, isLoading: isLoadingSession } = useQuery<Session>({
        queryKey: sessionKey,
        queryFn: async () => {
            const url = isCase ? `/copilot/sessions/${caseId}` : '/copilot/sessions/general';
            return (await api.get(url)).data;
        },
    });

    const messages = session?.messages || [];

    // Braces matter: newer browsers return a Promise from scrollIntoView, and an effect must not return one.
    const scrollToBottom = () => { messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' }); };
    useEffect(scrollToBottom, [messages]);

    const chatMutation = useMutation({
        mutationFn: async (message: string) => {
            const response = await api.post('/copilot/chat', {
                case_id: isCase ? caseId : null,
                session_id: session?.id,
                message,
            });
            return response.data;
        },
        onSuccess: () => queryClient.invalidateQueries({ queryKey: sessionKey }),
    });

    const restartMutation = useMutation({
        mutationFn: async () => {
            // Restart is only available for case sessions.
            return (await api.post(`/copilot/sessions/${caseId}/restart`)).data;
        },
        onSuccess: () => queryClient.invalidateQueries({ queryKey: sessionKey }),
    });

    const handleSubmit = (e: React.FormEvent) => {
        e.preventDefault();
        if (!input.trim() || chatMutation.isPending || !session) return;
        const message = input;
        setInput('');
        chatMutation.mutate(message);
    };

    return (
        <div className="flex flex-col h-full bg-canvas/50">
            {/* Header */}
            <div className="p-4 border-b border-line bg-panel/50 backdrop-blur-md flex items-center justify-between shrink-0">
                <h3 className="font-bold text-fg flex items-center gap-2 text-sm uppercase tracking-wider">
                    <Sparkles size={16} className="text-accent" />
                    Copilot
                    <span className="text-[10px] font-medium text-fg-3 normal-case tracking-normal">
                        {isCase ? `· case #${caseId}` : '· general'}
                    </span>
                    {aiInfo?.provider && (
                        <span className="font-mono text-[10px] font-medium text-fg-3 normal-case tracking-normal truncate max-w-56" title="Where case data is sent">
                            · AI: {aiInfo.provider} · {aiInfo.model}
                        </span>
                    )}
                </h3>
                <div className="flex items-center gap-2">
                    {isCase && (
                        <button
                            onClick={() => restartMutation.mutate()}
                            disabled={restartMutation.isPending || isLoadingSession}
                            title="Restart session"
                            aria-label="Restart session"
                            className="p-1.5 text-fg-3 hover:text-white hover:bg-raised rounded-md transition-all disabled:opacity-50"
                        >
                            <RotateCcw size={14} className={restartMutation.isPending ? 'animate-spin' : ''} />
                        </button>
                    )}
                    {onClose && (
                        <button
                            onClick={onClose}
                            aria-label="Close copilot"
                            className="p-1.5 text-fg-3 hover:text-white hover:bg-raised rounded-md transition-all"
                        >
                            <X size={16} />
                        </button>
                    )}
                </div>
            </div>

            {/* Messages */}
            <div className="flex-1 overflow-y-auto p-4 space-y-6 scrollbar-thin scrollbar-thumb-slate-800 scrollbar-track-transparent">
                {isLoadingSession ? (
                    <div className="flex items-center justify-center py-8">
                        <Loader2 size={20} className="animate-spin text-accent" />
                    </div>
                ) : (
                    messages.map((msg) => (
                        <motion.div
                            initial={{ opacity: 0, y: 10 }}
                            animate={{ opacity: 1, y: 0 }}
                            key={msg.id}
                            className={cn('flex gap-3', msg.role === 'user' ? 'flex-row-reverse' : '')}
                        >
                            <div className={cn(
                                'w-8 h-8 rounded-lg flex items-center justify-center shrink-0 border shadow-lg',
                                msg.role === 'assistant'
                                    ? 'bg-accent/10 border-accent/30 text-accent'
                                    : 'bg-accent/10 border-accent/30 text-accent'
                            )}>
                                {msg.role === 'assistant' ? <Bot size={16} /> : <UserIcon size={16} />}
                            </div>
                            <div className={cn(
                                'rounded-xl p-3 text-sm max-w-[85%]',
                                msg.role === 'assistant'
                                    ? 'bg-raised/80 text-fg border border-line shadow-xs'
                                    : 'bg-accent/10 text-accent border border-accent/20'
                            )}>
                                {msg.role === 'assistant' ? (
                                    <>
                                        <div className="copilot-markdown leading-relaxed">
                                            <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
                                        </div>
                                        {msg.action && <CopilotActionCard action={msg.action} caseId={caseId} />}
                                        {(msg.suggestions ?? []).map((s, i) => (
                                            <CopilotSuggestionChip key={`${msg.id}-sugg-${i}`} action={s} caseId={caseId} />
                                        ))}
                                    </>
                                ) : (
                                    <p className="leading-relaxed whitespace-pre-wrap">{msg.content}</p>
                                )}
                            </div>
                        </motion.div>
                    ))
                )}

                {chatMutation.isPending && (
                    <>
                        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="flex gap-3 flex-row-reverse">
                            <div className="w-8 h-8 rounded-lg bg-accent/10 border border-accent/30 text-accent flex items-center justify-center shrink-0 shadow-lg">
                                <UserIcon size={16} />
                            </div>
                            <div className="rounded-xl p-3 text-sm max-w-[85%] bg-accent/10 text-accent border border-accent/20">
                                <p className="leading-relaxed">{chatMutation.variables}</p>
                            </div>
                        </motion.div>
                        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex gap-3">
                            <div className="w-8 h-8 rounded-lg bg-accent/10 border border-accent/30 text-accent flex items-center justify-center shrink-0">
                                <Bot size={16} />
                            </div>
                            <div className="bg-raised/50 rounded-xl p-3 text-sm text-fg-2 flex items-center gap-2 border border-line">
                                <Loader2 size={14} className="animate-spin text-accent" />
                                <span className="animate-pulse">Analyzing…</span>
                            </div>
                        </motion.div>
                    </>
                )}
                <div ref={messagesEndRef} />
            </div>

            {/* Input */}
            <div className="p-4 border-t border-line bg-panel/30 shrink-0">
                <form onSubmit={handleSubmit} className="relative group">
                    <div className="absolute inset-0 bg-accent/5 rounded-lg -z-10 group-focus-within:bg-accent/10 transition-colors" />
                    <input
                        type="text"
                        value={input}
                        onChange={(e) => setInput(e.target.value)}
                        placeholder={isCase ? 'Ask about this case…' : 'Ask about the queue or SOC topics…'}
                        disabled={isLoadingSession || !session}
                        aria-label="Message the copilot"
                        className="w-full bg-canvas/80 border border-line rounded-lg py-3 pl-10 pr-10 text-sm focus:outline-hidden focus:ring-1 focus:ring-accent/50 focus:border-accent/50 transition-all text-fg placeholder:text-fg-3 font-medium disabled:opacity-50"
                    />
                    <Terminal size={14} className="absolute left-3.5 top-3.5 text-fg-3 group-focus-within:text-accent transition-colors" />
                    <button
                        type="submit"
                        disabled={!input.trim() || chatMutation.isPending || !session}
                        aria-label="Send message"
                        className="absolute right-2 top-2 p-1.5 text-fg-2 hover:text-on-accent hover:bg-accent rounded-md disabled:opacity-50 disabled:hover:bg-transparent transition-all"
                    >
                        <Send size={14} />
                    </button>
                </form>
            </div>
        </div>
    );
}
