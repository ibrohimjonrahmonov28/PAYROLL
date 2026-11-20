"""
Fabrika smena hisob-kitobi (Asia/Tashkent).
Egasi talabi:
- Ish kuni sikli: 08:00 dan ertasi kuni 08:00 gacha (24 soat).
- 1-smena (kunduzgi): 08:00 – 18:00.
- 2-smena (tungi): 18:00 – 08:00 (ertasi kuni ertalab 08:00 gacha, masalan 20-kuni soat 03:00 da
  bajarilgan ish 19-kunning 2-smenasiga tegishli bo'ladi).
"""
from __future__ import annotations

import zoneinfo
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, Optional

from django.utils import timezone

TASHKENT_TZ = zoneinfo.ZoneInfo("Asia/Tashkent")
SHIFT_1_START_HOUR = 8
SHIFT_1_END_HOUR = 18


def _to_tashkent(dt: Optional[datetime]) -> datetime:
    """Berilgan datetime ni Asia/Tashkent vaqt zonasiga o'tkazish."""
    if dt is None:
        return timezone.localtime()
    if timezone.is_naive(dt):
        return dt.replace(tzinfo=TASHKENT_TZ)
    return dt.astimezone(TASHKENT_TZ)


def get_shift_info(dt: Optional[datetime] = None) -> Dict[str, Any]:
    """
    Berilgan vaqt (yoki hozirgi vaqt) bo'yicha joriy smena va ish kuni ma'lumotlarini qaytaradi.

    Qoidalar:
      - 08:00 <= t < 18:00:
          1-smena (kunduzgi), ish kuni = dt.date(), [08:00, 18:00)
      - t >= 18:00:
          2-smena (tungi), ish kuni = dt.date(), [18:00, ertasi 08:00)
      - t < 08:00:
          2-smena (tungi), ish kuni = dt.date() - 1 kun, [kechagi 18:00, bugun 08:00)
    """
    local_dt = _to_tashkent(dt)
    t = local_dt.time()
    d = local_dt.date()

    if time(SHIFT_1_START_HOUR, 0) <= t < time(SHIFT_1_END_HOUR, 0):
        shift_num = 1
        shift_name = "1-smena (kunduzgi)"
        shift_label = "1-smena"
        work_date = d
        shift_start = datetime.combine(work_date, time(SHIFT_1_START_HOUR, 0), tzinfo=TASHKENT_TZ)
        shift_end = datetime.combine(work_date, time(SHIFT_1_END_HOUR, 0), tzinfo=TASHKENT_TZ)
        time_window_str = "08:00 – 18:00"
    elif t >= time(SHIFT_1_END_HOUR, 0):
        shift_num = 2
        shift_name = "2-smena (tungi)"
        shift_label = "2-smena"
        work_date = d
        shift_start = datetime.combine(work_date, time(SHIFT_1_END_HOUR, 0), tzinfo=TASHKENT_TZ)
        shift_end = datetime.combine(work_date + timedelta(days=1), time(SHIFT_1_START_HOUR, 0), tzinfo=TASHKENT_TZ)
        time_window_str = "18:00 – 08:00"
    else:  # t < 08:00 (ertasi kuni ertalabki qism)
        shift_num = 2
        shift_name = "2-smena (tungi)"
        shift_label = "2-smena"
        work_date = d - timedelta(days=1)
        shift_start = datetime.combine(work_date, time(SHIFT_1_END_HOUR, 0), tzinfo=TASHKENT_TZ)
        shift_end = datetime.combine(d, time(SHIFT_1_START_HOUR, 0), tzinfo=TASHKENT_TZ)
        time_window_str = "18:00 – 08:00"

    day_start = datetime.combine(work_date, time(SHIFT_1_START_HOUR, 0), tzinfo=TASHKENT_TZ)
    day_end = datetime.combine(work_date + timedelta(days=1), time(SHIFT_1_START_HOUR, 0), tzinfo=TASHKENT_TZ)

    return {
        'shift_num': shift_num,
        'shift_name': shift_name,
        'shift_label': shift_label,
        'work_date': work_date,
        'work_date_str': work_date.strftime("%d.%m.%Y"),
        'shift_start': shift_start,
        'shift_end': shift_end,
        'day_start': day_start,
        'day_end': day_end,
        'time_window_str': time_window_str,
    }


def get_shift_for_timestamp(dt: Optional[datetime]) -> Optional[Dict[str, Any]]:
    """Tarixdagi biron bir timestamp (masalan meto_done_at) qaysi smenada bo'lganini aniqlash."""
    if dt is None:
        return None
    return get_shift_info(dt)


def get_work_day_shifts(work_date: Optional[date] = None) -> Dict[str, Any]:
    """Berilgan ish kuni uchun 1- va 2-smena oraliqlarini qaytaradi."""
    if work_date is None:
        work_date = get_shift_info()['work_date']

    day_start = datetime.combine(work_date, time(SHIFT_1_START_HOUR, 0), tzinfo=TASHKENT_TZ)
    shift_1_end = datetime.combine(work_date, time(SHIFT_1_END_HOUR, 0), tzinfo=TASHKENT_TZ)
    day_end = datetime.combine(work_date + timedelta(days=1), time(SHIFT_1_START_HOUR, 0), tzinfo=TASHKENT_TZ)

    return {
        'work_date': work_date,
        'work_date_str': work_date.strftime("%d.%m.%Y"),
        'day_start': day_start,
        'day_end': day_end,
        'shift1': {
            'num': 1,
            'name': "1-smena (kunduzgi)",
            'label': "1-smena",
            'start': day_start,
            'end': shift_1_end,
            'time_window_str': "08:00 – 18:00",
        },
        'shift2': {
            'num': 2,
            'name': "2-smena (tungi)",
            'label': "2-smena",
            'start': shift_1_end,
            'end': day_end,
            'time_window_str': "18:00 – 08:00",
        },
    }
