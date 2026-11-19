import { useState, useRef, useEffect } from 'react';
import { Send, Bot, User as UserIcon, Loader2, Eraser, X, Sparkles } from 'lucide-react';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { motion } from 'framer-motion';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import CopilotActionCard, { CopilotSuggestionChip } from './CopilotActionCard';
import type { CopilotAction } from '../../types';
import { Button, Skeleton } from '../../components/ui';

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
    const inputRef = useRef<HTMLTextAreaElement>(null);
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
        // On error too: a gateway timeout can still leave a saved reply on the server.
        onSettled: () => queryClient.invalidateQueries({ queryKey: sessionKey }),
    });

    const restartMutation = useMutation({
        mutationFn: async () => (await api.post(`/copilot/sessions/${isCase ? caseId : 'general'}/restart`)).data,
        onSuccess: () => { chatMutation.reset(); return queryClient.invalidateQueries({ queryKey: sessionKey }); },
    });

    // Grow with the text (pasted reports, logs) up to max-h, then scroll.
    useEffect(() => {
        const el = inputRef.current;
        if (!el) return;
        el.style.height = 'auto';
        el.style.height = `${el.scrollHeight}px`;
    }, [input]);

    const handleSubmit = (e?: React.FormEvent) => {
        e?.preventDefault();
        if (!input.trim() || chatMutation.isPending || !session) return;
        const message = input;
        setInput('');
        chatMutation.mutate(message);
    };

    return (
        <div className="flex flex-col h-full bg-surface">
            {/* Header */}
            <div className="px-4 py-3 border-b border-line bg-panel flex items-center justify-between gap-2 shrink-0">
                <div className="min-w-0">
                    <h2 className="text-sm font-semibold text-fg flex items-center gap-2">
                        <Sparkles size={14} className="text-accent" aria-hidden />
                        Copilot
                        <span className="text-xs font-normal text-fg-3">
                            {isCase ? <>· case <span className="num">#{caseId}</span></> : '· general'}
                        </span>
                    </h2>
                    {aiInfo?.provider && (
                        <p className="mt-0.5 text-xs text-fg-3 truncate" title="Where case data is sent">
                            AI: <span className="font-mono">{aiInfo.provider} · {aiInfo.model}</span>
                        </p>
                    )}
                </div>
                <div className="flex items-center gap-1 shrink-0">
                    <Button
                        variant="ghost" size="sm"
                        onClick={() => restartMutation.mutate()}
                        disabled={restartMutation.isPending || chatMutation.isPending || isLoadingSession}
                        title="Clear conversation"
                    >
                        {restartMutation.isPending ? <Loader2 size={13} className="animate-spin" aria-hidden /> : <Eraser size={13} aria-hidden />}
                        Clear
                    </Button>
                    {onClose && (
                        <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close copilot" title="Close (⌘J)">
                            <X size={16} />
                        </Button>
                    )}
                </div>
            </div>

            {/* Messages */}
            <div className="flex-1 overflow-y-auto p-4 space-y-5" aria-live="polite">
                {isLoadingSession ? (
                    <Skeleton rows={3} />
                ) : (
                    messages.map((msg) => (
                        <motion.div
                            initial={{ opacity: 0, y: 10 }}
                            animate={{ opacity: 1, y: 0 }}
                            key={msg.id}
                            className={cn('flex gap-3', msg.role === 'user' ? 'flex-row-reverse' : '')}
                        >
                            <div className={cn(
                                'w-7 h-7 rounded-md flex items-center justify-center shrink-0 border',
                                msg.role === 'assistant'
                                    ? 'bg-accent/10 border-accent/30 text-accent'
                                    : 'bg-raised border-line text-fg-3'
                            )} aria-hidden>
                                {msg.role === 'assistant' ? <Bot size={16} /> : <UserIcon size={16} />}
                            </div>
                            <div className={cn(
                                'rounded-md p-3 text-sm max-w-[85%] min-w-0 break-words [overflow-wrap:anywhere]',
                                msg.role === 'assistant'
                                    ? 'bg-panel text-fg border border-line'
                                    : 'bg-raised text-fg border border-line'
                            )}>
                                {msg.role === 'assistant' ? (
                                    <>
                                        <div className="copilot-markdown leading-relaxed">
                                            <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
                                        </div>
                                        {msg.action && <CopilotActionCard action={msg.action} caseId={caseId} messageId={msg.id} />}
                                        {(msg.suggestions ?? []).map((s, i) => (
                                            <CopilotSuggestionChip key={`${msg.id}-sugg-${i}`} action={s} caseId={caseId} messageId={msg.id} suggestionIndex={i} />
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
                            <div className="w-7 h-7 rounded-md bg-raised border border-line text-fg-3 flex items-center justify-center shrink-0" aria-hidden>
                                <UserIcon size={16} />
                            </div>
                            <div className="rounded-md p-3 text-sm max-w-[85%] min-w-0 break-words [overflow-wrap:anywhere] bg-raised text-fg border border-line">
                                <p className="leading-relaxed whitespace-pre-wrap">{chatMutation.variables}</p>
                            </div>
                        </motion.div>
                        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex gap-3">
                            <div className="w-7 h-7 rounded-md bg-accent/10 border border-accent/30 text-accent flex items-center justify-center shrink-0" aria-hidden>
                                <Bot size={16} />
                            </div>
                            <div role="status" className="bg-panel rounded-md p-3 text-sm text-fg-2 flex items-center gap-2 border border-line">
                                <Loader2 size={14} className="animate-spin text-accent" aria-hidden />
                                <span>Analyzing…</span>
                            </div>
                        </motion.div>
                    </>
                )}
                {chatMutation.isError && (
                    <p role="alert" className="text-xs text-severity-critical bg-severity-critical/5 border border-severity-critical/30 rounded-md p-3">
                        The copilot didn't answer in time. If a reply arrives it will show up here; otherwise try again.
                    </p>
                )}
                <div ref={messagesEndRef} />
            </div>

            {/* Input */}
            <div className="p-4 border-t border-line bg-panel shrink-0">
                <form onSubmit={handleSubmit} className="relative">
                    <textarea
                        ref={inputRef}
                        rows={1}
                        value={input}
                        onChange={(e) => setInput(e.target.value)}
                        onKeyDown={(e) => {
                            // Enter sends, Shift+Enter adds a line; never send mid-IME composition.
                            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); handleSubmit(); }
                        }}
                        placeholder={isCase ? 'Ask about this case…' : 'Ask about the queue or SOC topics…'}
                        disabled={isLoadingSession || !session}
                        aria-label="Message the copilot"
                        className="block w-full max-h-48 resize-none overflow-y-auto bg-panel border border-line-strong rounded-md py-2.5 pl-3 pr-10 text-sm focus:outline-2 focus:-outline-offset-1 focus:outline-accent text-fg placeholder:text-muted disabled:opacity-50"
                    />
                    <button
                        type="submit"
                        disabled={!input.trim() || chatMutation.isPending || !session}
                        aria-label="Send message"
                        className="absolute right-1.5 top-1.5 p-1.5 text-fg-2 hover:text-on-accent hover:bg-accent rounded-md disabled:opacity-50 disabled:hover:bg-transparent transition-colors"
                    >
                        <Send size={14} />
                    </button>
                </form>
            </div>
        </div>
    );
}
