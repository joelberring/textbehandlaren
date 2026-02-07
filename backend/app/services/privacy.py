import re

class PrivacyService:
    def __init__(self):
        # Regex for Swedish Social Security Number (Personnummer)
        # Supports: YYYYMMDD-XXXX, YYMMDD-XXXX, YYYYMMDDXXXX, YYMMDDXXXX
        self.ssn_pattern = re.compile(r'\b(19|20)?(\d{2})(\d{2})(\d{2})[-+]?(\d{4})\b')
        
        # Simple email regex
        self.email_pattern = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')
        
        # Simple phone regex (Swedish style)
        self.phone_pattern = re.compile(r'\b(07[02369]\s?-?\d{2}\s?-?\d{2}\s?-?\d{3}|0\d{1,2}\s?-?\d{2}\s?-?\d{2}\s?-?\d{1,2})\b')

    def scan_text(self, text: str) -> dict:
        findings = []
        
        if self.ssn_pattern.search(text):
            findings.append("Social Security Number (Personnummer)")
        if self.email_pattern.search(text):
            findings.append("Email Address")
        if self.phone_pattern.search(text):
            findings.append("Phone Number")
            
        return {
            "is_sensitive": len(findings) > 0,
            "findings": findings
        }

    def mask_pii(self, text: str) -> str:
        # Masking logic
        masked = self.ssn_pattern.sub("[PERSONNUMMER]", text)
        masked = self.email_pattern.sub("[EMAIL]", masked)
        masked = self.phone_pattern.sub("[TELEFON]", masked)
        return masked

privacy_service = PrivacyService()
