from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from datetime import date, datetime, timedelta
from typing import Any, Mapping

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

LOCAL_CALENDAR_STORAGE_PATH = "/config/.storage"
LOCAL_CALENDAR_PREFIX = "local_calendar."
ICS_EXTENSION = ".ics"
DATE_OR_DATETIME_OR_STRING = vol.Any(datetime, date, cv.string)
UID_DATA_KEYS = (
    "uid",
    "UID",
    "id",
    "event_id",
    "eventId",
    "event_uid",
    "eventUid",
    "ical_uid",
    "icalUid",
)


def _non_empty_string(value: Any) -> str:
    """Validate string input and reject whitespace-only values."""
    text = cv.string(value).strip()
    if not text:
        raise vol.Invalid("Value cannot be empty.")
    return text


LIST_EVENTS_SCHEMA = vol.Schema(
    {
        vol.Required("calendar"): cv.entity_id,
        vol.Optional("start"): DATE_OR_DATETIME_OR_STRING,
        vol.Optional("end"): DATE_OR_DATETIME_OR_STRING,
        vol.Optional("limit"): vol.All(vol.Coerce(int), vol.Range(min=1, max=1000)),
    }
)

ADD_EVENT_SCHEMA = vol.Schema(
    {
        vol.Required("calendar"): cv.entity_id,
        vol.Required("summary"): _non_empty_string,
        vol.Required("all_day"): cv.boolean,
        vol.Required("start"): DATE_OR_DATETIME_OR_STRING,
        vol.Required("end"): DATE_OR_DATETIME_OR_STRING,
        vol.Optional("description"): cv.string,
        vol.Optional("location"): cv.string,
        vol.Optional("rrule"): cv.string,
    }
)

DELETE_EVENT_SCHEMA = vol.Schema(
    {
        vol.Required("calendar"): cv.entity_id,
        vol.Optional("uid"): cv.string,
        vol.Optional("UID"): cv.string,
        vol.Optional("id"): cv.string,
        vol.Optional("event_id"): cv.string,
        vol.Optional("eventId"): cv.string,
        vol.Optional("event_uid"): cv.string,
        vol.Optional("eventUid"): cv.string,
        vol.Optional("ical_uid"): cv.string,
        vol.Optional("icalUid"): cv.string,
        vol.Optional("summary"): _non_empty_string,
        vol.Optional("start"): DATE_OR_DATETIME_OR_STRING,
        vol.Optional("end"): DATE_OR_DATETIME_OR_STRING,
    }
)

UPDATE_EVENT_SCHEMA = vol.Schema(
    {
        vol.Required("calendar"): cv.entity_id,
        vol.Optional("uid"): cv.string,
        vol.Optional("UID"): cv.string,
        vol.Optional("id"): cv.string,
        vol.Optional("event_id"): cv.string,
        vol.Optional("eventId"): cv.string,
        vol.Optional("event_uid"): cv.string,
        vol.Optional("eventUid"): cv.string,
        vol.Optional("ical_uid"): cv.string,
        vol.Optional("icalUid"): cv.string,
        vol.Optional("summary"): cv.string,
        vol.Optional("start"): DATE_OR_DATETIME_OR_STRING,
        vol.Optional("end"): DATE_OR_DATETIME_OR_STRING,
        vol.Optional("location"): cv.string,
        vol.Optional("description"): cv.string,
        vol.Optional("rrule"): cv.string,
    }
)

IMPORT_EVENTS_SCHEMA = vol.Schema(
    {
        vol.Required("calendar"): cv.entity_id,
        vol.Required("ics"): vol.All(cv.string, vol.Length(min=1)),
        vol.Optional("clear_before_import", default=False): cv.boolean,
    }
)


def _local_calendar_ics_path(slug: str) -> str:
    return f"{LOCAL_CALENDAR_STORAGE_PATH}/{LOCAL_CALENDAR_PREFIX}{slug}{ICS_EXTENSION}"


