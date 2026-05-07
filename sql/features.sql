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
DROP VIEW IF EXISTS v_cohort;
    
CREATE VIEW IF NOT EXISTS v_cohort AS
SELECT 
    p.subject_id,
    i.hadm_id,
    i.icustay_id,
    p.gender,
    a.admission_type,
    a.insurance,
    a.ethnicity,
    p.dob,
    p.dod,
    i.intime, 
    i.outtime, 
    a.admittime, 
    a.dischtime,
    ROUND((JULIANDAY(i.outtime)   - JULIANDAY(i.intime))   * 24, 2) AS icu_los_hours,
    ROUND( JULIANDAY(a.dischtime) - JULIANDAY(a.admittime),       2) AS hosp_los_days,
    ROUND((JULIANDAY(a.admittime) - JULIANDAY(p.dob))      / 365.25, 1) AS age_at_admit,
    CASE WHEN a.hospital_expire_flag = 1 THEN 1 ELSE 0 END AS died_in_hospital
FROM icustays AS i 
JOIN admissions AS a ON i.hadm_id    = a.hadm_id
JOIN patients   AS p ON a.subject_id = p.subject_id;

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

/*
View    : v_prior_admissions
Purpose : Count of prior hospital admissions for each patient before
            the current admission. A strong predictor of readmission risk
            and patient complexity.

Source table:
    admissions — Self-joined to compare each admission against all
                    earlier admissions for the same patient

Self-join logic:
    a1 = current admission
    a2 = any earlier admission for the same patient
    JOIN condition: a1.subject_id = a2.subject_id
                AND a2.admittime < a1.admittime

Join type:
    LEFT JOIN — ensures patients on their first admission are retained
    in the result with n_prior_admissions = 0. An INNER JOIN would
    exclude first-time admissions entirely.

Aggregation:
    COUNT(a2.hadm_id) — counts matching prior admission rows.
    Returns 0 for first admissions (no a2 rows match),
    1+ for patients with prior history.

Notes:
    - In the MIMIC-III demo most patients have only one admission since
        the cohort was selected based on mortality. The feature still adds
        value as a binary signal (ever admitted before vs never).
    - Grain is hadm_id — joins to v_cohort on hadm_id in v_features.
*/
CREATE VIEW IF NOT EXISTS v_prior_admissions AS
SELECT
    a1.subject_id,
    a1.hadm_id,
    COUNT(a2.hadm_id) AS n_prior_admissions
FROM admissions AS a1
LEFT JOIN admissions AS a2
    ON  a1.subject_id = a2.subject_id
    AND a2.admittime  < a1.admittime
GROUP BY a1.subject_id, a1.hadm_id
ORDER BY n_prior_admissions DESC;

/*
View    : v_vasopressors
Purpose : Binary flag indicating whether vasopressors were administered
            during each ICU stay. Vasopressor use indicates haemodynamic
            instability and is strongly associated with ICU mortality.

Source tables:
    inputevents_cv — CareVue medication inputs (older charting system)
    inputevents_mv — Metavision medication inputs (newer charting system)

Vasopressors captured:
    Dobutamine      — CV: 30042, 30306, 5747       MV: 221653
    Dopamine        — CV: 4501, 5805, 5329, 30043,  MV: 221662
                            30307
    Epinephrine     — CV: 30044, 30309, 30119, 3112 MV: 221289
    Norepinephrine  — MV: 221906
    Vasopressin     — CV: 1222, 2765, 42802, 2561,  MV: 222315
                            2248, 6255, 2445, 30051,
                            7341, 1136, 2334, 1327,
                            42273

Method:
    UNION of two SELECT DISTINCT queries — one per charting system.
    DISTINCT ensures one row per ICU stay regardless of how many
    vasopressor administrations occurred.
    UNION (not UNION ALL) removes any duplicate icustay_ids that
    appear in both systems.

Output:
    icustay_id      — ICU stay identifier
    vasopressor_flag — Always 1 (presence flag only)

Notes:
    - Absence of a row in this view means no vasopressors were given.
        In v_features this is handled with COALESCE(vp.vasopressor_flag, 0)
        to convert NULL to 0 for ICU stays not in this view.
    - 32 of 136 ICU stays in the demo had vasopressor use (~24%).
*/
CREATE VIEW IF NOT EXISTS v_vasopressors AS
SELECT DISTINCT icustay_id, 1 AS vasopressor_flag
FROM inputevents_cv
WHERE itemid IN (
    4501, 5805, 30042, 30306, 5329, 30043, 30307,  -- Dopamine/Dobutamine CareVue
    30044, 30309, 30119,                             -- Epinephrine CareVue
    1222, 2765, 42802, 2561, 2248, 6255, 2445,      -- Vasopressin CareVue
    30051, 7341, 5747, 3112, 1136, 2334, 1327,      -- Others CareVue
    42273
)
UNION
SELECT DISTINCT icustay_id, 1 AS vasopressor_flag
FROM inputevents_mv
WHERE itemid IN (
    221653,  -- Dobutamine Metavision
    221662,  -- Dopamine Metavision
    221289,  -- Epinephrine Metavision
    221906,  -- Norepinephrine Metavision
    222315   -- Vasopressin Metavision
);

