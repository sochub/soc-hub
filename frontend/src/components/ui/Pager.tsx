import { ChevronLeft, ChevronRight } from 'lucide-react';

const btn = 'px-2 py-1 text-xs font-semibold rounded-sm bg-raised text-fg-2 hover:bg-raised-2 disabled:opacity-50 flex items-center gap-1';

/** Prev/Next for a zero-based page; renders nothing when everything fits on one page. */
export default function Pager({ page, pageSize, total, onPage }: {
    page: number; pageSize: number; total: number; onPage: (page: number) => void;
}) {
    if (total <= pageSize) return null;
    return (
        <div className="flex items-center justify-end gap-2 text-sm text-fg-3">
            <span>Page {page + 1} of {Math.ceil(total / pageSize)}</span>
            <button disabled={page === 0} onClick={() => onPage(page - 1)} className={btn}><ChevronLeft size={12} />Prev</button>
            <button disabled={(page + 1) * pageSize >= total} onClick={() => onPage(page + 1)} className={btn}>Next<ChevronRight size={12} /></button>
        </div>
    );
}
