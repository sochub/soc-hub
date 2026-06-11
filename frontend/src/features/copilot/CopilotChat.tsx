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

    const { data: session, isLoading: isLoadingSession } = useQuery<Session>({
        queryKey: sessionKey,
        queryFn: async () => {
            const url = isCase ? `/copilot/sessions/${caseId}` : '/copilot/sessions/general';
            return (await api.get(url)).data;
        },
    });

    const messages = session?.messages || [];

    const scrollToBottom = () => messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
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
        <div className="flex flex-col h-full bg-slate-950/50">
            {/* Header */}
            <div className="p-4 border-b border-slate-800/50 bg-slate-900/50 backdrop-blur-md flex items-center justify-between shrink-0">
                <h3 className="font-bold text-slate-100 flex items-center gap-2 text-sm uppercase tracking-wider">
                    <Sparkles size={16} className="text-brand-400" />
                    Copilot
                    <span className="text-[10px] font-medium text-slate-500 normal-case tracking-normal">
                        {isCase ? `· case #${caseId}` : '· general'}
                    </span>
                </h3>
                <div className="flex items-center gap-2">
                    {isCase && (
                        <button
                            onClick={() => restartMutation.mutate()}
                            disabled={restartMutation.isPending || isLoadingSession}
                            title="Restart session"
                            aria-label="Restart session"
                            className="p-1.5 text-slate-500 hover:text-white hover:bg-slate-800 rounded-md transition-all disabled:opacity-50"
                        >
                            <RotateCcw size={14} className={restartMutation.isPending ? 'animate-spin' : ''} />
                        </button>
                    )}
                    {onClose && (
                        <button
                            onClick={onClose}
                            aria-label="Close copilot"
                            className="p-1.5 text-slate-500 hover:text-white hover:bg-slate-800 rounded-md transition-all"
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
                        <Loader2 size={20} className="animate-spin text-brand-400" />
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
                                    ? 'bg-brand-900/20 border-brand-500/30 text-brand-400'
                                    : 'bg-blue-900/20 border-blue-500/30 text-blue-400'
                            )}>
                                {msg.role === 'assistant' ? <Bot size={16} /> : <UserIcon size={16} />}
                            </div>
                            <div className={cn(
                                'rounded-xl p-3 text-sm max-w-[85%]',
                                msg.role === 'assistant'
                                    ? 'bg-slate-800/80 text-slate-200 border border-slate-700/50 shadow-sm'
                                    : 'bg-blue-600/10 text-blue-100 border border-blue-500/20'
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
                            <div className="w-8 h-8 rounded-lg bg-blue-900/20 border border-blue-500/30 text-blue-400 flex items-center justify-center shrink-0 shadow-lg">
                                <UserIcon size={16} />
                            </div>
                            <div className="rounded-xl p-3 text-sm max-w-[85%] bg-blue-600/10 text-blue-100 border border-blue-500/20">
                                <p className="leading-relaxed">{chatMutation.variables}</p>
                            </div>
                        </motion.div>
                        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex gap-3">
                            <div className="w-8 h-8 rounded-lg bg-brand-900/20 border border-brand-500/30 text-brand-400 flex items-center justify-center shrink-0">
                                <Bot size={16} />
                            </div>
                            <div className="bg-slate-800/50 rounded-xl p-3 text-sm text-slate-400 flex items-center gap-2 border border-slate-700/50">
                                <Loader2 size={14} className="animate-spin text-brand-400" />
                                <span className="animate-pulse">Analyzing…</span>
                            </div>
                        </motion.div>
                    </>
                )}
                <div ref={messagesEndRef} />
            </div>

            {/* Input */}
            <div className="p-4 border-t border-slate-800/50 bg-slate-900/30 shrink-0">
                <form onSubmit={handleSubmit} className="relative group">
                    <div className="absolute inset-0 bg-brand-500/5 rounded-lg -z-10 group-focus-within:bg-brand-500/10 transition-colors" />
                    <input
                        type="text"
                        value={input}
                        onChange={(e) => setInput(e.target.value)}
                        placeholder={isCase ? 'Ask about this case…' : 'Ask about the queue or SOC topics…'}
                        disabled={isLoadingSession || !session}
                        aria-label="Message the copilot"
                        className="w-full bg-slate-950/80 border border-slate-700/50 rounded-lg py-3 pl-10 pr-10 text-sm focus:outline-none focus:ring-1 focus:ring-brand-500/50 focus:border-brand-500/50 transition-all text-slate-200 placeholder:text-slate-600 font-medium disabled:opacity-50"
                    />
                    <Terminal size={14} className="absolute left-3.5 top-3.5 text-slate-500 group-focus-within:text-brand-400 transition-colors" />
                    <button
                        type="submit"
                        disabled={!input.trim() || chatMutation.isPending || !session}
                        aria-label="Send message"
                        className="absolute right-2 top-2 p-1.5 text-slate-400 hover:text-white hover:bg-brand-600 rounded-md disabled:opacity-50 disabled:hover:bg-transparent transition-all"
                    >
                        <Send size={14} />
                    </button>
                </form>
            </div>
        </div>
    );
}
