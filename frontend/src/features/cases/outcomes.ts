// Mirrors backend CASE_OUTCOMES. Closing a case needs one of these plus a closing note.
export const CASE_OUTCOMES = [
    { value: 'unknown', label: 'Unknown' },
    { value: 'false_positive', label: 'False Positive' },
    { value: 'true_positive_with_impact', label: 'True Positive With Impact' },
    { value: 'true_positive_without_impact', label: 'True Positive Without Impact' },
    { value: 'legitimate', label: 'Legitimate' },
] as const;
