# ICS Calendar Tools (Home Assistant) — v2.0.0

**ICS Calendar Tools** is a Home Assistant custom integration that lets you **add, update, and delete events** in **Local Calendar (.ics)** entities by **editing the underlying `.ics` file** and then triggering a Local Calendar refresh so changes appear without restarting Home Assistant.

This was built to work especially well with **Week Planner Card Plus** (Skylight-style family calendar dashboards), where you want reliable event editing and fast UI refresh.

---

## What this integration is (and is not)

✅ Works with **Local Calendar** entities (backed by a file like:  
`/config/.storage/local_calendar.<name>.ics`)

❌ Does **not** directly create/edit recurring series on **Google calendar entities** (Google calendars are not `.ics` files on disk).  
> Note: Your dashboard/card can still support Google recurring series via Home Assistant’s WebSocket calendar API — but that is separate from this integration’s file-based approach.

---

## Features

- ✅ **Add events** to a Local Calendar (`ics_calendar_tools.add_event`)
- ✅ **Update/edit events** (title/time/details) (`ics_calendar_tools.update_event`)
- ✅ **Delete events** reliably (UID-based) (`ics_calendar_tools.delete_event`)
- ✅ **List events** (including UID) for scripting (`ics_calendar_tools.list_events`)
- ✅ **RRULE repeat support** for Local Calendar events (writes true recurring rules into the `.ics`)
- ✅ Automatically refreshes Local Calendar after changes (no manual restart)
- ✅ Supports multiple Local Calendar entities

---

## Requirements

- Home Assistant
- **Local Calendar** integration configured
- At least one Local Calendar entity (example: `calendar.family_calendar`)

---

## Installation (HACS)

1. Open **HACS** → **Integrations**
2. Click the **3 dots** (top right) → **Custom repositories**
3. Add this repository URL:  
   `https://github.com/randrcomputers/ics-calendar-tools`  
   and choose category **Integration**
4. Install **ICS Calendar Tools**
5. Restart Home Assistant
6. Go to **Settings → Devices & services → Add integration**
7. Add **ICS Calendar Tools**

After that, you should see services under:  
**Developer Tools → Actions / Services**

---

## Services

### Notes (important)

- `rrule` uses standard RFC5545 recurrence rules (example: `FREQ=WEEKLY;BYDAY=MO,WE`).
- For **all-day** events, most calendar systems treat `DTEND` as **exclusive**.  
  Example: a one-day all-day event on `2026-02-08` should use end `2026-02-09`.
- **Update/Delete require a UID**. The UID is the `UID:` value inside the `.ics` VEVENT. Your UI (Week Planner Card Plus) should pass the UID of the clicked event.

---

### `ics_calendar_tools.add_event`

Add an event to a Local Calendar `.ics` file.

**Fields**
- `calendar` (required): Local Calendar entity id (ex: `calendar.family_calendar`)
- `summary` (required)
- `start` (required): `"YYYY-MM-DD"` for all-day, or ISO datetime for timed (example: `"2026-02-08 09:00:00"`)
- `end` (required): `"YYYY-MM-DD"` for all-day, or ISO datetime for timed
- `all_day` (required): `true/false`
- `description` (optional)
- `location` (optional)
- `rrule` (optional): e.g. `"FREQ=WEEKLY;INTERVAL=1;COUNT=5"`

> All-day note: Most calendar systems treat `DTEND` as **exclusive** for all-day events.  
> For a one-day all-day event on `2026-02-08`, use end `2026-02-09`.

**Example (weekly all-day repeat, 5 occurrences)**
```yaml
service: ics_calendar_tools.add_event
data:
  calendar: calendar.family_calendar
  summary: Test Repeat Weekly
  all_day: true
  start: "2026-02-08"
  end: "2026-02-09"
  rrule: "FREQ=WEEKLY;INTERVAL=1;COUNT=5"
```

**Example (timed event, no repeat)**
```yaml
service: ics_calendar_tools.add_event
data:
  calendar: calendar.family_calendar
  summary: Dentist
  all_day: false
  start: "2026-02-08 14:30:00"
  end: "2026-02-08 15:30:00"
  description: "Bring insurance card"
  location: "Main St Dental"
```

---

### `ics_calendar_tools.update_event`

Update an existing event (by UID).

**Fields**
- `calendar` (required): Local Calendar entity id
- `uid` (required): UID of the VEVENT to update
- Any of the following (optional):  
  `summary`, `start`, `end`, `all_day`, `description`, `location`, `rrule`

**Example (change time + add/update RRULE)**
```yaml
service: ics_calendar_tools.update_event
data:
  calendar: calendar.family_calendar
  uid: "3eb61f28-8213-11f0-b1f8-0242ac110008"
  summary: Test Repeat Weekly (updated)
  all_day: true
  start: "2026-02-08"
  end: "2026-02-09"
  rrule: "FREQ=WEEKLY;INTERVAL=1"
```

**Example (remove repeat)**
```yaml
service: ics_calendar_tools.update_event
data:
  calendar: calendar.family_calendar
  uid: "3eb61f28-8213-11f0-b1f8-0242ac110008"
  rrule: ""
```

---

### `ics_calendar_tools.delete_event`

Delete an event (by UID).

**Fields**
- `calendar` (required): Local Calendar entity id
- `uid` (required): UID of the VEVENT to delete

**Example**
```yaml
service: ics_calendar_tools.delete_event
data:
  calendar: calendar.family_calendar
  uid: "3eb61f28-8213-11f0-b1f8-0242ac110008"
```

---

### `ics_calendar_tools.list_events`

List events from a Local Calendar `.ics` file and return their details (including `uid`).

**Fields**
- `calendar` (required): Local Calendar entity id
- `start` (optional): only include events that overlap this datetime/date
- `end` (optional): only include events that start before this datetime/date
- `limit` (optional): max number of events to return

**Response**
- `calendar`
- `count`
- `events[]` with `uid`, `summary`, `start`, `end`, `all_day`, `description`, `location`, `rrule`

**Example**
```yaml
service: ics_calendar_tools.list_events
target:
  entity_id: calendar.family_calendar
data:
  calendar: calendar.family_calendar
  start: "2026-01-01T00:00:00"
  end: "2026-12-31T23:59:59"
  limit: 200
response_variable: calendar_events
```

---

## Troubleshooting

- **I don’t see the services:**  
  Confirm the integration is installed, then restart Home Assistant. After restart, look under **Developer Tools → Actions / Services**.
- **Edits don’t appear immediately:**  
  This integration triggers a Local Calendar refresh after writing, but the UI may still cache. Try a browser refresh, or confirm the `.ics` file contents actually changed.
- **Wrong dates for all-day events:**  
  Remember `end` is exclusive for all-day events (one-day all-day requires end = next day).
