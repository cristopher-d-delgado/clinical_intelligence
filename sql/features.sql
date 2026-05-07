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
JOIN patients AS p ON a.subject_id = p.subject_id;

/*
View    : v_vitals_24h
Purpose : Vital sign summary statistics for the first 24 hours of each
            ICU stay. Provides mean, min, and max for each vital sign plus
            a total measurement count as a monitoring intensity proxy.

Source tables:
    icustays    — Provides icustay_id and intime for the 24-hour window
    chartevents — Raw vital sign measurements with itemid, charttime,
                    and valuenum

Time window:
    charttime >= intime
    charttime <= DATETIME(intime, '+24 hours')
    Only measurements in the first 24 hours of ICU admission are included.
    This ensures features are comparable across patients regardless of
    total ICU length of stay, and reflects information available early
    enough to support clinical decision making.

Item ID mapping (CareVue / Metavision):
    Heart Rate        : 211, 220045
    Systolic BP       : 51, 220179
    SpO2              : 646, 220277
    Respiratory Rate  : 618, 220210
    Temperature (F)   : 678, 223761
    GCS Total         : 198, 223900

Data quality filters:
    valuenum IS NOT NULL — excludes missing measurements
    valuenum > 0         — excludes physiologically impossible values

Aggregation:
    ROUND(..., 1) applied to all values — one decimal place is the
    clinical standard for vital sign reporting.

Notes:
    - Two item IDs per vital sign reflect the CareVue and Metavision
        charting systems used at different times in MIMIC-III. Both are
        needed to avoid missing measurements depending on which system
        was active for a given patient.
    - n_vital_measurements_24h counts all vital rows regardless of type
        and serves as a proxy for monitoring intensity.
*/
CREATE VIEW IF NOT EXISTS v_vitals_24h AS
SELECT 
    i.icustay_id,

    -- Heart Rate
    ROUND(AVG(CASE WHEN c.itemid IN (211, 220045) THEN c.valuenum END), 1) AS hr_mean_24h,
    ROUND(MIN(CASE WHEN c.itemid IN (211, 220045) THEN c.valuenum END), 1) AS hr_min_24h,
    ROUND(MAX(CASE WHEN c.itemid IN (211, 220045) THEN c.valuenum END), 1) AS hr_max_24h,

    -- Systolic BP
    ROUND(AVG(CASE WHEN c.itemid IN (51, 220179) THEN c.valuenum END), 1) AS sbp_mean_24h,
    ROUND(MIN(CASE WHEN c.itemid IN (51, 220179) THEN c.valuenum END), 1) AS sbp_min_24h,
    ROUND(MAX(CASE WHEN c.itemid IN (51, 220179) THEN c.valuenum END), 1) AS sbp_max_24h,

    -- SpO2
    ROUND(AVG(CASE WHEN c.itemid IN (646, 220277) THEN c.valuenum END), 1) AS spo2_mean_24h,
    ROUND(MIN(CASE WHEN c.itemid IN (646, 220277) THEN c.valuenum END), 1) AS spo2_min_24h,
    ROUND(MAX(CASE WHEN c.itemid IN (646, 220277) THEN c.valuenum END), 1) AS spo2_max_24h,

    -- Respiratory Rate
    ROUND(AVG(CASE WHEN c.itemid IN (618, 220210) THEN c.valuenum END), 1) AS resp_mean_24h,
    ROUND(MIN(CASE WHEN c.itemid IN (618, 220210) THEN c.valuenum END), 1) AS resp_min_24h,
    ROUND(MAX(CASE WHEN c.itemid IN (618, 220210) THEN c.valuenum END), 1) AS resp_max_24h,

    -- Temperature (Fahrenheit)
    ROUND(AVG(CASE WHEN c.itemid IN (678, 223761) THEN c.valuenum END), 1) AS temp_mean_24h,
    ROUND(MIN(CASE WHEN c.itemid IN (678, 223761) THEN c.valuenum END), 1) AS temp_min_24h,
    ROUND(MAX(CASE WHEN c.itemid IN (678, 223761) THEN c.valuenum END), 1) AS temp_max_24h,

    -- GCS Total
    ROUND(AVG(CASE WHEN c.itemid IN (198, 223900) THEN c.valuenum END), 1) AS gcs_mean_24h,
    ROUND(MIN(CASE WHEN c.itemid IN (198, 223900) THEN c.valuenum END), 1) AS gcs_min_24h,
    ROUND(MAX(CASE WHEN c.itemid IN (198, 223900) THEN c.valuenum END), 1) AS gcs_max_24h,

    -- Total vital measurements
    COUNT(c.valuenum) AS n_vital_measurements_24h

FROM icustays AS i
JOIN chartevents AS c ON i.subject_id = c.subject_id
WHERE c.charttime >= i.intime
AND   c.charttime <= DATETIME(i.intime, '+24 hours')
AND   c.valuenum IS NOT NULL
AND   c.valuenum > 0
GROUP BY i.icustay_id;