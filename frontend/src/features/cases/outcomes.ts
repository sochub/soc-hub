// Mirrors backend CASE_OUTCOMES. Closing a case needs one of these plus a closing note.
export const CASE_OUTCOMES = [
    { value: 'unknown', label: 'Unknown' },
    { value: 'false_positive', label: 'False positive' },
    { value: 'true_positive_with_impact', label: 'True positive with impact' },
    { value: 'true_positive_without_impact', label: 'True positive without impact' },
    { value: 'legitimate', label: 'Legitimate' },
] as const;

export const outcomeLabel = (v: string) => CASE_OUTCOMES.find((o) => o.value === v)?.label ?? v.replace(/_/g, ' ');
