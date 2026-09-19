"""Generate realistic HR sample datasets for DataPilot demos."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "sample_data"

DEPARTMENTS = ["Engineering", "Sales", "HR", "Operations", "Finance"]
LOCATIONS = ["Bangalore", "Hyderabad", "Mumbai", "Pune", "Remote"]
EMPLOYMENT = ["Full-time", "Contract", "Intern"]
FIRST = [
    "Aarav", "Diya", "Ishaan", "Meera", "Kabir", "Ananya", "Rohan", "Sara",
    "Vikram", "Nisha", "Arjun", "Priya", "Rahul", "Kavya", "Dev", "Isha",
    "Neel", "Tara", "Aman", "Riya",
]
LAST = ["Sharma", "Patel", "Reddy", "Nair", "Khan", "Iyer", "Gupta", "Das", "Mehta", "Joshi"]
MONTHS = [f"2025-{month:02d}" for month in range(1, 7)]
PERIODS = ["2024-Q3", "2024-Q4", "2025-Q1", "2025-Q2"]


def main() -> None:
    rng = np.random.default_rng(42)
    SAMPLE.mkdir(parents=True, exist_ok=True)

    rows = []
    for emp_id in range(101, 161):
        dept = DEPARTMENTS[(emp_id - 101) % len(DEPARTMENTS)]
        # Bias locations so Bangalore is largest, Hyderabad second.
        loc = rng.choice(LOCATIONS, p=[0.34, 0.26, 0.16, 0.12, 0.12])
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        join = pd.Timestamp("2021-01-01") + pd.Timedelta(days=int(rng.integers(0, 1400)))
        emp_type = rng.choice(EMPLOYMENT, p=[0.78, 0.16, 0.06])
        rows.append(
            {
                "employee_id": emp_id,
                "employee_name": name,
                "department": dept,
                "location": loc,
                "joining_date": join.strftime("%Y-%m-%d"),
                "employment_type": emp_type,
            }
        )
    employees = pd.DataFrame(rows)
    employees.to_csv(SAMPLE / "employees.csv", index=False)

    attendance_rows = []
    dept_attendance_bias = {
        "Engineering": 0.94,
        "Sales": 0.88,
        "HR": 0.91,
        "Operations": 0.86,
        "Finance": 0.92,
    }
    loc_bias = {"Bangalore": 0.01, "Hyderabad": -0.02, "Mumbai": 0.0, "Pune": 0.0, "Remote": 0.02}
    for _, emp in employees.iterrows():
        base = dept_attendance_bias[emp["department"]] + loc_bias[emp["location"]]
        for month in MONTHS:
            working = int(rng.integers(20, 23))
            rate = min(0.99, max(0.72, base + float(rng.normal(0, 0.03))))
            present = int(round(working * rate))
            leave = working - present
            attendance_rows.append(
                {
                    "employee_id": int(emp["employee_id"]),
                    "month": month,
                    "working_days": working,
                    "present_days": present,
                    "leave_days": leave,
                }
            )
    attendance = pd.DataFrame(attendance_rows)
    attendance.to_excel(SAMPLE / "attendance.xlsx", index=False, engine="openpyxl")

    perf_rows = []
    dept_perf = {
        "Engineering": 4.18,
        "Sales": 3.72,
        "HR": 3.95,
        "Operations": 3.55,
        "Finance": 4.02,
    }
    for _, emp in employees.iterrows():
        for idx, period in enumerate(PERIODS):
            mean = dept_perf[emp["department"]] + idx * 0.04
            score = float(np.clip(rng.normal(mean, 0.28), 2.4, 5.0))
            manager = float(np.clip(score + rng.normal(0, 0.15), 2.5, 5.0))
            perf_rows.append(
                {
                    "employee_id": int(emp["employee_id"]),
                    "review_period": period,
                    "performance_score": round(score, 2),
                    "manager_rating": round(manager, 2),
                }
            )
    performance = pd.DataFrame(perf_rows)
    performance.to_csv(SAMPLE / "performance.csv", index=False)
    print(f"Wrote {len(employees)} employees, {len(attendance)} attendance rows, {len(performance)} performance rows")


if __name__ == "__main__":
    main()
