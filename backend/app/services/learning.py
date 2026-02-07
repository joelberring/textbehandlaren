from backend.app.core.firebase import db
from backend.app.core.config import settings
from langchain_anthropic import ChatAnthropic
from datetime import datetime
from typing import List, Optional
import re

class LearningService:
    def __init__(self):
        self.llm = ChatAnthropic(
            model="claude-3-5-sonnet-20240620",
            anthropic_api_key=settings.ANTHROPIC_API_KEY,
            temperature=0
        )
        self._instruction_keywords = [
            "använd", "undvik", "skriv", "strukturera", "prioritera", "fokusera",
            "håll", "rubrik", "rubriker", "punktlista", "punktlistor", "källhänvisning",
            "kort", "kortfattat", "lång", "utförlig", "detaljerad", "ton", "formell",
            "saklig", "sammanfatta", "svenska"
        ]

    def _normalize_rule(self, text: str) -> str:
        rule = re.sub(r"\s+", " ", (text or "")).strip()
        if not rule:
            return ""
        rule = re.sub(r"^(kan du|jag vill att du|jag vill|från och med nu|framöver)\s+", "", rule, flags=re.IGNORECASE)
        rule = rule.strip(" -:;,.")
        if not rule:
            return ""
        if len(rule) > 180:
            rule = rule[:180].rstrip() + "..."
        if rule and not rule.endswith("."):
            rule += "."
        return rule[0].upper() + rule[1:] if rule else ""

    def _extract_explicit_rules(self, text: str) -> List[str]:
        if not text:
            return []
        candidates = re.split(r"[\n\r]+|(?<=[.!?])\s+", text)
        rules = []
        for c in candidates:
            s = re.sub(r"\s+", " ", c).strip()
            if len(s) < 12:
                continue
            lower = s.lower()
            if any(k in lower for k in self._instruction_keywords):
                norm = self._normalize_rule(s)
                if norm:
                    rules.append(norm)
        # dedupe while preserving order
        unique = []
        seen = set()
        for r in rules:
            key = r.lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(r)
        return unique[:8]

    def _merge_adaptive_rules(self, existing: List[dict], new_rules: List[str], cap: int = 20) -> List[dict]:
        now = datetime.utcnow()
        merged = {}

        for item in existing or []:
            if not isinstance(item, dict):
                continue
            rule = self._normalize_rule(item.get("rule", ""))
            if not rule:
                continue
            key = rule.lower()
            try:
                score = int(item.get("score", 1))
            except Exception:
                score = 1
            merged[key] = {
                "rule": rule,
                "score": max(1, min(score, 12)),
                "source": item.get("source", "auto"),
                "updated_at": item.get("updated_at", now),
            }

        for rule in new_rules:
            key = rule.lower()
            if key in merged:
                merged[key]["score"] = min(12, int(merged[key].get("score", 1)) + 2)
                merged[key]["updated_at"] = now
            else:
                merged[key] = {
                    "rule": rule,
                    "score": 3,
                    "source": "auto",
                    "updated_at": now,
                }

        ranked = list(merged.values())
        ranked.sort(key=lambda x: (int(x.get("score", 1)), str(x.get("updated_at", ""))), reverse=True)
        return ranked[:cap]

    async def capture_preferences_from_text(self, user_id: str, text: str, source: str = "query") -> List[dict]:
        new_rules = self._extract_explicit_rules(text)
        if not new_rules:
            return []

        user_pref_ref = db.collection("user_preferences").document(str(user_id))
        user_pref_doc = user_pref_ref.get()
        existing = []
        if user_pref_doc.exists:
            existing = user_pref_doc.to_dict().get("adaptive_style_memory", []) or []

        merged = self._merge_adaptive_rules(existing, new_rules, cap=20)
        user_pref_ref.set({
            "user_id": user_id,
            "adaptive_style_memory": merged,
            "adaptive_memory_last_source": source,
            "updated_at": datetime.utcnow()
        }, merge=True)
        return merged

    async def set_personal_style_rules(self, user_id: str, rules: List[str]) -> List[str]:
        cleaned = []
        seen = set()
        for rule in rules or []:
            norm = self._normalize_rule(rule)
            if not norm:
                continue
            key = norm.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(norm)

        cleaned = cleaned[:12]
        db.collection("user_preferences").document(str(user_id)).set({
            "user_id": user_id,
            "explicit_style_rules": cleaned,
            "updated_at": datetime.utcnow()
        }, merge=True)
        return cleaned

    async def learn_from_conversation(self, user_id: str, conversation_id: str):
        """
        Analyzes a conversation to extract stylistic preferences and updates the user's profile.
        """
        conv_ref = db.collection("conversations").document(conversation_id).get()
        if not conv_ref.exists:
            raise ValueError("Konversationen hittades inte.")

        conv_data = conv_ref.to_dict()
        messages = conv_data.get("messages", [])
        
        if len(messages) < 2:
            raise ValueError("Konversationen är för kort för att lära sig av. Skriv några meddelanden först!")

        # Format conversation for analysis
        conv_text = "\n".join([f"{m['role']}: {m['content']}" for m in messages])

        prompt = f"""Du är en expert på att analysera användarpreferenser i dokumentproduktion. 
Analysera följande dialog och identifiera hur användaren vill ha sina texter utformade (ton, struktur, detaljnivå, etc).

DIALOG:
{conv_text}

EXTRAHERA REGLER:
Skapa en lista med 3-5 korta, konkreta regler på svenska som beskriver användarens preferenser. 
Exempel: "Använd alltid punktlistor för sammanfattningar", "Håll tonen formell men personlig".
Svara ENDAST med listan, en per rad.
"""

        try:
            response = await self.llm.ainvoke(prompt)
        except Exception as e:
            print(f"Primary model in LearningService failed: {e}. Falling back to Haiku.")
            fallback_llm = ChatAnthropic(
                model="claude-3-haiku-20240307",
                anthropic_api_key=settings.ANTHROPIC_API_KEY,
                temperature=0
            )
            response = await fallback_llm.ainvoke(prompt)

        new_rules = [line.strip("- ").strip() for line in response.content.split("\n") if line.strip()]

        # Update user profile in Firestore
        user_pref_ref = db.collection("user_preferences").document(str(user_id))
        user_pref_doc = user_pref_ref.get()
        
        existing_rules = []
        if user_pref_doc.exists:
            existing_rules = user_pref_doc.to_dict().get("learned_style_rules", [])

        # Merge and deduplicate (simplified)
        combined_rules = list(set(existing_rules + new_rules))[:10]  # Keep top 10

        user_pref_ref.set({
            "user_id": user_id,
            "learned_style_rules": combined_rules,
            "updated_at": datetime.utcnow()
        }, merge=True)

        return combined_rules

    async def set_global_style_rules(self, rules: List[str]) -> List[str]:
        """
        Set global style rules that apply to all users.
        Only admins should call this.
        """
        db.collection("system_settings").document("global_styles").set({
            "global_style_rules": rules,
            "updated_at": datetime.utcnow()
        })
        return rules

    async def get_global_style_rules(self) -> List[str]:
        """Get the global style rules set by admins."""
        doc = db.collection("system_settings").document("global_styles").get()
        if doc.exists:
            return doc.to_dict().get("global_style_rules", [])
        return []

    async def get_combined_rules(self, user_id: str) -> dict:
        """
        Get both global and personal style rules for a user.
        Returns dict with global, explicit, learned and adaptive rules.
        """
        global_rules = await self.get_global_style_rules()
        
        learned_rules = []
        explicit_rules = []
        adaptive_memory = []
        user_pref_doc = db.collection("user_preferences").document(str(user_id)).get()
        if user_pref_doc.exists:
            pref = user_pref_doc.to_dict()
            learned_rules = pref.get("learned_style_rules", []) or []
            explicit_rules = pref.get("explicit_style_rules", []) or []
            adaptive_memory = pref.get("adaptive_style_memory", []) or []

        adaptive_memory = sorted(
            [x for x in adaptive_memory if isinstance(x, dict) and x.get("rule")],
            key=lambda x: (int(x.get("score", 1)), str(x.get("updated_at", ""))),
            reverse=True
        )[:8]
        adaptive_rules = [x.get("rule") for x in adaptive_memory if int(x.get("score", 1)) >= 2]

        personal_rules = []
        seen = set()
        for rule in (explicit_rules + learned_rules + adaptive_rules):
            key = str(rule).strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            personal_rules.append(rule)
        
        return {
            "global_rules": global_rules,
            "personal_rules": personal_rules,
            "explicit_rules": explicit_rules,
            "learned_rules": learned_rules,
            "adaptive_rules": adaptive_rules,
            "adaptive_memory": adaptive_memory
        }

learning_service = LearningService()
