Test inputs for the three demo cases (synthetic data)

shared/ (same for all three cases)
  report-template.docx      four sections, one fixed statement, {{placeholders}}
  protocol.json             5 planned samples S-001 to S-005, range 2.40 to 2.60 mm
  reference/*.md            3 reference documents
  calibration-record.pdf    supporting attachment (Attachment A)

Case 1, complete:   shared/* + case-1-complete/results.csv. Expect 5 of 5 pass, no warnings.
Case 2, failing:    shared/* + case-2-failing/results.csv. S-003 is 2.71 mm, expect 1 Fail, in report, summary and Excel.
Case 3, incomplete: shared/* EXCEPT calibration-record.pdf + case-3-incomplete/results.csv (S-004 removed).
                    Do not upload the PDF. Expect warnings for missing S-004 and missing calibration record.
