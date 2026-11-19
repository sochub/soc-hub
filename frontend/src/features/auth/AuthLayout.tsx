import { cn } from '../../lib/utils';

/** Shared centered-card layout for the signed-out pages: sign in, invitations, OAuth consent, verification links. */
export default function AuthLayout({ title, description, children, footer, wide }: {
    title: React.ReactNode; description?: React.ReactNode; children?: React.ReactNode; footer?: React.ReactNode; wide?: boolean;
}) {
    return (
        <main className="min-h-screen flex items-center justify-center bg-canvas text-fg px-4 py-10">
            <div className={cn('w-full', wide ? 'max-w-md' : 'max-w-sm')}>
                <div className="flex items-center justify-center gap-2.5 mb-6">
                    <img src={`${import.meta.env.BASE_URL}logo.png`} alt="" className="w-9 h-9 p-1 rounded-md bg-rail" /* white mark on the website's black tile */ />
                    <span className="font-display text-lg font-semibold tracking-tight">sochub</span>
                </div>
                <div className="rounded-md border border-line bg-panel p-6 sm:p-8 space-y-6">
                    <div className="space-y-1.5">
                        <h1 className="text-2xl font-semibold text-fg text-balance">{title}</h1>
                        {description && <div className="text-sm text-fg-3">{description}</div>}
                    </div>
                    {children}
                </div>
                {footer && <div className="mt-4 text-center text-xs text-fg-3">{footer}</div>}
            </div>
        </main>
    );
}
