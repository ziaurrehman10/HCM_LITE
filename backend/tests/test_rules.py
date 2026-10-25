import datetime as dt
from rules import annual_tax, working_days, compute_payslip, haversine_m, calc_status, month_bounds

def test_tax_slabs():
    assert annual_tax(500_000) == 0
    assert annual_tax(1_000_000) == 20_000
    assert annual_tax(1_200_000) == 30_000
    assert annual_tax(2_400_000) == 210_000
    assert annual_tax(3_600_000) == 510_000
    assert round(annual_tax(4_000_000)) == 650_000

def test_working_days_skips_weekend_and_holiday():
    mon, sun = dt.date(2026, 10, 19), dt.date(2026, 10, 25)
    assert working_days(mon, sun, {5, 6}, set()) == 5
    assert working_days(mon, sun, {5, 6}, {dt.date(2026, 10, 21)}) == 4

def test_status_rules():
    d = dt.datetime(2026, 10, 20)
    assert calc_status(d.replace(hour=9, minute=15), None) == "present"
    assert calc_status(d.replace(hour=9, minute=16), None) == "late"
    assert calc_status(d.replace(hour=9), d.replace(hour=13)) == "half_day"

def test_payslip_math():
    p = compute_payslip(100_000, 22, 22, absent=1, unpaid=1, half=2)
    assert p["gross"] == 150_000
    assert p["deductions"] == round(150_000 / 22 * 3, 2)
    assert p["hra"] == 40_000 and p["medical"] == 10_000
    assert compute_payslip(100_000, 20, 10, 0, 0, 0)["tax"] == round(annual_tax(1_800_000) / 12 / 2, 2)

def test_haversine_and_month():
    assert haversine_m(24.86, 67.0, 24.86, 67.0) == 0
    assert 100 < haversine_m(24.8600, 67.0000, 24.8610, 67.0000) < 120
    assert month_bounds("2026-02") == (dt.date(2026, 2, 1), dt.date(2026, 2, 28))
