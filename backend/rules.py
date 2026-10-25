"""Pure business rules (Section 3 of the brief) - no DB access, fully unit-testable."""
import math, datetime as dt

def is_working_day(d, weekend, holidays):
    return d.weekday() not in weekend and d not in holidays

def working_days(start, end, weekend, holidays):
    n, d = 0, start
    while d <= end:
        n += is_working_day(d, weekend, holidays); d += dt.timedelta(1)
    return n

def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))

def calc_status(check_in, check_out, office_start="09:00", grace_min=15, half_day_hours=5.0):
    h, m = map(int, office_start.split(":"))
    late_after = check_in.replace(hour=h, minute=m, second=0, microsecond=0) + dt.timedelta(minutes=grace_min)
    status = "late" if check_in > late_after else "present"
    if check_out and (check_out - check_in).total_seconds() / 3600 < half_day_hours:
        status = "half_day"
    return status

# (lower, upper, rate, base tax at lower) - annual gross, illustrative slab from the brief
SLABS = [(0, 600_000, 0.0, 0), (600_000, 1_200_000, 0.05, 0), (1_200_000, 2_400_000, 0.15, 30_000),
         (2_400_000, 3_600_000, 0.25, 210_000), (3_600_000, math.inf, 0.35, 510_000)]

def annual_tax(annual):
    for lo, hi, rate, base in SLABS:
        if annual <= hi:
            return base + rate * max(annual - lo, 0)

def compute_payslip(basic, wd_month, wd_active, absent, unpaid, half):
    r = lambda x: round(x, 2)
    gross = 1.5 * basic                                  # basic + HRA 40% + medical 10%
    wd = max(wd_month, 1)
    per_day = gross / wd
    factor = min(wd_active / wd, 1.0)                    # joiner / leaver proration
    ded = per_day * (absent + unpaid + 0.5 * half)
    tax = annual_tax(gross * 12) / 12 * factor
    net = max(gross * factor - ded - tax, 0)
    return dict(basic=r(basic), hra=r(.4 * basic), medical=r(.1 * basic), gross=r(gross),
                working_days=wd_month, active_days=wd_active, absent_days=absent,
                unpaid_days=unpaid, half_days=half, deductions=r(ded), tax=r(tax), net=r(net))

def month_bounds(month: str):
    y, m = map(int, month.split("-"))
    start = dt.date(y, m, 1)
    end = (dt.date(y + (m == 12), (m % 12) + 1, 1) - dt.timedelta(1))
    return start, end
