# Time tracking

Each case has a **Time** tab that shows how long analysts worked on it. Time is captured automatically while an
analyst has the case open, and can be corrected or added by hand. Durations are derived from start and end
timestamps, never stored as a counter.

## How time is captured

- While a case is open in a **visible** browser tab, the app sends a heartbeat when the case opens and then every
  **60 seconds**. Hidden tabs send nothing.
- The server joins heartbeats into sessions. A heartbeat within **5 minutes** of the user's last automatic entry
  on that case extends it; a longer gap starts a new entry.
- Automatic entries are labelled **estimated from case activity**. They are an estimate of presence, not a
  timesheet.
- Only analysts, admins and the super admin send heartbeats. Viewers record nothing and do not see the Time tab.

## Manual corrections

Analysts can add time by hand ("Minutes" plus an optional note, ending now), and edit or delete entries:

- You can edit or delete **your own** entries, automatic or manual. Admins can edit or delete anyone's.
- Editing changes the duration (end time) and the note. The end must be after the start.
- Manual entries are labelled **manual**.
- Adding, editing and deleting entries is written to the audit log. Automatic heartbeats are not.

## Who sees what

| Who | Sees |
|---|---|
| Analyst and above | The Time tab of any case in the active organization: every entry, per-analyst totals and the case total (also shown in the case header) |
| Admin | The above, plus **Settings -> Time spent**: hours per analyst and case for a date range (default last 30 days), as a table or **CSV** download (`user_email, case_id, case_title, severity, seconds`) |
| Viewer, guest | Nothing |

The tenant rollup counts entries by their start date. The same data is available at `GET /api/v1/stats/time`
(`format=json|csv`, `start`, `end`; admin only).

## Privacy

The only signal is "this case is open in a visible tab". There is no keystroke, mouse, screen or content capture,
and nothing finer than the case-level session. Analysts can see and correct what was recorded about them, and
admins can too.
