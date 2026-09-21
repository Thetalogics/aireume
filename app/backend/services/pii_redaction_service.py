"""
PII Redaction Service for Transcript Analysis

Removes personally identifiable information (PII) from transcripts before analysis
to eliminate bias based on names, locations, organizations, and other identifiers.

Uses Presidio for enterprise-grade PII detection and anonymization.
"""
import logging
import re
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class RedactionResult:
    """Result of PII redaction operation."""
    redacted_text: str
    redaction_map: Dict[str, List[str]]
    redaction_count: int
    confidence_scores: Dict[str, float]


class PIIRedactionService:
    """
    Service for detecting and redacting PII from transcripts.
    
    Fallback implementation using regex patterns when Presidio is unavailable.
    For production, install: pip install presidio-analyzer presidio-anonymizer spacy
    """
    
    def __init__(self):
        self.use_presidio = False
        self.analyzer = None
        self.anonymizer = None
        
        try:
            import spacy
            from presidio_analyzer import AnalyzerEngine
            from presidio_analyzer.nlp_engine import NlpEngineProvider
            from presidio_anonymizer import AnonymizerEngine

            # Default AnalyzerEngine pulls en_core_web_lg (400MB) at request time.
            # The image only ships en_core_web_sm.
            if not spacy.util.is_package("en_core_web_sm"):
                raise RuntimeError("en_core_web_sm is not installed")
            nlp_engine = NlpEngineProvider(nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
            }).create_engine()
            self.analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["en"])
            self.anonymizer = AnonymizerEngine()
            self.use_presidio = True
            logger.info("Presidio PII redaction initialized with en_core_web_sm")
        except Exception as exc:
            logger.warning("Presidio unavailable (%s). Using regex PII redaction.", exc)
    
    def redact_pii(self, text: str) -> RedactionResult:
        """
        Redact PII from text while maintaining context for evaluation.
        
        Args:
            text: Original transcript text
            
        Returns:
            RedactionResult with redacted text and audit trail
        """
        if self.use_presidio:
            return self._redact_with_presidio(text)
        else:
            return self._redact_with_regex(text)
    
    def _redact_with_presidio(self, text: str) -> RedactionResult:
        """Redact using Presidio (enterprise-grade)."""
        try:
            # Analyze text for PII entities
            results = self.analyzer.analyze(
                text=text,
                entities=[
                    "PERSON",
                    "EMAIL_ADDRESS", 
                    "PHONE_NUMBER",
                    "LOCATION",
                    "ORG",
                    "URL",
                    "US_SSN",
                    "CREDIT_CARD",
                    # International PII entities
                    "UK_NHS_NUMBER",
                    "UK_NINO",         # UK National Insurance Number
                    "ES_NIF",          # Spain NIF
                    "ES_NIE",          # Spain NIE
                    "FR_CNI",          # France National ID
                    "IT_FISCAL_CODE",  # Italy Codice Fiscale
                    "IT_VAT_CODE",     # Italy VAT
                    "DE_VAT",          # Germany VAT
                    "SG_NRIC_FIN",     # Singapore NRIC/FIN
                    "IN_AADHAAR",      # India Aadhaar
                    "IN_PAN",          # India PAN
                    "AU_ABN",          # Australia ABN
                    "AU_TFN",          # Australia Tax File Number
                    "IBAN_CODE",       # International Bank Account Number
                    "IP_ADDRESS",      # IP addresses
                    "DATE_TIME",       # Dates that could be DOB
                ],
                language="en"
            )
            
            # Build redaction map for audit trail
            redaction_map = {}
            confidence_scores = {}
            
            for result in results:
                entity_type = result.entity_type
                original_value = text[result.start:result.end]
                
                if entity_type not in redaction_map:
                    redaction_map[entity_type] = []
                    confidence_scores[entity_type] = []
                
                redaction_map[entity_type].append(original_value)
                confidence_scores[entity_type].append(result.score)
            
            # Anonymize with context-preserving placeholders
            anonymized = self.anonymizer.anonymize(
                text=text,
                analyzer_results=results,
                operators={
                    "PERSON": {"type": "replace", "new_value": "CANDIDATE"},
                    "EMAIL_ADDRESS": {"type": "replace", "new_value": "EMAIL"},
                    "PHONE_NUMBER": {"type": "replace", "new_value": "PHONE"},
                    "LOCATION": {"type": "replace", "new_value": "LOCATION"},
                    "ORG": {"type": "replace", "new_value": "ORGANIZATION"},
                    "URL": {"type": "replace", "new_value": "URL"},
                    "US_SSN": {"type": "replace", "new_value": "SSN"},
                    "CREDIT_CARD": {"type": "replace", "new_value": "CARD"},
                }
            )
            
            # Calculate average confidence per entity type
            avg_confidence = {
                entity: sum(scores) / len(scores)
                for entity, scores in confidence_scores.items()
                if scores
            }
            
            return RedactionResult(
                redacted_text=anonymized.text,
                redaction_map=redaction_map,
                redaction_count=len(results),
                confidence_scores=avg_confidence
            )
            
        except Exception as e:
            logger.error(f"Presidio redaction failed: {e}. Falling back to regex.")
            return self._redact_with_regex(text)
    
    def _redact_with_regex(self, text: str) -> RedactionResult:
        """Fallback redaction using regex patterns."""
        redaction_map = {}
        redacted = text
        total_redactions = 0
        
        # Pattern: Email addresses
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        emails = re.findall(email_pattern, redacted)
        if emails:
            redaction_map["EMAIL_ADDRESS"] = emails
            redacted = re.sub(email_pattern, "EMAIL", redacted)
            total_redactions += len(emails)
        
        # Pattern: Phone numbers (various formats)
        phone_pattern = r'\b(?:\+?1[-.]?)?\(?([0-9]{3})\)?[-.]?([0-9]{3})[-.]?([0-9]{4})\b'
        phones = re.findall(phone_pattern, redacted)
        if phones:
            redaction_map["PHONE_NUMBER"] = [f"{p[0]}-{p[1]}-{p[2]}" for p in phones]
            redacted = re.sub(phone_pattern, "PHONE", redacted)
            total_redactions += len(phones)
        
        # International phone patterns
        intl_phone_pattern = r'\+(?:44|49|33|34|39|31|91|86|81|82|65|61|55|52|1)[-\s]?(?:\d[-\s]?){6,12}\d'
        intl_phones = re.findall(intl_phone_pattern, redacted)
        if intl_phones:
            redaction_map["INTL_PHONE"] = intl_phones
            redacted = re.sub(intl_phone_pattern, "PHONE", redacted)
            total_redactions += len(intl_phones)
        
        # UK National Insurance Number (e.g. AB123456C)
        uk_nino_pattern = r'\b[A-Z]{2}\d{6}[A-Z]\b'
        uk_ninos = re.findall(uk_nino_pattern, redacted)
        if uk_ninos:
            redaction_map["UK_NINO"] = uk_ninos
            redacted = re.sub(uk_nino_pattern, "UK_NINO", redacted)
            total_redactions += len(uk_ninos)
        
        # India Aadhaar (12 digits with spaces, e.g. 1234 5678 9012)
        aadhaar_pattern = r'\b\d{4}\s?\d{4}\s?\d{4}\b'
        aadhaars = re.findall(aadhaar_pattern, redacted)
        if aadhaars:
            redaction_map["IN_AADHAAR"] = aadhaars
            redacted = re.sub(aadhaar_pattern, "AADHAAR", redacted)
            total_redactions += len(aadhaars)
        
        # India PAN (e.g. ABCDE1234F)
        pan_pattern = r'\b[A-Z]{5}\d{4}[A-Z]\b'
        pans = re.findall(pan_pattern, redacted)
        if pans:
            redaction_map["IN_PAN"] = pans
            redacted = re.sub(pan_pattern, "PAN", redacted)
            total_redactions += len(pans)
        
        # Singapore NRIC/FIN (e.g. S1234567A)
        nric_pattern = r'\b[STFG]\d{7}[A-Z]\b'
        nrics = re.findall(nric_pattern, redacted)
        if nrics:
            redaction_map["SG_NRIC"] = nrics
            redacted = re.sub(nric_pattern, "NRIC", redacted)
            total_redactions += len(nrics)
        
        # IBAN (International Bank Account Number)
        iban_pattern = r'\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b'
        ibans = re.findall(iban_pattern, redacted)
        if ibans:
            redaction_map["IBAN"] = ibans
            redacted = re.sub(iban_pattern, "IBAN", redacted)
            total_redactions += len(ibans)
        
        # EU National ID patterns (various)
        eu_id_pattern = r'\b\d{10,12}[A-Z]?\b'  # Broad pattern for various EU IDs
        # Only match if preceded by common ID labels
        eu_id_labeled = r'(?:id|identity|national\s+id|nif|nie|cni|fiscal)\s*(?:number|no\.?|num[:#])?\s*[:.]?\s*(\b\d{8,12}[A-Z]?\b)'
        eu_ids = re.findall(eu_id_labeled, redacted, re.IGNORECASE)
        if eu_ids:
            redaction_map["EU_NATIONAL_ID"] = eu_ids
            redacted = re.sub(eu_id_labeled, r'EU_ID', redacted, flags=re.IGNORECASE)
            total_redactions += len(eu_ids)
        
        # IP addresses
        ip_pattern = r'\b(?:\d{1,3}\.){3}\d{1,3}\b'
        ips = re.findall(ip_pattern, redacted)
        if ips:
            redaction_map["IP_ADDRESS"] = ips
            redacted = re.sub(ip_pattern, "IP_ADDR", redacted)
            total_redactions += len(ips)
        
        # Pattern: URLs
        url_pattern = r'https?://[^\s]+'
        urls = re.findall(url_pattern, redacted)
        if urls:
            redaction_map["URL"] = urls
            redacted = re.sub(url_pattern, "URL", redacted)
            total_redactions += len(urls)
        
        # Pattern: Common name patterns (capitalized words, but preserve technical terms)
        # This is conservative - only redacts obvious name patterns
        name_pattern = r'\b(Mr\.|Mrs\.|Ms\.|Dr\.|Prof\.)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b'
        names = re.findall(name_pattern, redacted)
        if names:
            redaction_map["PERSON"] = [f"{title} {name}" for title, name in names]
            redacted = re.sub(name_pattern, r'\1 CANDIDATE', redacted)
            total_redactions += len(names)
        
        # Pattern: University names (common patterns)
        university_pattern = r'\b(University of [A-Z][a-z]+|[A-Z][a-z]+ University|MIT|Stanford|Harvard|Yale|Princeton)\b'
        universities = re.findall(university_pattern, redacted)
        if universities:
            redaction_map["ORG"] = universities
            redacted = re.sub(university_pattern, "UNIVERSITY", redacted)
            total_redactions += len(universities)
        
        # Pattern: Company names (ending with Inc, LLC, Corp, etc.)
        company_pattern = r'\b([A-Z][A-Za-z0-9]+(?:\s+[A-Z][A-Za-z0-9]+)*)\s+(Inc\.|LLC|Corp\.|Corporation|Ltd\.)\b'
        companies = re.findall(company_pattern, redacted)
        if companies:
            redaction_map["ORG"] = redaction_map.get("ORG", []) + [f"{name} {suffix}" for name, suffix in companies]
            redacted = re.sub(company_pattern, r'ORGANIZATION \2', redacted)
            total_redactions += len(companies)
        
        return RedactionResult(
            redacted_text=redacted,
            redaction_map=redaction_map,
            redaction_count=total_redactions,
            confidence_scores={}  # Regex doesn't provide confidence scores
        )
    
    def validate_redaction(self, original: str, redacted: str) -> Dict[str, any]:
        """
        Validate that redaction was successful and didn't remove critical content.
        
        Returns validation metrics.
        """
        original_words = set(original.lower().split())
        redacted_words = set(redacted.lower().split())
        
        # Calculate content preservation ratio
        preserved_words = original_words.intersection(redacted_words)
        preservation_ratio = len(preserved_words) / len(original_words) if original_words else 0
        
        # Check for placeholder presence
        placeholders = ["CANDIDATE", "EMAIL", "PHONE", "LOCATION", "ORGANIZATION", "URL"]
        placeholder_count = sum(redacted.count(p) for p in placeholders)
        
        return {
            "preservation_ratio": preservation_ratio,
            "original_word_count": len(original_words),
            "redacted_word_count": len(redacted_words),
            "placeholder_count": placeholder_count,
            "quality": "high" if preservation_ratio > 0.7 else "medium" if preservation_ratio > 0.5 else "low"
        }


# Singleton instance
_pii_service: Optional[PIIRedactionService] = None


def get_pii_service() -> PIIRedactionService:
    """Get or create singleton PII redaction service."""
    global _pii_service
    if _pii_service is None:
        _pii_service = PIIRedactionService()
    return _pii_service