/*
View    : v_features
Purpose : Master feature view — assembles all upstream views into one
            flat table ready for pandas and XGBoost. One row per ICU stay,
            one column per feature plus the target variable.

Source views (all LEFT JOINed onto v_cohort):
    v_cohort            — Demographics, LOS, target variable
    v_vitals_24h        — Vital sign stats, joined on icustay_id
    v_labs_24h          — Lab result stats, joined on icustay_id
    v_comorbidities     — Comorbidity flags, joined on hadm_id
    v_prior_admissions  — Prior admission count, joined on hadm_id
    v_vasopressors      — Vasopressor flag, joined on icustay_id

Join type:
    LEFT JOIN throughout — ensures all ICU stays from v_cohort are
    retained even if they have no matching rows in downstream views
    (e.g. no labs drawn, no vasopressors given). Missing values appear
    as NULL and are handled by median imputation in sql_features.py.

Special handling:
    COALESCE(vp.vasopressor_flag, 0) — converts NULL to 0 for ICU stays
    with no vasopressor rows in v_vasopressors.

Output:
    ~136 rows (one per ICU stay in the demo)
    ~50+ columns covering demographics, vitals, labs,
    comorbidities, prior history, interventions, and target

Usage:
    Queried by sql_features.py via:
        SELECT * FROM v_features
    The resulting DataFrame is passed through _clean() for
    categorical encoding, outlier clipping, and NaN imputation
    before being handed to XGBoost in model.py.
*/
DROP VIEW IF EXISTS v_features;

CREATE VIEW IF NOT EXISTS v_features AS
SELECT
    -- v_cohort (all columns)
    c.subject_id,
    c.hadm_id,
    c.icustay_id,
    c.gender,
    c.admission_type,
    c.insurance,
    c.ethnicity,
    c.dob,
    c.dod,
    c.intime,
    c.outtime,
    c.admittime,
    c.dischtime,
    c.icu_los_hours,
    c.hosp_los_days,
    c.age_at_admit,
    c.died_in_hospital,

    -- v_vitals_24h (skip icustay_id)
    v.hr_mean_24h,
    v.hr_min_24h,
    v.hr_max_24h,
    v.sbp_mean_24h,
    v.sbp_min_24h,
    v.sbp_max_24h,
    v.spo2_mean_24h,
    v.spo2_min_24h,
    v.spo2_max_24h,
    v.resp_mean_24h,
    v.resp_min_24h,
    v.resp_max_24h,
    v.temp_mean_24h,
    v.temp_min_24h,
    v.temp_max_24h,
    v.gcs_mean_24h,
    v.gcs_min_24h,
    v.gcs_max_24h,
    v.n_vital_measurements_24h,

    -- v_labs_24h (skip icustay_id)
    l.hematocrit_mean_24h,
    l.hematocrit_min_24h,
    l.potassium_mean_24h,
    l.potassium_min_24h,
    l.potassium_max_24h,
    l.sodium_mean_24h,
    l.sodium_min_24h,
    l.sodium_max_24h,
    l.creatinine_mean_24h,
    l.creatinine_max_24h,
    l.chloride_mean_24h,
    l.bun_mean_24h,
    l.bun_max_24h,
    l.bicarb_mean_24h,
    l.bicarb_min_24h,
    l.anion_gap_mean_24h,
    l.anion_gap_max_24h,
    l.glucose_mean_24h,
    l.glucose_min_24h,
    l.glucose_max_24h,
    l.platelets_mean_24h,
    l.platelets_min_24h,
    l.hemoglobin_mean_24h,
    l.hemoglobin_min_24h,
    l.wbc_mean_24h,
    l.wbc_max_24h,
    l.mchc_mean_24h,
    l.mch_mean_24h,
    l.mcv_mean_24h,
    l.rbc_mean_24h,
    l.rbc_min_24h,
    l.rdw_mean_24h,
    l.rdw_max_24h,
    l.magnesium_mean_24h,
    l.magnesium_min_24h,
    l.calcium_mean_24h,
    l.calcium_min_24h,
    l.phosphate_mean_24h,
    l.phosphate_max_24h,
    l.n_lab_draws_24h,

    -- v_comorbidities (skip subject_id, hadm_id)
    co.has_hypertension,
    co.has_afib,
    co.has_aki,
    co.has_chf,
    co.has_diabetes,
    co.has_resp_failure,
    co.has_sepsis,
    co.has_anemia,
    co.has_cad,
    co.has_acidosis,
    co.n_diagnoses,

    -- v_prior_admissions (skip subject_id, hadm_id)
    pa.n_prior_admissions,

    -- v_vasopressors (COALESCE handles NULL for stays with no vasopressors)
    COALESCE(vp.vasopressor_flag, 0) AS vasopressor_flag

FROM v_cohort AS c
LEFT JOIN v_vitals_24h       AS v  ON c.icustay_id = v.icustay_id
LEFT JOIN v_labs_24h         AS l  ON c.icustay_id = l.icustay_id
LEFT JOIN v_comorbidities    AS co ON c.hadm_id    = co.hadm_id
LEFT JOIN v_prior_admissions AS pa ON c.hadm_id    = pa.hadm_id
LEFT JOIN v_vasopressors     AS vp ON c.icustay_id = vp.icustay_id;