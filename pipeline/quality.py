"""DQ-1: explicit, independently counted quality rules; no silent row repair."""
from pyspark.sql import functions as F

ENCOUNTER_COLUMNS = ['encounter_id', 'patient_id', 'facility_id', 'onset_date',
                     'report_date', 'age_years', 'sex', 'icd10_code',
                     'lab_confirmed', 'admitted', 'outcome']


def clean_encounters(raw, facilities, salt):
    for column in ENCOUNTER_COLUMNS:
        if column not in raw.columns:
            raw = raw.withColumn(column, F.lit(None).cast('string'))
    raw = raw.select(*ENCOUNTER_COLUMNS, '_batch_id', '_loaded_at', '_source_file')
    for c in ENCOUNTER_COLUMNS:
        raw = raw.withColumn(c, F.trim(F.col(c).cast('string')))
    refs = facilities.select('facility_id', 'district_id').dropDuplicates(['facility_id'])
    x = raw.join(F.broadcast(refs), 'facility_id', 'left')
    x = (x.withColumn('_onset', F.to_date('onset_date'))
         .withColumn('_report', F.to_date('report_date'))
         .withColumn('_age', F.col('age_years').cast('double')))
    rules = {
        'missing_encounter_id': F.col('encounter_id').isNull() | (F.length('encounter_id') == 0),
        'missing_patient_id': F.col('patient_id').isNull() | (F.length('patient_id') == 0),
        'missing_facility_id': F.col('facility_id').isNull() | (F.length('facility_id') == 0),
        'orphan_facility': F.col('district_id').isNull(),
        'invalid_geography': ~F.col('district_id').rlike(r'^(0[1-9]|[1-5][0-9]|6[0-4])$'),
        'invalid_onset_date': F.col('_onset').isNull(),
        'invalid_report_date': F.col('_report').isNull(),
        'onset_after_report': F.col('_onset') > F.col('_report'),
        'delay_above_21': F.datediff('_report', '_onset') > 21,
        'invalid_age': F.col('_age').isNull() | (F.col('_age') < 0) | (F.col('_age') > 110),
        'invalid_sex': ~F.col('sex').isin('M', 'F', 'O', 'U', 'male', 'female', 'other', 'unknown'),
        'invalid_icd10': ~F.col('icd10_code').rlike(r'^(A90|A91|A00|A09|J10|J11|B5[0-4]|Z00|R50)(\.[0-9A-Z]+)?$'),
        'invalid_lab_flag': ~F.lower('lab_confirmed').isin('true', 'false', '1', '0'),
        'invalid_admission_flag': ~F.lower('admitted').isin('true', 'false', '1', '0'),
        'invalid_outcome': ~F.lower('outcome').isin('recovered', 'died', 'death', 'ongoing', 'referred', 'unknown'),
    }
    errors = [F.when(F.coalesce(condition, F.lit(True)), F.lit(name)) for name, condition in rules.items()]
    x = x.withColumn('reason_codes', F.filter(F.array(*errors), lambda item: item.isNotNull()))
    x = (x.withColumn('patient_hash', F.sha2(F.concat(F.lit(salt), F.lit(':'), F.col('patient_id')), 256))
         .drop('patient_id', 'age_years')
         .withColumn('age_band', F.when(F.col('_age') < 5, '00-04')
                     .when(F.col('_age') < 15, '05-14').when(F.col('_age') < 25, '15-24')
                     .when(F.col('_age') < 45, '25-44').when(F.col('_age') < 65, '45-64')
                     .otherwise('65+'))
         .drop('onset_date', 'report_date', '_age')
         .withColumnRenamed('_onset', 'onset_date').withColumnRenamed('_report', 'report_date')
         .withColumn('admitted', F.lower('admitted').isin('true', '1'))
         .withColumn('lab_confirmed', F.lower('lab_confirmed').isin('true', '1'))
         .withColumn('disease', F.when(F.col('icd10_code').rlike('^A9[01]'), 'dengue')
                     .when(F.col('icd10_code').rlike('^A0[09]'), 'diarrhoea')
                     .when(F.col('icd10_code').rlike('^J1[01]'), 'influenza')
                     .when(F.col('icd10_code').rlike('^B5[0-4]'), 'malaria').otherwise('other')))
    return x, tuple(rules)
