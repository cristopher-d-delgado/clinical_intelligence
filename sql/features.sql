"""
Create 7 View Tables. 
1. v_cohort 
    Purpose: The base table. Every other view joins back to this one. 
    
    Source tables: 
        * icustays
        * admissions
        * patients   
    
    What it computes: 
        * ICU length of stay in hours using JULIANDAY() math
        * Hospital length of stay in days
        * Patient age of admission
        * Admission type, insurance, ethnicity, gender
        * The target variable - `died_in_hospital` as a 0/1 flag from `hospital_expire_flag`
        * Data of death for survival analysis
    
    Why it exists: Every ML row needs a spine and one row per ICU stay with the target label attached is the spine.

2. v_vitals_24h
    Purpose: Summarise vital signs in the first 24 hours of each ICU stay.
    
    Source tables: icustays, chartevents
    
    What it computes: For each vital sign — mean, min, max values within the 24-hour window. Also counts total vital measurements as a proxy for monitoring intensity.
    
    The time window: charttime BETWEEN intime AND DATETIME(intime, '+24 hours')
    
    Key concept — item IDs: MIMIC doesn't store `heart rate` as a column name. It stores a numeric itemid. Heart rate is itemid 211 (CareVue system) and 220045 (Metavision system). The SQL uses CASE WHEN itemid IN (211, 220045) to pull the right measurements regardless of which system recorded them.
    
    Why first 24 hours: Vital sign trends in the first 24 hours of ICU admission are strongly predictive of outcomes. Using a fixed window makes features comparable across patients with different length stays.

3. v_labs_24h
    Purpose: Same idea as vitals but for lab results.
    
    Source tables: icustays, labevents
    
    What it computes: Max, min, or mean of key lab values in the first 24 hours — creatinine, BUN, lactate, WBC, hemoglobin, platelets, sodium, potassium, bicarbonate, bilirubin. Also a binary flag for lactate > 4.0 which is a clinical threshold for severe sepsis.
    
    Same item ID concept: Creatinine is itemid 50912, lactate is 50813, etc.

    Why these labs: They cover the major organ systems — renal (creatinine, BUN), sepsis severity (lactate), infection (WBC), anaemia (hemoglobin), liver (bilirubin), electrolytes (sodium, potassium, bicarbonate).

4. v_comorbidites
    Purpose: Flag which chronic conditions each patient has on admission.
    
    Source table: diagnoses_icd
    
    What it computes: Binary 0/1 flags for CHF, diabetes, CKD, COPD, sepsis, cancer. Also total number of diagnoses as a complexity measure.
    
    How it works: ICD-9 codes are grouped by prefix. CHF codes all start with 428, diabetes with 250, CKD with 585 etc. LIKE '428%' catches all variants.
    
    Why MAX(): A patient can have multiple diagnosis rows. MAX(CASE WHEN ... THEN 1 ELSE 0 END) returns 1 if any row matches, 0 if none do — effectively an OR across all diagnosis rows for that patient.

5. v_prior_admissions
    Purpose: Count how many times each patient was admitted before this admission.
    
    Source table: admissions (self-join)
    
    How it works: Joins admissions to itself — a1 is the current admission, a2 is any earlier admission for the same patient where a2.admittime < a1.admittime. Counting a2 rows gives prior admissions.
    
    Why it matters: Prior admissions is one of the strongest predictors of readmission risk.

6. v_vasopressors
    Purpose: Flag whether vasopressors were used during the ICU stay.

    Source tables: inputevents_cv, inputevents_mv
    
    What it computes: A simple binary flag — 1 if any vasopressor was given, 0 if not.
    
    Why two tables: MIMIC-III has two medication input systems — CareVue (CV) and Metavision (MV). Each has its own table and its own item IDs for the same drugs. The UNION combines both so no patient is missed regardless of which system was used.

7. v_features
    Purpose: The master view — joins all 6 views into one flat table.
    
    What it computes: Nothing new. Just LEFT JOINs every view onto v_cohort using icustay_id or hadm_id as the key. LEFT JOIN means patients with no lab results, no vasopressors etc. still appear — their columns just come through as NULL which Python handles with median imputation.
    
    This is what Python queries: SELECT * FROM v_features is the single query that pulls the entire feature matrix into pandas.
"""
