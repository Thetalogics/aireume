# Bias Evaluation

ARIA does not claim to be bias-free, fair, or certified against any employment-discrimination standard.

Deterministic scoring consumes structured resume/JD features: skills, experience, architecture, education, timeline, domain, and risk. It does not consume race, religion, sex, sexual orientation, disability, or political affiliation.

Name, email, phone, and address are excluded from scoring inputs. Changing those fields alone must not change the deterministic score.

Known proxy risks remain: school names, employers, locations, and language inferred from resume text can still correlate with protected groups. Human review is required before employment action.

Fairness testing currently covers identity/contact independence and prompt-injection score independence. Broader demographic fairness is not proven.
