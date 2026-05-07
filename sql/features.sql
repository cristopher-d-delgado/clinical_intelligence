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

# In this case there is only one vital id. 
# This makes sense b/c this vital comes from lab not measurements from two medical devices
# v_lab
CREATE VIEW IF NOT EXISTS v_labs_24h AS
SELECT
    i.icustay_id,

    -- Hematocrit
    ROUND(AVG(CASE WHEN l.itemid = 51221 THEN l.valuenum END), 2) AS hematocrit_mean_24h,
    ROUND(MIN(CASE WHEN l.itemid = 51221 THEN l.valuenum END), 2) AS hematocrit_min_24h,

    -- Potassium
    ROUND(AVG(CASE WHEN l.itemid = 50971 THEN l.valuenum END), 2) AS potassium_mean_24h,
    ROUND(MIN(CASE WHEN l.itemid = 50971 THEN l.valuenum END), 2) AS potassium_min_24h,
    ROUND(MAX(CASE WHEN l.itemid = 50971 THEN l.valuenum END), 2) AS potassium_max_24h,

    -- Sodium
    ROUND(AVG(CASE WHEN l.itemid = 50983 THEN l.valuenum END), 2) AS sodium_mean_24h,
    ROUND(MIN(CASE WHEN l.itemid = 50983 THEN l.valuenum END), 2) AS sodium_min_24h,
    ROUND(MAX(CASE WHEN l.itemid = 50983 THEN l.valuenum END), 2) AS sodium_max_24h,

    -- Creatinine
    ROUND(AVG(CASE WHEN l.itemid = 50912 THEN l.valuenum END), 2) AS creatinine_mean_24h,
    ROUND(MAX(CASE WHEN l.itemid = 50912 THEN l.valuenum END), 2) AS creatinine_max_24h,

    -- Chloride
    ROUND(AVG(CASE WHEN l.itemid = 50902 THEN l.valuenum END), 2) AS chloride_mean_24h,

    -- Urea Nitrogen (BUN)
    ROUND(AVG(CASE WHEN l.itemid = 51006 THEN l.valuenum END), 2) AS bun_mean_24h,
    ROUND(MAX(CASE WHEN l.itemid = 51006 THEN l.valuenum END), 2) AS bun_max_24h,

    -- Bicarbonate
    ROUND(AVG(CASE WHEN l.itemid = 50882 THEN l.valuenum END), 2) AS bicarb_mean_24h,
    ROUND(MIN(CASE WHEN l.itemid = 50882 THEN l.valuenum END), 2) AS bicarb_min_24h,

    -- Anion Gap
    ROUND(AVG(CASE WHEN l.itemid = 50868 THEN l.valuenum END), 2) AS anion_gap_mean_24h,
    ROUND(MAX(CASE WHEN l.itemid = 50868 THEN l.valuenum END), 2) AS anion_gap_max_24h,

    -- Glucose
    ROUND(AVG(CASE WHEN l.itemid = 50931 THEN l.valuenum END), 2) AS glucose_mean_24h,
    ROUND(MIN(CASE WHEN l.itemid = 50931 THEN l.valuenum END), 2) AS glucose_min_24h,
    ROUND(MAX(CASE WHEN l.itemid = 50931 THEN l.valuenum END), 2) AS glucose_max_24h,

    -- Platelet Count
    ROUND(AVG(CASE WHEN l.itemid = 51265 THEN l.valuenum END), 2) AS platelets_mean_24h,
    ROUND(MIN(CASE WHEN l.itemid = 51265 THEN l.valuenum END), 2) AS platelets_min_24h,

    -- Hemoglobin
    ROUND(AVG(CASE WHEN l.itemid = 51222 THEN l.valuenum END), 2) AS hemoglobin_mean_24h,
    ROUND(MIN(CASE WHEN l.itemid = 51222 THEN l.valuenum END), 2) AS hemoglobin_min_24h,

    -- White Blood Cells
    ROUND(AVG(CASE WHEN l.itemid = 51301 THEN l.valuenum END), 2) AS wbc_mean_24h,
    ROUND(MAX(CASE WHEN l.itemid = 51301 THEN l.valuenum END), 2) AS wbc_max_24h,

    -- MCHC
    ROUND(AVG(CASE WHEN l.itemid = 51249 THEN l.valuenum END), 2) AS mchc_mean_24h,

    -- MCH
    ROUND(AVG(CASE WHEN l.itemid = 51248 THEN l.valuenum END), 2) AS mch_mean_24h,

    -- MCV
    ROUND(AVG(CASE WHEN l.itemid = 51250 THEN l.valuenum END), 2) AS mcv_mean_24h,

    -- Red Blood Cells
    ROUND(AVG(CASE WHEN l.itemid = 51279 THEN l.valuenum END), 2) AS rbc_mean_24h,
    ROUND(MIN(CASE WHEN l.itemid = 51279 THEN l.valuenum END), 2) AS rbc_min_24h,

    -- RDW
    ROUND(AVG(CASE WHEN l.itemid = 51277 THEN l.valuenum END), 2) AS rdw_mean_24h,
    ROUND(MAX(CASE WHEN l.itemid = 51277 THEN l.valuenum END), 2) AS rdw_max_24h,

    -- Magnesium
    ROUND(AVG(CASE WHEN l.itemid = 50960 THEN l.valuenum END), 2) AS magnesium_mean_24h,
    ROUND(MIN(CASE WHEN l.itemid = 50960 THEN l.valuenum END), 2) AS magnesium_min_24h,

    -- Calcium Total
    ROUND(AVG(CASE WHEN l.itemid = 50893 THEN l.valuenum END), 2) AS calcium_mean_24h,
    ROUND(MIN(CASE WHEN l.itemid = 50893 THEN l.valuenum END), 2) AS calcium_min_24h,

    -- Phosphate
    ROUND(AVG(CASE WHEN l.itemid = 50970 THEN l.valuenum END), 2) AS phosphate_mean_24h,
    ROUND(MAX(CASE WHEN l.itemid = 50970 THEN l.valuenum END), 2) AS phosphate_max_24h,

    -- Total lab draws 
    COUNT(*) AS n_lab_draws_24h

