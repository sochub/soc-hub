export default function Integrations() {
    return (
        <div className="space-y-6">
            <div>
                <h1 className="text-3xl font-bold text-zinc-900 tracking-tight">Integrations</h1>
                <p className="text-zinc-500 mt-1">Manage external integrations and data sources</p>
            </div>

            <div className="glass-panel p-12 rounded-xl text-center border-dashed border-2 border-zinc-200">
                <div className="max-w-md mx-auto">
                    <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-zinc-100 flex items-center justify-center">
                        <svg className="w-8 h-8 text-zinc-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                        </svg>
                    </div>
                    <h3 className="text-lg font-medium text-zinc-700 mb-2">Integration Settings</h3>
                    <p className="text-zinc-400 text-sm">
                        Configure connections to Jira, Slack, and other security tools.
                    </p>
                    <p className="text-zinc-400 text-xs mt-4">Coming soon...</p>
                </div>
            </div>
        </div>
    );
}