def _to_dt(value: str) -> datetime:
    dt = dt_util.parse_datetime(value)
    if dt is None:
        parsed_date = dt_util.parse_date(value)
        if parsed_date is not None:
            dt = dt_util.start_of_local_day(parsed_date)
        else:
            raise ServiceValidationError(f"Invalid datetime: {value}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    return dt


def _coerce_dt(value: Any) -> datetime:
    """Accept a datetime object (from a datetime selector) or a string and return an aware local datetime."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
        return dt_util.as_local(value)
    if isinstance(value, date):
        return dt_util.start_of_local_day(value)
    return _to_dt(str(value).strip().replace(" ", "T"))


def _coerce_date(value: Any) -> date:
    """Accept a date/datetime object or string and return a date (for all-day events)."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    d = dt_util.parse_date(s)
    if d:
        return d
    return _to_dt(s.replace(" ", "T")).date()


def _find_ics_path_for_calendar(hass: HomeAssistant, calendar_entity_id: str) -> str:
    """Resolve Local Calendar .ics path using the entity's storage_key."""
    ent_reg = er.async_get(hass)
    entity = ent_reg.async_get(calendar_entity_id)

    if not entity:
        raise ServiceValidationError(f"Entity not found in registry: {calendar_entity_id}")

    if entity.platform != "local_calendar":
        raise ServiceValidationError(f"{calendar_entity_id} is not a Local Calendar entity")

    if not entity.config_entry_id:
        raise ServiceValidationError(f"{calendar_entity_id} has no config_entry_id")

    cfg = hass.config_entries.async_get_entry(entity.config_entry_id)
    if not cfg or cfg.domain != "local_calendar":
        raise ServiceValidationError(f"{calendar_entity_id} is not backed by local_calendar config entry")

    storage_key = cfg.data.get("storage_key")
    if not storage_key:
        raise ServiceValidationError(f"Local Calendar config entry has no storage_key: {cfg.entry_id}")

    path = _local_calendar_ics_path(str(storage_key))
    if not os.path.exists(path):
        raise ServiceValidationError(f"Local Calendar .ics file not found: {path}")

    return path


def _load_icalendar(path: str):
    from icalendar import Calendar

    with open(path, "rb") as f:
        data = f.read()
    return Calendar.from_ical(data)


def _load_import_icalendar(raw_ics: str):
    from icalendar import Calendar

    ics_text = raw_ics.strip()
    if not ics_text:
        raise ServiceValidationError("ics is required")

    try:
        cal = Calendar.from_ical(ics_text)
    except Exception as err:
        raise ServiceValidationError("Invalid ICS content.") from err

    if getattr(cal, "name", None) != "VCALENDAR":
        raise ServiceValidationError("ICS content must contain a VCALENDAR.")

    imported_event_uids: set[str] = set()
    imported_events = []
    imported_timezones = []

    for component in cal.subcomponents:
        if component.name == "VTIMEZONE":
            tzid = str(component.get("TZID", "")).strip()
            if not tzid:
                raise ServiceValidationError("Imported VTIMEZONE is missing TZID.")
            imported_timezones.append(component)
            continue

        if component.name != "VEVENT":
            continue

        uid = str(component.get("UID", "")).strip()
        if not uid:
            raise ServiceValidationError("Each imported event must have a UID.")
        if uid in imported_event_uids:
            raise ServiceValidationError(f"Duplicate UID in imported ICS content: {uid}")

        dtstart = component.get("DTSTART")
        if dtstart is None:
            raise ServiceValidationError(f"Imported event {uid} is missing DTSTART.")

        start_dt = _dt_from_ical(dtstart)
        if start_dt is None:
            raise ServiceValidationError(f"Imported event {uid} has an invalid DTSTART.")

        dtend = component.get("DTEND")
        if dtend is not None:
            start_raw = getattr(dtstart, "dt", dtstart)
            end_raw = getattr(dtend, "dt", dtend)
            if isinstance(start_raw, datetime) != isinstance(end_raw, datetime):
                raise ServiceValidationError(f"Imported event {uid} must use matching DTSTART/DTEND value types.")

        end_dt = _event_end_dt(component)
        if end_dt is not None and end_dt <= start_dt:
            raise ServiceValidationError(f"Imported event {uid} must end after it starts.")

        imported_event_uids.add(uid)
        imported_events.append(component)

    if not imported_events:
        raise ServiceValidationError("ICS content must contain at least one VEVENT.")

    return imported_timezones, imported_events, imported_event_uids


def _write_icalendar_atomic(path: str, cal) -> None:
    """Write ICS safely: backup, atomic replace, best-effort fsync."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = f"{path}.bak_{ts}"
    try:
        shutil.copy2(path, backup)
    except Exception:
        # If file didn't exist yet, ignore
        pass

    directory = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(prefix="ics_", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as f:
            data = cal.to_ical()
            # Some parsers are happier with a trailing newline
            if data and not data.endswith(b"\n"):
                data += b"\n"
            f.write(data)
            try:
                f.flush()
                os.fsync(f.fileno())
            except Exception:
                pass

        os.replace(tmp_path, path)

        # Best-effort flush of directory entry
        try:
            dir_fd = os.open(directory, os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except Exception:
            pass

    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


def _normalize_summary(s: str | None) -> str | None:
    if s is None:
        return None
    s2 = " ".join(str(s).strip().split())
    return s2.lower()


def _uid_from_call_data(data: Mapping[str, Any]) -> str | None:
    """Accept UID from several common keys used by cards/integrations."""
    for k in UID_DATA_KEYS:
        v = data.get(k)
        if v:
            return str(v).strip()
    return None


def _dt_from_ical(value) -> datetime | None:
    """Convert DTSTART/DTEND's .dt (date or datetime) to datetime (local)."""
    if value is None:
        return None
    v = getattr(value, "dt", value)
    if isinstance(v, datetime):
        return dt_util.as_local(v)
    # date -> treat as local start of day
    try:
        return dt_util.as_local(dt_util.start_of_local_day(v))
    except Exception:
        return None


def _event_end_dt(component) -> datetime | None:
    """Return event end datetime, handling DTEND or DURATION."""
    ev_end = component.get("DTEND")
    if ev_end:
        return _dt_from_ical(ev_end)
    dur = component.get("DURATION")
    if dur:
        try:
            start = _dt_from_ical(component.get("DTSTART"))
            if start is None:
                return None
            # icalendar stores DURATION as datetime.timedelta typically
            if isinstance(dur.dt, timedelta):
                return start + dur.dt
        except Exception:
            return None
    return None


def _iso_ical_value(value) -> str | None:
    """Convert iCalendar date/datetime values to ISO strings."""
    if value is None:
        return None
    v = getattr(value, "dt", value)
    if isinstance(v, datetime):
        return dt_util.as_local(v).isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def _match_event(component, uid: str | None, summary: str | None, start: datetime | None, end: datetime | None) -> bool:
    if component.name != "VEVENT":
        return False

    # UID match (preferred)
    if uid:
        ev_uid = str(component.get("UID", "")).strip()

        # Most of the time UID is exact match.
        if ev_uid == uid:
            return True

        # Some UIs pass composite IDs; be tolerant only when it looks safe.
        if len(uid) >= 12 and (uid in ev_uid or ev_uid in uid):
            return True

        return False

    # Fallback matching by summary/start/end (best-effort)
    if summary is not None:
        if _normalize_summary(str(component.get("SUMMARY", ""))) != _normalize_summary(summary):
            return False

    if start is not None:
        ev_start_dt = _dt_from_ical(component.get("DTSTART"))
        if ev_start_dt is None:
            return False
        if abs(dt_util.as_local(ev_start_dt) - dt_util.as_local(start)) > timedelta(minutes=1):
            return False

    if end is not None:
        ev_end_dt = _event_end_dt(component)
        if ev_end_dt is None:
            return False
        if abs(dt_util.as_local(ev_end_dt) - dt_util.as_local(end)) > timedelta(minutes=1):
            return False

    return True


def _all_local_calendar_ics_paths() -> list[str]:
    base = LOCAL_CALENDAR_STORAGE_PATH
    out: list[str] = []
    try:
        for name in os.listdir(base):
            if name.startswith(LOCAL_CALENDAR_PREFIX) and name.endswith(ICS_EXTENSION):
                out.append(os.path.join(base, name))
    except Exception:
        pass
    return out


def _ics_paths_containing_uid(uid: str) -> list[str]:
    """Fast-ish scan to find which local_calendar.*.ics contains a UID."""
    uid_s = str(uid).strip()
    needle1 = f"UID:{uid_s}".encode("utf-8")
    needle2 = b"UID;"
    uid_b = uid_s.encode("utf-8")

    matches: list[str] = []
    for path in _all_local_calendar_ics_paths():
        try:
            with open(path, "rb") as f:
                data = f.read()
            if needle1 in data or (needle2 in data and uid_b in data):
                matches.append(path)
        except Exception:
            continue
    return matches


def _component_tzid(component) -> str | None:
    tzid = component.get("TZID")
    if not tzid:
        return None
    tzid_str = str(tzid).strip()
    return tzid_str or None


async def _wait_for_mtime_change(path: str, before: float | None, timeout_s: float = 2.0) -> None:
    if before is None:
        return
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        try:
            now = os.path.getmtime(path)
            if now != before:
                return
        except Exception:
            return
        await asyncio.sleep(0.1)


async def _reload_local_calendar_entries(hass: HomeAssistant) -> None:
    """Reload Local Calendar config entries so it re-reads .ics files."""
    entries = hass.config_entries.async_entries("local_calendar")
    if not entries:
        _LOGGER.debug("ICS_CALENDAR_TOOLS: no local_calendar config entries found to reload")
        return

    _LOGGER.debug("ICS_CALENDAR_TOOLS: reloading %d local_calendar config entries", len(entries))
    for entry in entries:
        try:
            await hass.config_entries.async_reload(entry.entry_id)
        except Exception as e:
            _LOGGER.warning("ICS_CALENDAR_TOOLS: failed to reload local_calendar entry %s: %s", entry.entry_id, e)


async def _force_refresh_after_edit(hass: HomeAssistant, cal_ent: str, ics_path: str, before_mtime: float | None) -> None:
    # Ensure filesystem mtime has updated so Local Calendar reload reads fresh content
    await _wait_for_mtime_change(ics_path, before_mtime, timeout_s=2.0)

    # Reload Local Calendar entries (preferred; avoids relying on a user script)
    await _reload_local_calendar_entries(hass)

    # Nudge the specific calendar entity the UI is showing (best-effort)
    try:
        await hass.services.async_call(
            "homeassistant",
            "update_entity",
            {"entity_id": cal_ent},
            blocking=True,
        )
    except Exception:
        pass


def _register_services(hass: HomeAssistant) -> None:
    """Register services once per HA runtime."""
    data = hass.data.setdefault(DOMAIN, {})
    if data.get("_services_registered"):
        return
    data["_services_registered"] = True


    async def handle_add(call: ServiceCall) -> None:
        from icalendar import Event, vRecur

        cal_ent = call.data["calendar"]

        summary = (call.data.get("summary") or "").strip()
        if not summary:
            raise ServiceValidationError("summary is required")

        desc = call.data.get("description")
        loc = call.data.get("location")
        all_day = bool(call.data.get("all_day", False))
        start_val = call.data.get("start")
        end_val = call.data.get("end")
        rrule_raw = (call.data.get("rrule") or "").strip()

        path = _find_ics_path_for_calendar(hass, cal_ent)

        # load calendar
        try:
            before_mtime = os.path.getmtime(path)
        except Exception:
            before_mtime = None

        cal = await hass.async_add_executor_job(_load_icalendar, path)

        ev = Event()
        # Unique UID (stable enough for local .ics)
        uid = f"{dt_util.utcnow().strftime('%Y%m%dT%H%M%SZ')}-{os.urandom(4).hex()}@homeassistant"
        ev.add("uid", uid)
        ev.add("summary", summary)

        if desc:
            ev.add("description", str(desc))
        if loc:
            ev.add("location", str(loc))

        if all_day:
            sdt = _coerce_date(start_val)
            edt = _coerce_date(end_val)
            ev.add("dtstart", sdt)
            if edt < sdt:
                raise ServiceValidationError("end must be on or after start for all-day events.")
            # If UI gives an inclusive end date equal to start, convert to exclusive end (+1 day).
            if edt == sdt:
                edt = sdt + timedelta(days=1)
            ev.add("dtend", edt)
        else:
            sdt = _coerce_dt(start_val)
            edt = _coerce_dt(end_val)
            if edt <= sdt:
                raise ServiceValidationError("end must be after start for non all-day events.")
            ev.add("dtstart", sdt)
            ev.add("dtend", edt)

        if rrule_raw:
            # Accept either "RRULE:FREQ=..." or just "FREQ=..."
            if rrule_raw.upper().startswith("RRULE:"):
                rrule_raw = rrule_raw.split(":", 1)[1].strip()
            try:
                ev.add("rrule", vRecur.from_ical(rrule_raw))
            except Exception as e:
                raise ServiceValidationError(f"Invalid RRULE: {rrule_raw}") from e

        cal.add_component(ev)

        await hass.async_add_executor_job(_write_icalendar_atomic, path, cal)
        await _force_refresh_after_edit(hass, cal_ent, path, before_mtime)

    async def handle_delete(call: ServiceCall) -> None:
        cal_ent = call.data["calendar"]
        _LOGGER.debug("ICS_CALENDAR_TOOLS delete_event call data=%s", dict(call.data))

        uid = _uid_from_call_data(call.data)
        summary = call.data.get("summary")
        start_val = call.data.get("start")
        end_val = call.data.get("end")
        if uid is None and summary is None and start_val is None and end_val is None:
            raise ServiceValidationError("Delete requires uid, or at least one fallback matcher: summary/start/end.")

        start = _coerce_dt(start_val) if start_val is not None else None
        end = _coerce_dt(end_val) if end_val is not None else None

        path = _find_ics_path_for_calendar(hass, cal_ent)
        before_mtime = None
        try:
            before_mtime = os.path.getmtime(path)
        except Exception:
            pass

        cal = await hass.async_add_executor_job(_load_icalendar, path)

        removed = 0
        kept = []
        for comp in cal.subcomponents:
            if _match_event(comp, uid, summary, start, end):
                removed += 1
            else:
                kept.append(comp)

        if removed == 0:
            if uid:
                paths = _ics_paths_containing_uid(str(uid).strip())
                if not paths:
                    raise ServiceValidationError(
                        "No matching event found to delete (UID not found in any local calendar)."
                    )

                deleted_any = False
                for p in paths:
                    cal2 = await hass.async_add_executor_job(_load_icalendar, p)

                    removed2 = 0
                    kept2 = []
                    for comp2 in cal2.subcomponents:
                        if _match_event(comp2, str(uid).strip(), None, None, None):
                            removed2 += 1
                        else:
                            kept2.append(comp2)

                    if removed2:
                        from icalendar import Calendar

                        new_cal2 = Calendar()
                        for k, v in cal2.items():
                            new_cal2.add(k, v)
                        for comp2 in kept2:
                            new_cal2.add_component(comp2)

                        await hass.async_add_executor_job(_write_icalendar_atomic, p, new_cal2)
                        deleted_any = True

                if not deleted_any:
                    raise ServiceValidationError(
                        "No matching event found to delete (UID search hit files but VEVENT not removed)."
                    )

                await _force_refresh_after_edit(hass, cal_ent, path, before_mtime)
                return

            raise ServiceValidationError("No matching event found to delete.")

        if removed > 1 and not uid:
            raise ServiceValidationError("Multiple matches found; provide uid to delete precisely.")

        from icalendar import Calendar

        new_cal = Calendar()
        for k, v in cal.items():
            new_cal.add(k, v)
        for comp in kept:
            new_cal.add_component(comp)

        await hass.async_add_executor_job(_write_icalendar_atomic, path, new_cal)
        await _force_refresh_after_edit(hass, cal_ent, path, before_mtime)

    async def handle_update(call: ServiceCall) -> None:
        cal_ent = call.data["calendar"]
        _LOGGER.debug("ICS_CALENDAR_TOOLS update_event call data=%s", dict(call.data))

        uid = _uid_from_call_data(call.data)
        new_summary = call.data.get("summary")
        new_start_val = call.data.get("start")
        new_end_val = call.data.get("end")
        new_loc = call.data.get("location")
        new_desc = call.data.get("description")
        rrule_raw = (call.data.get("rrule") or "").strip()

        new_start = _coerce_dt(new_start_val) if new_start_val is not None else None
        new_end = _coerce_dt(new_end_val) if new_end_val is not None else None

        if not uid:
            raise ServiceValidationError("Update requires uid/id/event_id (a stable identifier).")

        path = _find_ics_path_for_calendar(hass, cal_ent)
        before_mtime = None
        try:
            before_mtime = os.path.getmtime(path)
        except Exception:
            pass

        cal = await hass.async_add_executor_job(_load_icalendar, path)

        updated = 0
        for comp in cal.subcomponents:
            if comp.name != "VEVENT":
                continue
            ev_uid = str(comp.get("UID", "")).strip()
            if ev_uid != str(uid).strip():
                continue

            if new_summary is not None:
                comp["SUMMARY"] = new_summary
            if new_start is not None and comp.get("DTSTART") is not None:
                comp["DTSTART"].dt = new_start
            if new_end is not None and comp.get("DTEND") is not None:
                comp["DTEND"].dt = new_end
            # If event uses DURATION and caller gives end, convert to DTEND
            if new_end is not None and comp.get("DTEND") is None and comp.get("DTSTART") is not None:
                try:
                    comp["DTEND"] = new_end
                    if comp.get("DURATION") is not None:
                        del comp["DURATION"]
                except Exception:
                    pass
            if new_loc is not None:
                comp["LOCATION"] = new_loc
            if new_desc is not None:
                comp["DESCRIPTION"] = new_desc
            # RRULE update (optional). If blank string is passed, clear RRULE.
            if rrule_raw:
                from icalendar import vRecur
                rr = rrule_raw
                if rr.upper().startswith("RRULE:"):
                    rr = rr.split(":", 1)[1].strip()
                try:
                    comp["RRULE"] = vRecur.from_ical(rr)
                except Exception as e:
                    raise ServiceValidationError(f"Invalid RRULE: {rr}") from e
            elif "rrule" in call.data:
                # Explicitly provided but empty -> remove RRULE
                try:
                    if comp.get("RRULE") is not None:
                        del comp["RRULE"]
                except Exception:
                    pass

            updated += 1

            updated_start = _dt_from_ical(comp.get("DTSTART"))
            updated_end = _event_end_dt(comp)
            if updated_start is not None and updated_end is not None and updated_end <= updated_start:
                raise ServiceValidationError("Updated event end must be after start.")

        if updated == 0:
            raise ServiceValidationError("No matching UID found to update.")

        await hass.async_add_executor_job(_write_icalendar_atomic, path, cal)
        await _force_refresh_after_edit(hass, cal_ent, path, before_mtime)

    async def handle_list(call: ServiceCall) -> dict[str, Any]:
        cal_ent = call.data["calendar"]

        start_val = call.data.get("start")
        end_val = call.data.get("end")
        limit: int = call.data.get("limit") or 0

        start_filter = _coerce_dt(start_val) if start_val is not None else None
        end_filter = _coerce_dt(end_val) if end_val is not None else None

        path = _find_ics_path_for_calendar(hass, cal_ent)
        cal = await hass.async_add_executor_job(_load_icalendar, path)

        events: list[dict[str, Any]] = []
        for comp in cal.subcomponents:
            if comp.name != "VEVENT":
                continue

            start_dt = _dt_from_ical(comp.get("DTSTART"))
            end_dt = _event_end_dt(comp)

            if start_filter and end_dt and dt_util.as_local(end_dt) < dt_util.as_local(start_filter):
                continue
            if start_filter and not end_dt and start_dt and dt_util.as_local(start_dt) < dt_util.as_local(start_filter):
                continue
            if end_filter and start_dt and dt_util.as_local(start_dt) > dt_util.as_local(end_filter):
                continue

            start_raw = getattr(comp.get("DTSTART"), "dt", None)
            item: dict[str, Any] = {
                "uid": str(comp.get("UID", "")).strip(),
                "summary": str(comp.get("SUMMARY", "")),
                "start": _iso_ical_value(comp.get("DTSTART")),
                "end": _iso_ical_value(comp.get("DTEND")) or (end_dt.isoformat() if end_dt else None),
                "all_day": isinstance(start_raw, date) and not isinstance(start_raw, datetime),
                "description": str(comp.get("DESCRIPTION", "")),
                "location": str(comp.get("LOCATION", "")),
                "rrule": str(comp.get("RRULE", "")),
            }
            events.append(item)

        events.sort(key=lambda ev: ev.get("start") or "")
        if limit > 0:
            events = events[:limit]

        return {"calendar": cal_ent, "count": len(events), "events": events}

    async def handle_import(call: ServiceCall) -> None:
        cal_ent = call.data["calendar"]
        clear_before_import = bool(call.data.get("clear_before_import", False))
        raw_ics = call.data["ics"]

        path = _find_ics_path_for_calendar(hass, cal_ent)
        before_mtime = None
        try:
            before_mtime = os.path.getmtime(path)
        except Exception:
            pass

        imported_timezones, imported_events, imported_event_uids = await hass.async_add_executor_job(
            _load_import_icalendar, raw_ics
        )
        cal = await hass.async_add_executor_job(_load_icalendar, path)

        from icalendar import Calendar

        new_cal = Calendar()
        for key, value in cal.items():
            new_cal.add(key, value)

        existing_event_uids: set[str] = set()
        existing_timezones: set[str] = set()

        for component in cal.subcomponents:
            if component.name == "VEVENT":
                existing_uid = str(component.get("UID", "")).strip()
                if existing_uid:
                    existing_event_uids.add(existing_uid)
                if clear_before_import:
                    continue
            elif component.name == "VTIMEZONE":
                tzid = _component_tzid(component)
                if tzid:
                    existing_timezones.add(tzid)

            new_cal.add_component(component)

        duplicate_uids = sorted(imported_event_uids & existing_event_uids) if not clear_before_import else []
        if duplicate_uids:
            raise ServiceValidationError(
                "Imported ICS content contains UID values that already exist in the selected calendar: "
                + ", ".join(duplicate_uids[:5])
                + ("..." if len(duplicate_uids) > 5 else "")
            )

        for component in imported_timezones:
            tzid = _component_tzid(component)
            if tzid and tzid in existing_timezones:
                continue
            new_cal.add_component(component)
            if tzid:
                existing_timezones.add(tzid)

        for component in imported_events:
            new_cal.add_component(component)

        await hass.async_add_executor_job(_write_icalendar_atomic, path, new_cal)
        await _force_refresh_after_edit(hass, cal_ent, path, before_mtime)

    hass.services.async_register(
        DOMAIN,
        "list_events",
        handle_list,
        schema=LIST_EVENTS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "add_event",
        handle_add,
        schema=ADD_EVENT_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        "update_event",
        handle_update,
        schema=UPDATE_EVENT_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        "delete_event",
        handle_delete,
        schema=DELETE_EVENT_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        "import_events",
        handle_import,
        schema=IMPORT_EVENTS_SCHEMA,
    )


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    # No YAML required. Services will be registered when the config entry is created.
    return True


async def async_setup_entry(hass: HomeAssistant, entry) -> bool:
    _LOGGER.debug("ICS_CALENDAR_TOOLS: setup entry %s", entry.entry_id)
    _register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry) -> bool:
    # Services remain registered for the runtime; nothing to unload.
    return True
