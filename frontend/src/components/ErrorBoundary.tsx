import { Component, type ReactNode } from 'react';
import { AlertTriangle } from 'lucide-react';

interface Props {
    children: ReactNode;
}

interface State {
    hasError: boolean;
    error: Error | null;
}

export default class ErrorBoundary extends Component<Props, State> {
    constructor(props: Props) {
        super(props);
        this.state = { hasError: false, error: null };
    }

    static getDerivedStateFromError(error: Error): State {
        return { hasError: true, error };
    }

    render() {
        if (this.state.hasError) {
            return (
                <div className="flex items-center justify-center min-h-[50vh] p-8">
                    <div className="text-center space-y-4 max-w-md">
                        <AlertTriangle size={48} className="mx-auto text-red-400" />
                        <h2 className="text-xl font-bold text-slate-100">Something went wrong</h2>
                        <p className="text-slate-400 text-sm">
                            An unexpected error occurred. Please refresh the page to continue.
                        </p>
                        <pre className="text-xs text-red-400/80 bg-slate-900 rounded-lg p-4 text-left overflow-auto max-h-32">
                            {this.state.error?.message}
                        </pre>
                        <button
                            onClick={() => window.location.reload()}
                            className="bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded-lg font-medium transition-colors"
                        >
                            Refresh Page
                        </button>
                    </div>
                </div>
            );
        }

        return this.props.children;
    }
}