FROM icustays AS i
JOIN labevents AS l ON i.subject_id = l.subject_id
WHERE l.charttime >= i.intime
AND   l.charttime <= DATETIME(i.intime, '+24 hours')
AND   l.valuenum IS NOT NULL
AND   l.valuenum > 0
GROUP BY i.icustay_id;

/*
View    : v_comorbidities
Purpose : Binary flags indicating the presence of key chronic conditions
            and acute diagnoses at the time of hospital admission.
            One row per hospital admission (hadm_id level, not ICU stay level).

Source table:
    diagnoses_icd — ICD-9 diagnosis codes assigned to each admission

Conditions flagged:
    has_hypertension  — ICD-9: 4019   (Unspecified essential hypertension)
    has_afib          — ICD-9: 42731  (Atrial fibrillation)
    has_aki           — ICD-9: 584%   (Acute kidney failure, all variants)
    has_chf           — ICD-9: 428%   (Congestive heart failure, all variants)
    has_diabetes      — ICD-9: 250%   (Diabetes mellitus, all variants)
    has_resp_failure  — ICD-9: 51881  (Acute respiratory failure)
    has_sepsis        — ICD-9: 99592, 0389 (Severe sepsis + Septicemia)
    has_anemia        — ICD-9: 285%   (Anemia, all variants)
    has_cad           — ICD-9: 414%   (Coronary artery disease, all variants)
    has_acidosis      — ICD-9: 2762   (Acidosis)

Aggregation:
    MAX(CASE WHEN icd9_code ... THEN 1 ELSE 0 END)
    Returns 1 if any diagnosis row for this admission matches the
    condition, 0 if none match. Effectively an OR across all diagnosis
    rows per admission.

Additional column:
    n_diagnoses — COUNT(DISTINCT icd9_code) as a patient complexity proxy.
                    Higher values indicate more comorbid conditions.

Notes:
    - Conditions were selected based on the top 20 most frequent diagnoses
        in the MIMIC-III demo dataset, filtered for clinical relevance to
        ICU mortality prediction.
    - LIKE patterns (e.g. '428%') capture all sub-variants of a condition
        family rather than a single specific code.
    - Grain is hadm_id — joins to v_cohort on hadm_id in v_features.
*/
CREATE VIEW IF NOT EXISTS v_comorbidities AS
SELECT
    d.subject_id,
    d.hadm_id,
    -- Hypertension
    MAX(CASE WHEN d.icd9_code = '4019' THEN 1 ELSE 0 END) AS has_hypertension,

    -- Atrial Fibrillation
    MAX(CASE WHEN d.icd9_code = '42731' THEN 1 ELSE 0 END) AS has_afib,

    -- Acute Kidney Failure
    MAX(CASE WHEN d.icd9_code LIKE '584%' THEN 1 ELSE 0 END) AS has_aki,

    -- CHF
    MAX(CASE WHEN d.icd9_code LIKE '428%' THEN 1 ELSE 0 END) AS has_chf,

    -- Diabetes
    MAX(CASE WHEN d.icd9_code LIKE '250%' THEN 1 ELSE 0 END) AS has_diabetes,

    -- Acute Respiratory Failure
    MAX(CASE WHEN d.icd9_code = '51881' THEN 1 ELSE 0 END) AS has_resp_failure,

    -- Sepsis (both codes combined)
    MAX(CASE WHEN d.icd9_code IN ('99592', '0389') THEN 1 ELSE 0 END) AS has_sepsis,

    -- Anemia
    MAX(CASE WHEN d.icd9_code LIKE '285%' THEN 1 ELSE 0 END) AS has_anemia,

    -- Coronary Artery Disease
    MAX(CASE WHEN d.icd9_code LIKE '414%' THEN 1 ELSE 0 END) AS has_cad,

    -- Acidosis
    MAX(CASE WHEN d.icd9_code = '2762' THEN 1 ELSE 0 END) AS has_acidosis,

    -- Total diagnoses (complexity proxy)
    COUNT(DISTINCT d.icd9_code) AS n_diagnoses

FROM diagnoses_icd AS d
GROUP BY d.subject_id, d.hadm_id;
