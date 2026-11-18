// Mirrors backend CaseStatus (app/models/case.py); backend/tests/test_case_status_parity.py keeps them in sync.
export const CASE_STATUSES = [
    { value: 'new', label: 'New' },
    { value: 'open', label: 'Open' },
    { value: 'in_progress', label: 'In progress' },
    { value: 'pending', label: 'Pending' },
    { value: 'resolved', label: 'Resolved' },
    { value: 'closed', label: 'Closed' },
] as const;

export type CaseStatusValue = typeof CASE_STATUSES[number]['value'];

export const statusLabel = (s: string | null | undefined) =>
    CASE_STATUSES.find((x) => x.value === s)?.label ?? (s ?? '').replace(/_/g, ' ');
