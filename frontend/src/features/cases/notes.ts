// Typed by people; everything else in the activity feed (status_change, task, artifact_added…) is written by the system.
export const NOTE_TYPES = ['comment', 'investigation', 'containment', 'remediation', 'other'];
export const isNote = (t: string) => NOTE_TYPES.includes(t) || t === 'guest_comment';
