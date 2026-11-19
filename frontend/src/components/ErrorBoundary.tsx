import { Component, type ReactNode } from 'react';
import { AlertTriangle } from 'lucide-react';
import Button from './ui/Button';

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
                    <div role="alert" className="text-center space-y-4 max-w-md">
                        <AlertTriangle size={40} className="mx-auto text-severity-critical" aria-hidden />
                        <h2 className="text-xl font-semibold text-fg">Something went wrong</h2>
                        <p className="text-fg-2 text-sm">
                            An unexpected error occurred. Refresh the page to continue.
                        </p>
                        {this.state.error?.message && (
                            <pre className="font-mono text-xs text-severity-critical bg-surface border border-line rounded-md p-4 text-left overflow-auto max-h-32 whitespace-pre-wrap">
                                {this.state.error.message}
                            </pre>
                        )}
                        <Button onClick={() => window.location.reload()}>Refresh page</Button>
                    </div>
                </div>
            );
        }

        return this.props.children;
    }
}
