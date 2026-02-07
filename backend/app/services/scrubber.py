from mistralai import Mistral
from backend.app.core.config import settings
import json
import re
from typing import Dict, List, Tuple

class ScrubberService:
    def __init__(self):
        self.client = Mistral(api_key=settings.MISTRAL_API_KEY) if settings.MISTRAL_API_KEY else None
        self.model = settings.GDPR_NAME_SCRUB_MODEL

    def _ensure_client(self):
        if not self.client:
            raise ValueError("Mistral API key is missing. Cannot run GDPR name scrubbing.")

    def is_configured(self) -> bool:
        return bool(self.client and settings.MISTRAL_API_KEY)

    def _looks_like_person_name(self, name: str) -> bool:
        n = re.sub(r"\s+", " ", (name or "").strip())
        if not n or "@" in n:
            return False
        if re.search(r"\d", n):
            return False

        blocked_terms = {
            "ab", "aktiebolag", "kommun", "region", "myndighet",
            "stadsbyggnadskontoret", "länsstyrelsen", "förvaltningen",
            "trafikverket", "stockholms stad", "sverige"
        }
        low = n.lower()
        if any(term in low for term in blocked_terms):
            return False

        parts = [p for p in re.split(r"\s+", n) if p]
        if len(parts) < 2 or len(parts) > 4:
            return False

        for p in parts:
            stripped = p.strip(".,;:()[]{}")
            if len(stripped) < 2:
                return False
            if stripped.isupper() and len(stripped) > 2:
                return False
        return True

    async def get_pii_map(self, text: str):
        if not settings.MISTRAL_API_KEY:
            return []

        prompt = f"""
        Identifiera alla personnamn och kontaktuppgifter (e-post, telefonnummer) i följande text.
        Returnera ENBART en JSON-lista på formatet:
        {{
            "findings": [
                {{"original": "Erik Svensson", "replacement": "[PERSON_1]"}},
                {{"original": "erik@example.com", "replacement": "[KONTAKT_1]"}}
            ]
        }}
        Om inga personuppgifter hittas, returnera en tom lista för findings.

        TEXT:
        {text}
        """

        try:
            response = self.client.chat.complete(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"}
            )
            data = json.loads(response.choices[0].message.content)
            return data.get("findings", [])
        except Exception as e:
            print(f"Mistral scrubbing failed: {e}")
            return []

    async def get_person_names(self, text: str, strict: bool = False) -> List[str]:
        """
        Extract only person names from text.
        Do not include organizations, places, addresses, titles, emails or phone numbers.
        """
        if not settings.MISTRAL_API_KEY:
            return []

        prompt = f"""
Identifiera ENDAST personnamn i texten nedan.
Regler:
- Returnera endast fulla namn på personer (för- och efternamn när möjligt).
- Ta INTE med organisationer, myndigheter, platsnamn, adresser, e-post, telefonnummer eller personnummer.
- Ta INTE med roller/titlar om de står utan namn.
- Om inga personnamn finns: returnera tom lista.

Svara ENBART som JSON-objekt med format:
{{
  "names": ["Förnamn Efternamn", "Anna Karlsson"]
}}

TEXT:
{text}
"""
        try:
            self._ensure_client()
            response = self.client.chat.complete(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"}
            )
            content = response.choices[0].message.content or "{}"
            data = json.loads(content)
            names = data.get("names", [])
            if not isinstance(names, list):
                return []
            cleaned = []
            for name in names:
                if not isinstance(name, str):
                    continue
                n = re.sub(r"\s+", " ", name.strip())
                if len(n) < 2:
                    continue
                if not self._looks_like_person_name(n):
                    continue
                cleaned.append(n)
            # Deduplicate while preserving order
            seen = set()
            dedup = []
            for n in cleaned:
                key = n.lower()
                if key not in seen:
                    seen.add(key)
                    dedup.append(n)
            return dedup
        except Exception as e:
            print(f"Mistral name extraction failed: {e}")
            if strict:
                raise
            return []

    def _replace_exact_name(self, text: str, name: str, replacement: str) -> Tuple[str, int]:
        """
        Replace exact name occurrences while avoiding partial word matches.
        """
        if not name:
            return text, 0
        pattern = re.compile(rf"(?<!\w){re.escape(name)}(?!\w)")
        return pattern.subn(replacement, text)

    async def scrub_person_names_with_cards(
        self,
        text: str,
        existing_map: Dict[str, str] = None,
        prefix: str = None
    ) -> Tuple[str, List[dict], Dict[str, str]]:
        """
        GDPR-safe name scrubbing:
        - identifies person names via Mistral (EU provider)
        - replaces ONLY names with document card identifiers
        - preserves all other content unchanged
        """
        if not text:
            return text, [], existing_map or {}
        if not settings.MISTRAL_API_KEY:
            raise ValueError("Mistral API key saknas för GDPR-namntvätt.")

        card_prefix = (prefix or settings.GDPR_CARD_PREFIX or "DOKUMENTKORT").strip()
        name_map = dict(existing_map or {})

        names = await self.get_person_names(text, strict=True)
        # Replace longer names first to avoid partial collisions.
        names = sorted(names, key=len, reverse=True)

        scrubbed = text
        findings = []

        for name in names:
            key = name.lower()
            if key not in name_map:
                card_id = f"[{card_prefix}_{len(name_map) + 1:04d}]"
                name_map[key] = card_id
            replacement = name_map[key]

            scrubbed, count = self._replace_exact_name(scrubbed, name, replacement)
            if count > 0:
                findings.append({
                    "type": "PERSON_NAME",
                    "original": name,
                    "replacement": replacement,
                    "occurrences": count
                })

        return scrubbed, findings, name_map

    async def scrub_text(self, text: str):
        findings = await self.get_pii_map(text)
        scrubbed_text = text
        
        # Sort by length descending to avoid partial replacements (e.g. "Erik Svensson" vs "Erik")
        findings.sort(key=lambda x: len(x["original"]), reverse=True)
        
        for item in findings:
            original = item["original"]
            replacement = item["replacement"]
            # Use regex to match exact words if possible, or simple replace
            # For robustness, we use escape as original might contain special chars
            pattern = re.escape(original)
            scrubbed_text = re.sub(pattern, replacement, scrubbed_text)
            
        return scrubbed_text, findings

scrubber_service = ScrubberService()
