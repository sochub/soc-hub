/** Route-loading placeholder: shaped like a page so the shell never flashes empty. */
export default function PageSkeleton() {
    return (
        <div className="px-4 sm:px-6 py-6 max-w-[1400px] mx-auto space-y-4 animate-pulse" aria-busy="true" aria-label="Loading">
            <div className="h-7 w-48 bg-raised rounded-md" />
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                {[0, 1, 2].map(i => <div key={i} className="h-24 bg-panel border border-line rounded-lg" />)}
            </div>
            <div className="h-72 bg-panel border border-line rounded-lg" />
        </div>
    );
}
