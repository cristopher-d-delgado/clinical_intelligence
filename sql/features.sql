/*
View    : v_cohort
Purpose : Base cohort table — the spine of the feature matrix.
            One row per ICU stay with patient demographics, length of
            stay calculations, and the target variable for ML modeling.

Source tables:
    icustays    — ICU entry/exit times and stay identifiers
    admissions  — Hospital admission/discharge times, admission type,
                    insurance, ethnicity, and mortality flag
    patients    — Patient demographics (gender, date of birth, date of death)

Computed columns:
    icu_los_hours   — ICU length of stay in hours using JULIANDAY() arithmetic
    hosp_los_days   — Hospital length of stay in days
    age_at_admit    — Patient age at time of hospital admission in years

Target variable:
    died_in_hospital — Binary 0/1 flag derived from hospital_expire_flag
                        in the admissions table. 1 = patient died during
                        this hospital admission, 0 = survived to discharge.

Notes:
    - All 100 demo patients were selected because they eventually die,
        so died_in_hospital will be skewed toward 1. Keep this in mind
        when interpreting model performance.
    - Grain is one row per ICU stay, not one row per patient. A single
        patient can have multiple ICU stays across multiple admissions.
*/
CREATE VIEW IF NOT EXISTS v_cohort AS 
SELECT 
    p.subject_id, p.dob,
    i.intime, i.outtime, 
    a.admittime, a.dischtime,
    
    ROUND((JULIANDAY(outtime) - JULIANDAY(intime)) * 24, 2) AS icu_los_hours,
    ROUND(JULIANDAY(dischtime) - JULIANDAY(admittime), 2) AS hosp_los_days,
    ROUND((JULIANDAY(admittime) - JULIANDAY(dob)) / 365, 1) AS age_at_admit
FROM icustays AS i 
JOIN admissions AS a ON i.hadm_id = a.hadm_id
JOIN patients AS p ON a.subject_id = p.subject_id

