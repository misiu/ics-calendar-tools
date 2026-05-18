# ICS Calendar Tools (Home Assistant) — v2.1.0

**ICS Calendar Tools** is a Home Assistant custom integration that lets you **add, update, and delete events** in **Local Calendar (.ics)** entities by **editing the underlying `.ics` file** and then triggering a Local Calendar refresh so changes appear without restarting Home Assistant.

This was built to work especially well with **Week Planner Card Plus** (Skylight-style family calendar dashboards), where you want reliable event editing and fast UI refresh.

---

## What this integration is (and is not)

✅ Works with **Local Calendar** entities (backed by a file like:
`/config/.storage/local_calendar.<name>.ics`)

This integration resolves the file path via the Local Calendar config entry `storage_key` (entity registry), so edits are always mapped to the correct `.ics` file.

❌ Does **not** directly create/edit recurring series on **Google calendar entities** (Google calendars are not `.ics` files on disk).
> Note: Your dashboard/card can still support Google recurring series via Home Assistant’s WebSocket calendar API — but that is separate from this integration’s file-based approach.

---

## Features

- ✅ **Add events** to a Local Calendar (`ics_calendar_tools.add_event`)
- ✅ **Update/edit events** (title/time/details) (`ics_calendar_tools.update_event`)
- ✅ **Delete events** reliably (UID-based) (`ics_calendar_tools.delete_event`)
- ✅ **List events** (including UID) for scripting (`ics_calendar_tools.list_events`)
- ✅ **RRULE repeat support** for Local Calendar events (write true recurring rules into the `.ics`)
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

### `ics_calendar_tools.add_event`
Add an event to a Local Calendar `.ics` file.

**Fields**
- `calendar` (required): Local Calendar entity id (ex: `calendar.family_calendar`)
- `summary` (required)
- `start` (required): `"YYYY-MM-DD"` for all-day, or ISO datetime for timed
- `end` (required): `"YYYY-MM-DD"` for all-day, or ISO datetime for timed
- `all_day` (required): `true/false`
- `description` (optional)
- `location` (optional)
- `rrule` (optional): e.g. `"FREQ=WEEKLY;INTERVAL=1;COUNT=5"`

> All-day note: Most calendar systems treat `DTEND` as **exclusive** for all-day events.
> For a one-day all-day event on `2026-02-08`, use end `2026-02-09`.

**Example (weekly all-day repeat, 5 occurrences)**
```yaml
action: ics_calendar_tools.add_event
data:
  calendar: calendar.family_calendar
  summary: Test Repeat Weekly
  all_day: true
  start: "2026-02-08"
  end: "2026-02-09"
  rrule: "FREQ=WEEKLY;INTERVAL=1;COUNT=5"
