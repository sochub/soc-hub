import { ApiReferenceReact } from '@scalar/api-reference-react';
import '@scalar/api-reference-react/style.css';
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Check, Copy } from 'lucide-react';
import { Link } from 'react-router-dom';
import { api } from '../../api/client';
import PageContainer from '../../components/layout/PageContainer';
import { Button, Card, ErrorState, PageHeader, Skeleton } from '../../components/ui';

const ingestUrl = `${window.location.origin}/api/v1/alerts/webhook`;
const curlSample = `curl -X POST ${ingestUrl} \\
  -H "X-API-Key: <your webhook key>" \\
  -H "Content-Type: application/json" \\
  -d '{"external_id":"siem-123","title":"Suspicious login","payload":{"ip":"1.2.3.4"}}'`;

// Scalar on the app's own tokens (it follows the light/dark theme because the tokens do).
const vars = `
  --scalar-background-1: var(--color-panel) !important;
  --scalar-background-2: var(--color-surface) !important;
  --scalar-background-3: var(--color-raised-2) !important;
  --scalar-background-accent: color-mix(in srgb, var(--color-accent) 10%, transparent) !important;
  --scalar-color-1: var(--color-fg) !important;
  --scalar-color-2: var(--color-fg-2) !important;
  --scalar-color-3: var(--color-fg-3) !important;
  --scalar-color-accent: var(--color-accent) !important;
  --scalar-link-color: var(--color-accent) !important;
  --scalar-border-color: var(--color-line) !important;
  --scalar-button-1: var(--color-accent) !important;
  --scalar-button-1-color: var(--color-on-accent) !important;
  --scalar-button-1-hover: var(--color-accent-hover) !important;
  --scalar-color-red: var(--color-severity-critical) !important;
  --scalar-color-orange: var(--color-severity-high) !important;
  --scalar-color-yellow: var(--color-severity-medium) !important;
  --scalar-color-blue: var(--color-severity-low) !important;
  --scalar-color-green: var(--color-lime) !important;
  --scalar-color-purple: var(--color-magenta) !important;
  --scalar-sidebar-background-1: var(--color-surface) !important;
  --scalar-sidebar-color-1: var(--color-fg) !important;
  --scalar-sidebar-color-2: var(--color-fg-2) !important;
  --scalar-sidebar-border-color: var(--color-line) !important;
  --scalar-sidebar-item-active-background: var(--color-raised) !important;
  --scalar-sidebar-color-active: var(--color-accent) !important;
  --scalar-font: var(--font-sans) !important;
  --scalar-font-code: var(--font-mono) !important;`;
const scalarCss = `.light-mode, .dark-mode, .scalar-app {${vars}\n}`;

function isDarkTheme() {
    const t = document.documentElement.dataset.theme;
    if (t === 'dark') return true;
    if (t === 'light') return false;
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false;
}

function CopyButton({ text }: { text: string }) {
    const [copied, setCopied] = useState(false);
    const copy = async () => {
        try {
            await navigator.clipboard.writeText(text);
            setCopied(true);
            window.setTimeout(() => setCopied(false), 2000);
        } catch { /* clipboard unavailable (insecure context); the text stays selectable */ }
    };
    return (
        <>
            <Button variant="secondary" size="sm" onClick={copy} title="Copy curl command">
                {copied ? <Check size={13} aria-hidden /> : <Copy size={13} aria-hidden />}
                {copied ? 'Copied' : 'Copy'}<span className="sr-only"> curl command</span>
            </Button>
            <span role="status" className="sr-only">{copied ? 'Copied to clipboard' : ''}</span>
        </>
    );
}

export default function ApiDocs() {
    // The schema needs a signed-in session, so fetch it through the API client rather than letting Scalar load the URL.
    const schema = useQuery({ queryKey: ['openapi'], queryFn: () => api.get('/openapi.json').then(r => r.data), staleTime: Infinity });
    return (
        <PageContainer>
            <PageHeader title="API docs" description="Build integrations against the sochub API." className="mb-0" />

            <Card className="p-4 space-y-3">
                <h2 className="text-sm font-semibold text-fg">Webhook quickstart</h2>
                <p className="text-sm text-fg-2">
                    Push alerts from a SIEM or automation into an organization in three steps:
                </p>
                <ol className="text-sm text-fg-2 list-decimal list-inside space-y-1">
                    <li>
                        Create a webhook on the{' '}
                        <Link to="/settings/integrations" className="text-accent hover:underline">Integrations</Link>{' '}
                        page (admin only). Each webhook has its own <span className="font-mono">X-API-Key</span>;
                        the alert's <span className="font-mono">source</span> is set from the webhook's name.
                    </li>
                    <li>
                        Send a <span className="font-mono">POST</span> to{' '}
                        <span className="font-mono">/api/v1/alerts/webhook</span> with the{' '}
                        <span className="font-mono">X-API-Key</span> header. The key alone determines the
                        destination organization; there is no organization field in the request.
                    </li>
                    <li>
                        The new alert appears in the{' '}
                        <Link to="/alerts" className="text-accent hover:underline">Alerts</Link>{' '}
                        queue, where an analyst can promote it to a case or dismiss it.
                    </li>
                </ol>
                <div className="relative">
                    <pre className="text-xs font-mono text-fg-2 bg-surface border border-line rounded-sm p-3 pr-24 overflow-x-auto">{curlSample}</pre>
                    <div className="absolute top-2 right-2"><CopyButton text={curlSample} /></div>
                </div>
            </Card>

            <Card className="overflow-hidden">
                {schema.data
                    ? <ApiReferenceReact configuration={{ content: schema.data, theme: 'default', customCss: scalarCss, forceDarkModeState: isDarkTheme() ? 'dark' : 'light', hideDarkModeToggle: true,
                        // No hosted request proxy: "Send request" goes straight from the browser (the default proxy.scalar.com would
                        // receive the pasted bearer token / API key and the deployment hostname). No telemetry, no hosted fonts either.
                        proxyUrl: '', telemetry: false, withDefaultFonts: false,
                        externalUrls: { dashboardUrl: '', registryUrl: '', proxyUrl: '', apiBaseUrl: '' } }} />
                    : schema.isError
                        ? <ErrorState className="m-4" message="Could not load the API reference." onRetry={() => schema.refetch()} />
                        : <Skeleton rows={6} className="p-4" />}
            </Card>
        </PageContainer>
    );
}
