CREATE TABLE IF NOT EXISTS health.biomarker_dictionary (
    id SERIAL PRIMARY KEY,
    name VARCHAR(120) NOT NULL UNIQUE,
    unit VARCHAR(30) NOT NULL,
    range_min DECIMAL(10,4),
    range_max DECIMAL(10,4),
    category VARCHAR(60),
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS health.blood_test_events (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL DEFAULT 1,
    test_date DATE NOT NULL,
    lab_name VARCHAR(120),
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS health.biomarker_results (
    id SERIAL PRIMARY KEY,
    event_id INT NOT NULL REFERENCES health.blood_test_events(id) ON DELETE CASCADE,
    biomarker_id INT NOT NULL REFERENCES health.biomarker_dictionary(id),
    value DECIMAL(12,4) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
INSERT INTO health.biomarker_dictionary (name,unit,range_min,range_max,category) VALUES
  ('Vitamin D (25-OH)','nmol/L',75,200,'Vitamins'),
  ('Free Testosterone','pmol/L',174,729,'Hormones'),
  ('Total Testosterone','nmol/L',8.64,29,'Hormones'),
  ('SHBG','nmol/L',18.3,54.1,'Hormones'),
  ('Ferritin','µg/L',30,400,'Iron'),
  ('Serum Iron','µmol/L',11,30,'Iron'),
  ('TSH','mIU/L',0.27,4.2,'Thyroid'),
  ('Free T4','pmol/L',12,22,'Thyroid'),
  ('HbA1c','%',4,5.6,'Glucose'),
  ('Fasting Glucose','mmol/L',3.9,5.6,'Glucose'),
  ('Total Cholesterol','mmol/L',0,5.2,'Lipids'),
  ('LDL Cholesterol','mmol/L',0,3.0,'Lipids'),
  ('HDL Cholesterol','mmol/L',1.0,99,'Lipids'),
  ('Triglycerides','mmol/L',0,1.7,'Lipids'),
  ('CRP (hsCRP)','mg/L',0,1.0,'Inflammation'),
  ('Creatinine','µmol/L',62,115,'Kidney'),
  ('eGFR','mL/min',90,999,'Kidney'),
  ('ALT','U/L',7,56,'Liver'),
  ('AST','U/L',10,40,'Liver'),
  ('Haemoglobin','g/dL',13.5,17.5,'Haematology'),
  ('Cortisol (AM)','nmol/L',140,700,'Hormones'),
  ('IGF-1','nmol/L',11,36,'Hormones')
ON CONFLICT (name) DO NOTHING;
