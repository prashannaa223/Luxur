"""Reminders: parse a time out of 'reminder 9:00pm call mom' and announce it when due."""
import re
import threading
from datetime import datetime, timedelta

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def ParseDue(text, now=None):
    """Best-effort time parser. Returns (datetime or None, message without the time words)."""
    now = now or datetime.now()
    t = text.lower()
    rel = re.search(r"\bin (\d+) (minute|minutes|min|mins|hour|hours|hr|hrs)\b", t)
    if rel:
        n = int(rel.group(1))
        due = now + (timedelta(hours=n) if rel.group(2).startswith("h") else timedelta(minutes=n))
        return due, _clean(text, rel.group(0))
    clocks = re.finditer(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?(?=\b|\s|$)", t)
    # The first number with am/pm or a colon is the time ("25th june 9pm" -> 9pm, not 25).
    clock = next((c for c in clocks if c.group(2) or c.group(3)), None)
    if not clock:
        return None, text.strip()
    hour, minute = int(clock.group(1)), int(clock.group(2) or 0)
    meridiem = (clock.group(3) or "").replace(".", "")
    if meridiem == "pm" and hour < 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None, text.strip()
    day = now.date()
    date = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([a-z]{3})[a-z]*\b", t)
    removed = [clock.group(0)]
    if date and date.group(2) in MONTHS:
        try:
            day = day.replace(month=MONTHS[date.group(2)], day=int(date.group(1)))
            removed.append(date.group(0))
        except ValueError:
            pass
    elif "tomorrow" in t:
        day = day + timedelta(days=1)
        removed.append("tomorrow")
    due = datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)
    if due < now and not date:
        due += timedelta(days=1)
    return due, _clean(text, *removed)


def _clean(text, *parts):
    for part in parts:
        text = re.sub(re.escape(part), "", text, flags=re.I)
    text = re.sub(r"\b(at|on|to|for|remind me|set a reminder)\b", " ", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip(" ,.") or "your reminder"


class Scheduler:
    """Checks Pulse Hub every 20 s and announces due reminders."""

    def __init__(self, hub, announce, interval=20):
        self.hub, self.announce, self.interval = hub, announce, interval
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()
        return self

    def check_once(self):
        fired = []
        for row in self.hub.due_reminders():
            self.hub.update_task(row["id"], "done", {"announced": datetime.now().isoformat(timespec="minutes")})
            self.announce(f"Reminder: {row['title']}")
            fired.append(row["title"])
        return fired

    def _loop(self):
        while not self._stop.wait(self.interval):
            try:
                self.check_once()
            except Exception as e:
                print(f"[Reminders] {e}")
