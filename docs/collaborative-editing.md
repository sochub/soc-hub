# Collaborative editing

Three things keep analysts working on the same case from losing each other's work: inline images, an edit lock
on the report, and version checks on saves.

## Inline images

Paste or drag-drop a **PNG, JPEG, GIF or WebP** image into the markdown editor for case notes, Activity entries
or the report. It uploads at once, shows a placeholder, and is replaced by a markdown image link. Other file
types are rejected; the usual upload size limit applies. Pasting text that comes with an image keeps the text.

- Inline images are ordinary case attachments flagged as inline. They are **hidden from the Evidence/attachments
  list**, and do not create a file-hash artifact or an Activity entry.
- Uploads are audited like any attachment upload. Viewing an inline image is **not** audited (it would log every
  render of a note).
- Analysts and above can upload. Any signed-in member of the tenant with access to the case can view the image.
- **Guests** see an image only if the Activity note that contains it is currently shared with them (and, for
  guest-authored notes, belongs to them). Unsharing the note closes access to the image. Images in anything
  guests cannot see (description, report, unshared notes) are never served to them.

## Report edit lock

Opening the report for editing takes a soft lock so two analysts do not overwrite each other.

- The lock lasts **120 seconds** and the editor renews it every **45 seconds** while open. It is released when
  you leave; if the browser dies it expires on its own.
- If someone else holds it, you see "Being edited by <email>" and a **Take over** button. Taking over is allowed
  by any analyst and is written to the audit log (`report.lock_takeover`, with the previous holder).
- The lock is advisory: it does not block API saves. The version check below is the safety net.

## Version conflicts

Saving a report, note or Activity entry sends the version you started from (`base_updated_at`). If someone saved
since, the server answers **409** with `detail: "version_conflict"` and the current `updated_at`, and nothing is
overwritten. The UI shows "This report/note/event changed while you were editing - copy your changes, then
reload". To recover: copy your text, reload to get the latest version, and re-apply your changes.

API clients: echo the `updated_at` you last read as `base_updated_at` on `PUT /cases/{id}/report`, and on updates
of notes and events. Omitting the field skips the check (last write wins); versions compare to the second.
