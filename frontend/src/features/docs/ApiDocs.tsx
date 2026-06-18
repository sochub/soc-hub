import { ApiReferenceReact } from '@scalar/api-reference-react';
import '@scalar/api-reference-react/style.css';
import { Link } from 'react-router-dom';

const ingestUrl = `${window.location.origin}/api/v1/alerts/webhook`;
const curlSample = `curl -X POST ${ingestUrl} \\
  -H "X-API-Key: <your webhook key>" \\
  -H "Content-Type: application/json" \\
  -d '{"external_id":"siem-123","title":"Suspicious login","payload":{"ip":"1.2.3.4"}}'`;

export default function ApiDocs() {
    return (
        <div className="space-y-6">
            <div>
                <h1 className="text-3xl font-bold text-zinc-900 tracking-tight">API Docs</h1>
                <p className="text-zinc-500 mt-1">Build integrations against the SOC Hub API.</p>
            </div>

            <div className="glass-panel p-5 rounded-xl border border-zinc-200 space-y-3">
                <h2 className="font-semibold text-zinc-800">Webhook quickstart</h2>
                <p className="text-sm text-zinc-600">
                    Push alerts from a SIEM or automation into a tenant in three steps:
                </p>
                <ol className="text-sm text-zinc-600 list-decimal list-inside space-y-1">
                    <li>
                        Create a webhook on the{' '}
                        <Link to="/integrations" className="text-accent-600 hover:underline">Integrations</Link>{' '}
                        page (admin only). Each webhook has its own <span className="font-mono">X-API-Key</span>;
                        the alert's <span className="font-mono">source</span> is set from the webhook's name.
                    </li>
                    <li>
                        Send a <span className="font-mono">POST</span> to{' '}
                        <span className="font-mono">/api/v1/alerts/webhook</span> with the{' '}
                        <span className="font-mono">X-API-Key</span> header. The key alone determines the
                        destination tenant — there is no tenant field in the request.
                    </li>
                    <li>
                        The new alert appears in the{' '}
                        <Link to="/alerts" className="text-accent-600 hover:underline">Alerts</Link>{' '}
                        queue, where an analyst can promote it to a case or dismiss it.
                    </li>
                </ol>
                <pre className="text-xs text-zinc-600 bg-zinc-50 rounded-lg p-3 overflow-x-auto">{curlSample}</pre>
            </div>

            <div className="glass-panel rounded-xl border border-zinc-200 overflow-hidden">
                <ApiReferenceReact configuration={{ url: '/api/v1/openapi.json', theme: 'default' }} />
            </div>
        </div>
    );
}
