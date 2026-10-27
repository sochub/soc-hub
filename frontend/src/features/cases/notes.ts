// Typed by people; everything else in the activity feed (status_change, task, artifact_added…) is written by the system.
export const NOTE_TYPES = ['comment', 'investigation', 'containment', 'remediation', 'other'];
// guest_comment and ticket_comment are notes from outside (a case guest, the linked ticket): shown in full too.
export const isNote = (t: string) => NOTE_TYPES.includes(t) || t === 'guest_comment' || t === 'ticket_comment';
