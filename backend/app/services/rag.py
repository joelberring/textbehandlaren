from langchain_anthropic import ChatAnthropic
from backend.app.services.ingestion import ingestion_service
from backend.app.services.scrubber import scrubber_service
from backend.app.services.learning import learning_service
from backend.app.core.config import settings
from backend.app.core.firebase import db
from datetime import datetime
from google.cloud.firestore_v1.vector import Vector
from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
from backend.app.services.embeddings import get_embeddings
from backend.app.services.template_parser import parse_template, build_template_prompt
import os
import re
from typing import Dict, List

class RAGService:
    def __init__(self):
        self.default_primary_model = settings.LLM_DEFAULT_MODEL
        self.default_fallback_model = settings.LLM_FALLBACK_MODEL
        # Used to sanitize any LLM-produced citations so we never display fabricated source IDs.
        self._citation_id_re = re.compile(r"\bS\d+\b", re.IGNORECASE)

    def _normalize_and_filter_citations(self, text: str, allowed_source_ids: set, enable_citations: bool) -> str:
        """
        Enforce a strict citation format to prevent fabricated references.
        Allowed citations: [Sx] / (Sx) / bare Sx tokens, where Sx exists in allowed_source_ids.
        """
        if not text:
            return text

        out = str(text)

        # Convert common "Källa: ..." formats to plain [Sx] / (Sx)
        out = re.sub(
            r"\[\s*käll[aä][^\]]*?(S\d+)\s*\]",
            lambda m: f"[{m.group(1).upper()}]",
            out,
            flags=re.IGNORECASE
        )
        out = re.sub(
            r"\(\s*käll[aä][^)]*?(S\d+)\s*\)",
            lambda m: f"({m.group(1).upper()})",
            out,
            flags=re.IGNORECASE
        )

        if not enable_citations:
            # Remove any remaining citations if user disabled them.
            out = re.sub(r"\[\s*S\d+\s*\]", "", out, flags=re.IGNORECASE)
            out = re.sub(r"\(\s*S\d+\s*\)", "", out, flags=re.IGNORECASE)
            out = re.sub(r"\bS\d+\b", "", out, flags=re.IGNORECASE)
            out = re.sub(r"[ \t]{2,}", " ", out)
            out = re.sub(r"\n{3,}", "\n\n", out)
            return out.strip()

        # Drop unknown citations (hallucinated S-ids)
        def _keep_only_allowed_brackets(m):
            sid = (m.group(1) or "").upper()
            return f"[{sid}]" if sid in allowed_source_ids else ""
        out = re.sub(r"\[\s*(S\d+)\s*\]", _keep_only_allowed_brackets, out, flags=re.IGNORECASE)

        def _keep_only_allowed_paren(m):
            sid = (m.group(1) or "").upper()
            return f"({sid})" if sid in allowed_source_ids else ""
        out = re.sub(r"\(\s*(S\d+)\s*\)", _keep_only_allowed_paren, out, flags=re.IGNORECASE)

        out = re.sub(
            r"\b(S\d+)\b",
            lambda m: m.group(1).upper() if m.group(1).upper() in allowed_source_ids else "",
            out,
            flags=re.IGNORECASE
        )

        # Cleanup artifacts
        out = re.sub(r"\[\s*\]", "", out)
        out = re.sub(r"\(\s*\)", "", out)
        out = re.sub(r"[ \t]{2,}", " ", out)
        out = re.sub(r"\n{3,}", "\n\n", out)
        return out.strip()

    def _build_source_briefs(self, sources: list, max_sources: int = 18, excerpt_chars: int = 520) -> str:
        """
        Compact source view for verification prompts.
        The goal is to provide enough grounding signal without blowing up context size.
        """
        parts = []
        for s in (sources or [])[:max_sources]:
            meta = s.get("metadata", {}) or {}
            sid = (meta.get("source_ref") or s.get("source_ref") or "-").upper()
            filename = str(meta.get("filename") or "Okänd fil")
            page = meta.get("page")
            page_label = f"sida {page}" if page not in [None, "", 0, "0"] else "okänd sida"
            lib_name = str(meta.get("library_name") or "okänt bibliotek")
            lib_type = str(meta.get("library_type") or s.get("type") or "-")
            prio = meta.get("library_priority")
            prio_label = str(prio) if prio is not None else "-"

            excerpt = str(s.get("content") or "").strip()
            excerpt = re.sub(r"\s+", " ", excerpt)
            excerpt = excerpt[:excerpt_chars]
            parts.append(
                f"{sid}: {filename} • {page_label} • {lib_name} ({lib_type}, prio {prio_label})\n{excerpt}"
            )
        return "\n\n".join(parts) if parts else "Inga källor."

    async def _verify_and_ground_answer(
        self,
        query: str,
        draft_answer: str,
        sources: list,
        allowed_source_ids: set,
        show_citations: bool,
        learned_prefs: str,
        persona: str,
        primary_model: str,
        fallback_model: str,
        longform_mode: bool
    ) -> str:
        """
        Second-pass verifier that removes/rewrites any content that is not supported by the sources.
        This is intentionally strict to reduce hallucinations.
        """
        from langchain_core.messages import SystemMessage, HumanMessage

        briefs = self._build_source_briefs(
            sources,
            max_sources=min(24, max(12, len(sources or []))),
            excerpt_chars=520 if longform_mode else 420
        )
        allowed_list = ", ".join(sorted([sid for sid in (allowed_source_ids or set()) if sid])) or "-"

        citation_rule = (
            "KÄLLHÄNVISNINGAR: Använd endast [Sx] där Sx finns i listan över tillåtna käll-ID. "
            "Lägg källhänvisningar efter påståenden. Hitta aldrig på nya käll-ID."
        ) if show_citations else (
            "KÄLLHÄNVISNINGAR: Använd inga källhänvisningar i texten."
        )

        system_instr = f"""DU ÄR EN STRIKT FAKTAGRANSKARE.
Mål: Revidera utkastet så att ALLA sakpåståenden stöds av KÄLLUTDRAGEN. Du får INTE lägga till ny information.

Regler:
- Om ett påstående inte stöds av källutdragen: ta bort det eller skriv om det till en tydlig osäkerhet ("källmaterialet stödjer inte ...").
- HITTA ALDRIG PÅ fakta, siffror, datum, namn, slutsatser eller "notiser/OBS" som inte stöds.
- Behåll rubriker och disposition så långt det går, men prioritera korrekthet.
- Returnera endast den reviderade texten i markdown (inga förklaringar före/efter).

STIL (behåll så gott det går):
{learned_prefs or ""}

ASSISTENTENS ROLL/PERSONA:
{persona or ""}

{citation_rule}
"""

        human_instr = f"""FRÅGA/INSTRUKTION:
{query}

UTKAST (att granska):
{draft_answer}

TILLÅTNA KÄLL-ID:
{allowed_list}

KÄLLUTDRAG:
{briefs}
"""

        verify_max_tokens = 4096 if longform_mode else 2200
        resp = await self._invoke_with_fallback(
            [SystemMessage(content=system_instr), HumanMessage(content=human_instr)],
            max_tokens=verify_max_tokens,
            primary_model=primary_model,
            fallback_model=fallback_model
        )
        verified_raw = (resp.content or "").strip()
        verified_scrubbed, _output_findings = await scrubber_service.scrub_text(verified_raw)
        verified = self._normalize_and_filter_citations(
            verified_scrubbed,
            allowed_source_ids=allowed_source_ids,
            enable_citations=show_citations
        )
        return verified.strip()

    def _wants_longform(self, query: str, longform_flag: bool = None) -> bool:
        if longform_flag:
            return True
        lower = (query or "").lower()
        longform_terms = [
            "utförlig",
            "utförligt",
            "detaljerad",
            "detaljerat",
            "djupgående",
            "långt svar",
            "lång text",
            "sammanhängande text",
            "hela dokumentet",
            "fullständig",
            "fördjupad",
            "notebooklm"
        ]
        return any(term in lower for term in longform_terms)

    def _infer_target_words(
        self,
        query: str,
        target_pages: int = None,
        target_words: int = None,
        longform_flag: bool = None
    ) -> int:
        if target_words:
            return max(300, int(target_words))

        lower = (query or "").lower()
        words_match = re.search(r"(\d{3,5})\s*ord", lower)
        if words_match:
            return max(300, int(words_match.group(1)))

        pages_match = re.search(r"(\d+)\s*sidor", lower)
        if target_pages:
            return max(600, int(target_pages) * 450)
        if pages_match:
            return max(600, int(pages_match.group(1)) * 450)

        if self._wants_longform(lower, longform_flag):
            return 1500
        return None

    def _length_instruction(self, target_words: int = None) -> str:
        if target_words and target_words >= 2200:
            return f"Sikta på cirka {target_words} ord. Skriv djupt och sammanhängande, med tydlig rubrikstruktur."
        if target_words and target_words >= 1200:
            return f"Sikta på cirka {target_words} ord. Ge ett genomarbetat och välstrukturerat svar."
        if target_words and target_words >= 700:
            return f"Sikta på cirka {target_words} ord och använd flera underrubriker."
        return "Anpassa längden efter uppgiften. Om användaren ber om kort svar: prioritera precision före längd."

    def _is_simple_query(self, query: str) -> bool:
        q = (query or "").strip().lower()
        if not q:
            return False
        if len(q) > 110:
            return False
        complex_signals = [
            "utred", "analysera", "jämför", "fullständig", "fördjup", "lång", "utförlig",
            "disposition", "konsekvens", "samrådsredogörelse", "planbeskrivning"
        ]
        if any(s in q for s in complex_signals):
            return False
        question_like = ["vad", "vem", "var", "när", "hur", "kan du", "finns", "är det"]
        return any(s in q for s in question_like) or len(q.split()) <= 12

    def _pick_fast_model(self, selected_model: str, fallback_model: str, allowed_models: List[str]) -> str:
        if selected_model and "haiku" in selected_model:
            return selected_model
        for m in allowed_models:
            if "haiku" in m.lower():
                return m
        return fallback_model or selected_model

    def _allowed_chat_models(self) -> List[str]:
        parsed = [m.strip() for m in (settings.LLM_ALLOWED_MODELS or "").split(",") if m.strip()]
        if parsed:
            return parsed
        return [self.default_primary_model, self.default_fallback_model]

    def _sanitize_model(self, model_id: str, allowed_models: List[str], fallback_model: str) -> str:
        model = (model_id or "").strip()
        if model and model in allowed_models:
            return model
        return fallback_model

    def _resolve_models_for_assistant(self, asst_data: dict):
        allowed_models = self._allowed_chat_models()
        routing_doc = db.collection("system_settings").document("llm_routing").get()
        routing = routing_doc.to_dict() if routing_doc.exists else {}

        global_model = self._sanitize_model(
            routing.get("global_model"),
            allowed_models,
            self._sanitize_model(self.default_primary_model, allowed_models, allowed_models[0])
        )
        fallback_model = self._sanitize_model(
            routing.get("fallback_model"),
            allowed_models,
            self._sanitize_model(self.default_fallback_model, allowed_models, global_model)
        )

        allow_assistant_override = bool(routing.get("allow_assistant_override", True))
        selected_model = global_model
        selected_source = "global"

        assistant_model = (asst_data.get("model_preference") or "").strip()
        if allow_assistant_override and assistant_model and assistant_model in allowed_models:
            selected_model = assistant_model
            selected_source = "assistant"

        if selected_model == fallback_model:
            alternatives = [m for m in allowed_models if m != selected_model]
            if alternatives:
                fallback_model = alternatives[0]

        meta = {
            "selected_model": selected_model,
            "fallback_model": fallback_model,
            "selected_model_source": selected_source,
            "allow_assistant_override": allow_assistant_override,
            "allowed_models": allowed_models,
        }
        return selected_model, fallback_model, meta

    async def _invoke_with_fallback(self, messages, max_tokens: int = 4096, primary_model: str = None, fallback_model: str = None):
        primary = primary_model or self.default_primary_model
        fallback = fallback_model or self.default_fallback_model
        try:
            llm = ChatAnthropic(
                model=primary,
                anthropic_api_key=settings.ANTHROPIC_API_KEY,
                temperature=0,
                max_tokens=max_tokens
            )
            return await llm.ainvoke(messages)
        except Exception as e:
            print(f"Primary model {primary} failed: {e}. Falling back to {fallback}")
            fallback_llm = ChatAnthropic(
                model=fallback,
                anthropic_api_key=settings.ANTHROPIC_API_KEY,
                temperature=0,
                max_tokens=max_tokens
            )
            return await fallback_llm.ainvoke(messages)

    def _replace_first_matching_block(self, full_text: str, block_text: str, replacement: str):
        if block_text in full_text:
            return full_text.replace(block_text, replacement, 1), block_text

        candidates = [
            block_text.strip(),
            f"- {block_text.strip()}",
            f"* {block_text.strip()}",
            f"# {block_text.strip()}",
            f"## {block_text.strip()}",
            f"### {block_text.strip()}",
        ]
        for c in candidates:
            if c and c in full_text:
                return full_text.replace(c, replacement, 1), c
        return full_text, None

    def _parse_priority_profile(self, raw_profile) -> Dict[str, int]:
        profile = {}
        if not isinstance(raw_profile, list):
            return profile
        for item in raw_profile:
            if not isinstance(item, dict):
                continue
            lib_id = item.get("library_id")
            if not lib_id:
                continue
            try:
                prio = max(0, min(int(item.get("priority", 50)), 100))
            except Exception:
                prio = 50
            profile[lib_id] = prio
        return profile

    def _tokenize_query(self, text: str) -> List[str]:
        return re.findall(r"[a-z0-9åäö\-]{3,}", (text or "").lower())

    def _image_query_overlap_score(self, query_tokens: List[str], image_data: dict) -> int:
        if not query_tokens:
            return 0
        haystack_parts = []
        haystack_parts.extend(image_data.get("tags") or [])
        haystack_parts.extend(image_data.get("section_hints") or [])
        haystack_parts.append(image_data.get("description") or "")
        haystack_parts.append(image_data.get("context_excerpt") or "")
        haystack = " ".join([str(p) for p in haystack_parts]).lower()
        return sum(1 for token in query_tokens if token in haystack)

    async def _build_learned_prefs_block(self, user_id: str) -> str:
        combined = await learning_service.get_combined_rules(str(user_id))
        global_rules = combined.get("global_rules", []) or []
        explicit_rules = combined.get("explicit_rules", []) or []
        learned_rules = combined.get("learned_rules", []) or []
        adaptive_rules = combined.get("adaptive_rules", []) or []

        chunks = []
        if global_rules:
            chunks.append("GLOBALA STILREGLER (gäller alla):\n- " + "\n- ".join(global_rules))
        if explicit_rules:
            chunks.append("DINA LÅSTA PERSONLIGA REGLER:\n- " + "\n- ".join(explicit_rules))
        if learned_rules:
            chunks.append("DINA INLÄRDA STILPREFERENSER:\n- " + "\n- ".join(learned_rules))
        if adaptive_rules:
            chunks.append("DINA SENASTE ARBETSPREFERENSER (adaptivt minne):\n- " + "\n- ".join(adaptive_rules[:6]))
        return "\n\n".join(chunks)

    async def ask(self, query: str, assistant_id: str, conversation_id: str = None, 
                  custom_persona: str = None, show_citations: bool = True, 
                  user_id: str = None, project_id: str = None,
                  target_pages: int = None, target_words: int = None,
                  longform: bool = None, suggest_images: bool = True, response_mode: str = "auto",
                  progress_cb=None):
        if not user_id:
            user_id = "anonymous"

        if progress_cb:
            try:
                await progress_cb("preprocess", 2, "Förbereder fråga...")
            except Exception:
                pass
        
        # Fetch assistant metadata
        doc_ref = db.collection("assistants").document(assistant_id).get()
        if not doc_ref.exists:
            raise Exception("Assistant not found")
        
        asst_data = doc_ref.to_dict()
        library_ids = asst_data.get("library_ids", [])
        template_id = asst_data.get("template_id")
        persona = custom_persona or asst_data.get("system_prompt", settings.DEFAULT_PERSONA_PROMPT)
        assistant_priority_profile = self._parse_priority_profile(asst_data.get("library_priority_profile", []))
        selected_model, fallback_model, model_meta = self._resolve_models_for_assistant(asst_data)

        # Handle Template logic
        template_structure = ""
        if template_id:
            temp_ref = db.collection("templates").document(template_id).get()
            if temp_ref.exists:
                temp_data = temp_ref.to_dict()
                temp_path = temp_data.get("path")
                def _is_safe_template_path(path: str) -> bool:
                    try:
                        base = os.path.abspath("backend/app/templates") + os.sep
                        target = os.path.abspath(path)
                        return target.startswith(base) and target.lower().endswith(".docx")
                    except Exception:
                        return False

                if temp_path and os.path.exists(temp_path) and _is_safe_template_path(temp_path):
                    try:
                        parsed = parse_template(temp_path)
                        template_prompt = build_template_prompt(parsed)
                        if template_prompt:
                            template_structure = "\n" + template_prompt
                    except Exception as e:
                        print(f"Failed to parse template {template_id}: {e}")

        # V10: If project context, merge project libraries with assistant libraries
        project_context = ""
        if project_id:
            proj_ref = db.collection("projects").document(project_id).get()
            if proj_ref.exists:
                proj_data = proj_ref.to_dict()
                # Check user is member of project
                is_member = proj_data.get("owner_id") == user_id or any(
                    m.get("user_id") == user_id for m in proj_data.get("members", [])
                )
                if is_member:
                    # Combine project libraries with assistant libraries
                    project_libs = proj_data.get("library_ids", [])
                    library_ids = list(set(library_ids + project_libs))
                    project_context = f"\\n[PROJEKTARBETE: {proj_data.get('name', 'Okänt projekt')}]\\n"

        # Conversation attachments (bifogade filer i frågan)
        if conversation_id:
            attachment_library_id = None
            conv_ref = db.collection("conversations").document(conversation_id).get()
            if conv_ref.exists:
                conv_data = conv_ref.to_dict()
                if conv_data.get("user_id") == user_id:
                    attachment_library_id = conv_data.get("attachment_library_id")

            if not attachment_library_id:
                attach_ref = db.collection("conversation_attachments").document(conversation_id).get()
                if attach_ref.exists:
                    attach_data = attach_ref.to_dict()
                    if attach_data.get("user_id") == user_id:
                        attachment_library_id = attach_data.get("library_id")

            if attachment_library_id:
                library_ids = list(set(library_ids + [attachment_library_id]))

        # Fallback: if no libraries linked at all, use all of the user's own libraries
        if not library_ids and user_id:
            try:
                user_libs = db.collection("libraries").where("user_id", "==", user_id).stream()
                library_ids = [lib.id for lib in user_libs]
                if library_ids:
                    print(f"RAG fallback: using {len(library_ids)} user-owned libraries (no libraries linked to assistant)")
            except Exception as e:
                print(f"RAG fallback: failed to load user libraries: {e}")

        # Fetch learned user preferences (global + explicit + adaptive)
        learned_prefs = await self._build_learned_prefs_block(user_id)

        # GDPR Step 1: Scrub query first
        scrubbed_query, query_findings = await scrubber_service.scrub_text(query)
        all_findings = list(query_findings)
        response_mode = (response_mode or "auto").strip().lower()
        if response_mode not in ["auto", "fast", "standard", "deep"]:
            response_mode = "auto"

        target_words = self._infer_target_words(
            scrubbed_query,
            target_pages=target_pages,
            target_words=target_words,
            longform_flag=longform
        )
        longform_mode = self._wants_longform(scrubbed_query, longform) or bool(target_words and target_words >= 1200)
        simple_mode = (
            response_mode == "fast"
            or (response_mode == "auto" and self._is_simple_query(scrubbed_query) and not template_structure and not longform_mode)
        )
        if response_mode == "deep":
            longform_mode = True

        if progress_cb:
            try:
                await progress_cb("retrieval", 8, "Hämtar källor och bygger underlag...")
            except Exception:
                pass
        
        # Get Conversation History
        history = []
        current_draft = ""
        inline_texts = []
        if conversation_id:
            conv_doc = db.collection("conversations").document(conversation_id).get()
            if conv_doc.exists:
                conv_data = conv_doc.to_dict()
                if conv_data.get("user_id") == user_id:
                    history = conv_data.get("messages", [])
                    inline_texts = conv_data.get("attachment_inline_texts", [])
                    ai_messages = [m for m in history if m['role'] == 'ai']
                    if ai_messages:
                        current_draft = ai_messages[-1]['content']
            # If attachments are still processing and no inline text exists, return early
            try:
                attach_lib_id = None
                if conv_doc.exists:
                    attach_lib_id = conv_doc.to_dict().get("attachment_library_id")
                if not attach_lib_id:
                    attach_ref = db.collection("conversation_attachments").document(conversation_id).get()
                    if attach_ref.exists:
                        attach_lib_id = attach_ref.to_dict().get("library_id")
                if attach_lib_id and not inline_texts:
                    docs = db.collection("libraries").document(attach_lib_id).collection("documents").stream()
                    pending = [d.to_dict() for d in docs if d.to_dict().get("status") != "completed"]
                    if pending:
                        return {
                            "answer": "Filen/filerna bearbetas fortfarande. Vänta en stund och försök igen när status är klar.",
                            "sources": [],
                            "matched_images": [],
                            "scrubbed_query": scrubbed_query,
                            "pii_findings": all_findings
                        }
            except Exception as e:
                print(f"Attachment status check failed: {e}")

        # Selective Hybrid Retrieval
        all_context_chunks = []
        all_sources = []

        # Increase retrieval depth
        # Standard k=10, increased for long and template-bound generations
        if simple_mode:
            base_k = 4
        elif longform_mode and target_words and target_words >= 2000:
            base_k = 18
        elif longform_mode or (target_pages and target_pages >= 5) or template_structure:
            base_k = 15
        else:
            base_k = 10

        # Compute query embedding once and reuse across all libraries.
        query_vector = get_embeddings().embed_query(scrubbed_query)

        # Build library retrieval plan with explicit weighting (0-100 priority)
        library_plan = []
        for lib_id in library_ids:
            lib_ref = db.collection("libraries").document(lib_id).get()
            if not lib_ref.exists:
                continue
            
            lib_meta = lib_ref.to_dict()
            lib_priority = lib_meta.get("priority", 50)
            try:
                lib_priority = max(0, min(int(lib_priority), 100))
            except Exception:
                lib_priority = 50
            if lib_id in assistant_priority_profile:
                priority = assistant_priority_profile[lib_id]
                priority_source = "assistant_override"
            else:
                priority = lib_priority
                priority_source = "library_default"
            library_plan.append({
                "id": lib_id,
                "name": lib_meta.get("name", "Okänt bibliotek"),
                "type": lib_meta.get("library_type", "BACKGROUND"),
                "scrub": lib_meta.get("scrub_enabled", False),
                "priority": priority,
                "priority_source": priority_source,
                "library_default_priority": lib_priority
            })

        # Keep attachments and INPUT high by default if priority is equal.
        type_bias = {"ATTACHMENT_INLINE": 3, "INPUT": 2, "BACKGROUND": 1}
        library_plan.sort(
            key=lambda x: (x["priority"], type_bias.get(x["type"], 0)),
            reverse=True
        )

        def _k_for_priority(priority: int) -> int:
            if priority >= 85:
                return base_k
            if priority >= 70:
                return max(6, base_k - 2)
            if priority >= 50:
                return max(4, base_k - 4)
            return max(2, base_k - 6)

        # Global cap so low-priority libraries cannot drown high-priority material.
        # Also helps keep prompt sizes stable (better grounding, fewer hallucinations).
        if simple_mode:
            max_total_sources = 10
        elif longform_mode or template_structure:
            max_total_sources = 36
        else:
            max_total_sources = 24
        max_chunk_chars = 1400 if simple_mode else (2200 if (longform_mode or template_structure) else 1800)

        for lib in library_plan:
            if len(all_sources) >= max_total_sources:
                break
            lib_id = lib["id"]
            scrub_lib = lib["scrub"]
            lib_type = lib["type"]
            lib_name = lib["name"]
            lib_priority = lib["priority"]
            k = min(_k_for_priority(lib_priority), max_total_sources - len(all_sources))

            print(f"Searching library {lib_id} ({lib_type}, priority={lib_priority}) with k={k}...")
            source_docs = ingestion_service.search(scrubbed_query, [lib_id], k=k, query_vector=query_vector)
            print(f"Found {len(source_docs)} chunks in library {lib_id}")
            
            for doc in source_docs:
                text = doc.page_content
                if scrub_lib:
                    # Scrub content from sensitive libraries (Mistral integration)
                    text, findings = await scrubber_service.scrub_text(text)
                    all_findings.extend(findings)
                text = (text or "")[:max_chunk_chars]
                
                source_ref = f"S{len(all_sources) + 1}"
                metadata = dict(doc.metadata or {})
                metadata["library_id"] = lib_id
                metadata["library_name"] = lib_name
                metadata["library_type"] = lib_type
                metadata["library_priority"] = lib_priority
                metadata["library_priority_source"] = lib.get("priority_source", "library_default")
                metadata["source_ref"] = source_ref
                metadata.setdefault("filename", "Okänt dokument")

                label = f"[{lib_type}] {lib_name} (prio {lib_priority}) / {metadata.get('filename', 'Okänt dokument')}"
                all_context_chunks.append(f"KÄLLA {source_ref} ({label}):\n{text}")
                all_sources.append({
                    "source_ref": source_ref,
                    "content": text,
                    "metadata": metadata,
                    "type": lib_type
                })
                if progress_cb and len(all_sources) in [4, 8, 12, 18, 24, 32]:
                    try:
                        await progress_cb(
                            "retrieval",
                            min(20, 8 + int((len(all_sources) / max(1, max_total_sources)) * 12)),
                            f"Hämtar källor... ({len(all_sources)}/{max_total_sources})"
                        )
                    except Exception:
                        pass

        # Inline attachment text (direct read, no index)
        if inline_texts:
            for item in inline_texts:
                if len(all_sources) >= max_total_sources:
                    break
                try:
                    filename = item.get("filename", "Bifogad fil")
                    text = item.get("text", "")
                    if text:
                        text, findings = await scrubber_service.scrub_text(text)
                        all_findings.extend(findings)
                        text = (text or "")[:max_chunk_chars]
                        source_ref = f"S{len(all_sources) + 1}"
                        all_context_chunks.append(f"KÄLLA {source_ref} ([BIFOGAD FIL] {filename}):\n{text}")
                        all_sources.append({
                            "source_ref": source_ref,
                            "content": text,
                            "metadata": {
                                "filename": filename,
                                "inline": True,
                                "library_type": "ATTACHMENT_INLINE",
                                "library_name": "Konversationsbilaga",
                                "source_ref": source_ref
                            },
                            "type": "ATTACHMENT_INLINE"
                        })
                except Exception as e:
                    print(f"Inline attachment handling failed: {e}")
        context_text = "\n\n---\n\n".join(all_context_chunks)
        allowed_id_list = [s.get("source_ref") for s in all_sources if s.get("source_ref")]
        allowed_id_set = set(allowed_id_list)

        if progress_cb:
            try:
                await progress_cb("retrieval_done", 22, f"Källunderlag klart ({len(all_sources)} källutdrag).")
            except Exception:
                pass
        priority_lines = []
        for lib in library_plan[:12]:
            source_suffix = " (assistent)" if lib.get("priority_source") == "assistant_override" else ""
            priority_lines.append(
                f"- {lib['name']} ({lib['type']}), prioritet {lib['priority']}{source_suffix}"
            )
        library_priority_policy = "Ingen explicit biblioteksviktning tillgänglig."
        if priority_lines:
            library_priority_policy = (
                "BIBLIOTEKSPRIORITERING (högre värde = mer styrande vid konflikt):\n"
                + "\n".join(priority_lines)
            )
        if show_citations:
            citation_instr = (
                "KÄLLHÄNVISNINGAR:\n"
                "- Källhänvisningar är OBLIGATORISKA för sakpåståenden.\n"
                "- Använd ENDAST käll-ID i formatet [Sx] (exempel: [S3]).\n"
                "- Hitta ALDRIG på nya käll-ID (Sx).\n"
                "- Använd INGA filnamn i källhänvisningen.\n"
                "- Sätt källhänvisningen i slutet av meningen/stycket som stöds av källan.\n"
                "- Om flera källor stödjer ett påstående, lista dem: [S1][S4]."
            )
        else:
            citation_instr = "Använd INTE källhänvisningar i texten."

        if len(all_sources) == 0:
            return {
                "answer": "Jag hittar inga källor i dina bibliotek eller bifogade filer. Kontrollera att filerna är färdigbearbetade och försök igen.",
                "sources": [],
                "matched_images": [],
                "scrubbed_query": scrubbed_query,
                "pii_findings": all_findings
            }
        
        # V9: Image Retrieval
        matched_images = []
        image_needed_from_query = any(s in scrubbed_query.lower() for s in ["bild", "karta", "figur", "diagram"])
        if suggest_images and (not simple_mode or image_needed_from_query):
            matched_images = await self._search_images(scrubbed_query, library_plan)
        image_context = ""
        if matched_images:
            image_context = "\n\nRELEVANTA BILDER TILLGÄNGLIGA (kan bäddas in i export):\n" + "\n".join(
                [
                    f"- [BILD: {img['description'][:90]}...] (Källa: {img['source_document']}, sida {img['page']}, sektionstips: {', '.join(img.get('section_hints') or [])})"
                    for img in matched_images[:4]
                ]
            )
        

        # History window (20 messages)
        history_text = "\n".join([f"{m['role']}: {m['content'][:500]}..." for m in history[-20:]]) 

        from langchain_core.messages import SystemMessage, HumanMessage
        if simple_mode and not target_words:
            target_words = 220
        length_instruction = self._length_instruction(target_words)
        
        system_instr = f"""DIN IDENTITET OCH KÄRNINSTRUKTION:
{persona}

GROUNDING OCH SANNING (KRITISKT):
- Du får endast använda information som finns i de tillhandahållna REFERENSMATERIALEN nedan.
- Om du inte hittar svaret i källorna, säg: "Jag hittar tyvärr ingen information om detta i källmaterialet."
- HITTA ALDRIG PÅ FAKTA, SIFFROR, NAMN ELLER DATUM.
- Tidigare AI-utkast och dialoghistorik är arbetsmaterial, inte källor. Vid konflikt: följ referensmaterialet.
- Skapa ALDRIG källhänvisningar med käll-ID som inte finns i listan över tillåtna källor.
- Om en källa är otydlig, redovisa osäkerheten istället för att gissa.

STIL OCH FORMATERING:
{learned_prefs}
{citation_instr}

TILLÅTNA KÄLL-ID (använd endast dessa i [Sx]):
{", ".join(allowed_id_list)}
IMPORTANT: Om du redigerar ett befintligt utkast, behåll dess grundläggande struktur.
IMPORTANT: Om malltexten innehåller instruktioner eller fasta formuleringar, upprepa dem inte. Fyll endast i saklig text där det behövs.
ANVÄND MARKDOWN (# för rubriker, - för listor) för att strukturera ditt svar så att det kan formateras korrekt vid export.
{f"OM RELEVANTA BILDER FINNS: föreslå diskret placering i texten med rader i formatet [BILDFÖRSLAG: vad som ska visas | källa | sida | sektion]. Använd max 3 bildförslag och placera dem under relevanta rubriker." if suggest_images else "BILDFÖRSLAG ÄR AVSTÄNGT FÖR DETTA SVAR."}

SVARSARKITEKTUR (NotebookLM-liknande tydlighet):
- Tänk igenom svaret först och skriv sedan ett sammanhållet, genomarbetat svar.
- Inled med en tydlig H1-rubrik och en kort sammanfattning (2-5 meningar).
- Följ upp med flera H2/H3-rubriker i logisk ordning.
- Använd punktlistor där det förbättrar läsbarheten.
- Om svaret är långt: behåll röd tråd, undvik upprepningar och avsluta med tydligt ställningstagande/fortsatt arbete.
- Skriv aldrig ut hjälpord från mallar som "Rubrik:", "Underrubrik:", "Text" eller "Kursiv text".
{ "SNABBLÄGE: Ge ett kort, direkt och korrekt svar. Undvik onödig utfyllnad. Max cirka 220 ord om inte användaren ber om mer." if simple_mode else "" }

LÄNGDMÅL:
{length_instruction}

BIBLIOTEKSHIERARKI:
{library_priority_policy}
Vid motstridiga uppgifter: prioritera källor med högre biblioteksvärde, om inte användarens bifogade [INPUT]/[ATTACHMENT_INLINE] tydligt ska väga tyngre i frågan.

REFERENSMATERIAL (Använd detta för att hämta fakta):
{context_text}
{image_context}
{template_structure}
{project_context}

VIKTIGT: Om materialet ovan innehåller texter märkta som [INPUT] eller [ATTACHMENT_INLINE], betrakta dem som primära källor (användarens egna bifogade filer).
"""

        # Decide if we should do a two-step outline -> full text flow
        use_outline = (
            bool(template_structure)
            or len(scrubbed_query) > 180
            or "disposition" in scrubbed_query.lower()
            or longform_mode
            or bool(target_words and target_words >= 900)
        ) and not simple_mode

        if progress_cb:
            try:
                await progress_cb("generate", 28, "Startar textgenerering...")
            except Exception:
                pass

        if simple_mode:
            selected_model = self._pick_fast_model(selected_model, fallback_model, model_meta.get("allowed_models", []))
            model_meta["selected_model"] = selected_model
            model_meta["selected_model_source"] = "fast_mode"

        user_input = f"""Här är det aktuella utkastet eller kontexten:
{current_draft if current_draft else "Inget utkast än. Skapa ett nytt dokument baserat på källmaterialet."}

Dialoghistorik:
{history_text}

Min nya instruktion till dig:
{scrubbed_query}

{f"Längdmål: cirka {target_words} ord." if target_words else ""}

Uppdatera texten enligt instruktionen och returnera hela det uppdaterade dokumentet. Kom ihåg att följa din kärninstruktion ({persona}):"""

        messages = [
            SystemMessage(content=system_instr),
            HumanMessage(content=user_input)
        ]

        used_section_verification = False
        async def _generate_longform():
            nonlocal used_section_verification
            outline_user = f"""Skapa en disposition med rubriker (#, ##).
Målet är cirka {target_words or 1500} ord totalt. Ange ordmål per rubrik i parentes, t.ex. "## Bakgrund (800 ord)".
Om längdmålet är mycket långt (t.ex. > 6000 ord): dela upp i fler rubriker så att enskilda avsnitt inte blir extremt långa (sikta på ca 800–1400 ord per huvudavsnitt).
Följ mallstrukturen om den finns.
Returnera ENDAST dispositionen i markdown.

Fråga/instruktion:
{scrubbed_query}
"""
            outline_messages = [
                SystemMessage(content=system_instr),
                HumanMessage(content=outline_user)
            ]
            outline_resp = await self._invoke_with_fallback(
                outline_messages,
                max_tokens=2200,
                primary_model=selected_model,
                fallback_model=fallback_model
            )
            outline = outline_resp.content

            if progress_cb:
                try:
                    await progress_cb("outline", 34, "Disposition klar. Börjar skriva avsnitt...")
                except Exception:
                    pass

            headings = []
            for line in outline.splitlines():
                line = line.strip()
                if line.startswith("#"):
                    level = len(line) - len(line.lstrip("#"))
                    title = line.lstrip("#").strip()
                    title = re.sub(r"\(\s*\d+\s*ord\s*\)\s*$", "", title, flags=re.IGNORECASE).strip()
                    if title:
                        headings.append({"level": max(1, min(level, 3)), "title": title})

            if not headings:
                fallback_longform_user = f"""Skriv ett sammanhängande dokument med tydliga rubriker i markdown.
Sikta på cirka {target_words or 1500} ord.
Följ mallstrukturen om den finns och använd källhänvisningar när det behövs.

Fråga/instruktion:
{scrubbed_query}
"""
                fallback_messages = [
                    SystemMessage(content=system_instr),
                    HumanMessage(content=fallback_longform_user)
                ]
                fallback_resp = await self._invoke_with_fallback(
                    fallback_messages,
                    max_tokens=4096,
                    primary_model=selected_model,
                    fallback_model=fallback_model
                )
                return fallback_resp.content

            base_words = target_words or 1500
            per_section = max(220, int(base_words / max(1, len(headings))))
            sections_text = []
            done_titles = []
            verify_per_section = bool(target_words and target_words >= 3500)
            used_section_verification = verify_per_section
            outline_compact = "\n".join([("#" * h["level"]) + " " + h["title"] for h in headings])
            for idx, heading in enumerate(headings):
                title = heading["title"]
                level = heading["level"]
                prior_excerpt = "\n\n".join(sections_text).strip()
                if len(prior_excerpt) > 2800:
                    prior_excerpt = prior_excerpt[-2800:]
                prior_block = ""
                if prior_excerpt:
                    prior_block = f"Tidigare text (kort utdrag för kontinuitet):\n{prior_excerpt}\n"
                section_user = f"""Skriv avsnitt {idx + 1} av {len(headings)}: '{title}'.
Övergripande uppgift:
{scrubbed_query}

Disposition (för sammanhang):
{outline_compact}

Sikta på cirka {per_section} ord.
Bygg vidare på tidigare avsnitt utan upprepningar.
Redan skrivna rubriker: {", ".join(done_titles) if done_titles else "Inga"}.
Använd källhänvisningar där det behövs. Återge INTE instruktionstext.
Skriv inte rubriken igen i löptexten.
{prior_block}
Håll dig strikt till rubriken '{title}' och dispositionen; skriv inte innehåll som hör hemma under andra rubriker.
"""
                if progress_cb:
                    try:
                        pct = 34 + int(((idx) / max(1, len(headings))) * 46)
                        await progress_cb("writing", pct, f"Skriver avsnitt {idx + 1}/{len(headings)}: {title}")
                    except Exception:
                        pass
                section_messages = [
                    SystemMessage(content=system_instr),
                    HumanMessage(content=section_user)
                ]
                section_max_tokens = 4096 if per_section >= 1200 else (3200 if verify_per_section else 2400)
                sec_resp = await self._invoke_with_fallback(
                    section_messages,
                    max_tokens=section_max_tokens,
                    primary_model=selected_model,
                    fallback_model=fallback_model
                )
                body = sec_resp.content.strip()
                if body.startswith("#"):
                    first_line, _, rest = body.partition("\n")
                    normalized = first_line.lstrip("#").strip().lower().rstrip(":")
                    if normalized == title.lower().rstrip(":"):
                        body = rest.strip()

                if verify_per_section:
                    try:
                        if progress_cb:
                            await progress_cb("verify_section", 34 + int(((idx) / max(1, len(headings))) * 46) + 2, f"Verifierar avsnitt {idx + 1}/{len(headings)}...")
                        body = await self._verify_and_ground_answer(
                            query=f"{scrubbed_query}\n\nAVSNITT: {title}",
                            draft_answer=body,
                            sources=all_sources,
                            allowed_source_ids=allowed_id_set,
                            show_citations=show_citations,
                            learned_prefs=learned_prefs,
                            persona=persona,
                            primary_model=fallback_model or selected_model,
                            fallback_model=fallback_model,
                            longform_mode=False
                        )
                    except Exception as e:
                        print(f"Section verification failed ({title}): {e}")

                heading_prefix = "#" * level
                sections_text.append(f"{heading_prefix} {title}\n{body}")
                done_titles.append(title)
                if progress_cb:
                    try:
                        assembled = "\n\n".join(sections_text)
                        pct = 34 + int(((idx + 1) / max(1, len(headings))) * 46)
                        await progress_cb("writing", pct, f"Klart avsnitt {idx + 1}/{len(headings)}.", partial_answer=assembled)
                    except Exception:
                        pass

            return "\n\n".join(sections_text)

        if use_outline and (longform_mode or (target_words and target_words >= 1200)):
            answer = await _generate_longform()
        elif use_outline:
            outline_user = f"""Skapa först en tydlig DISPOSITION för dokumentet.
Följ strukturen i mallens rubriker om de finns. Returnera endast dispositionen i markdown med rubriker (#, ##, ###).

Fråga/instruktion:
{scrubbed_query}
"""
            outline_messages = [
                SystemMessage(content=system_instr),
                HumanMessage(content=outline_user)
            ]
            outline_resp = await self._invoke_with_fallback(
                outline_messages,
                max_tokens=1800,
                primary_model=selected_model,
                fallback_model=fallback_model
            )
            outline = outline_resp.content
            if progress_cb:
                try:
                    await progress_cb("outline", 34, "Disposition klar. Fyller med full text...")
                except Exception:
                    pass

            full_user = f"""Här är dispositionen:
{outline}

Fyll nu dispositionen med fullständig text baserat på källmaterialet. 
Använd källhänvisningar där det behövs och följ mallens struktur och ton.
{f"Sikta på cirka {target_words} ord totalt." if target_words else ""}

Utkast/kontext:
{current_draft if current_draft else "Inget utkast än. Skapa ett nytt dokument baserat på källmaterialet."}

Dialoghistorik:
{history_text}

Ny instruktion:
{scrubbed_query}

Returnera hela dokumentet i markdown."""
            full_messages = [
                SystemMessage(content=system_instr),
                HumanMessage(content=full_user)
            ]
            response = await self._invoke_with_fallback(
                full_messages,
                max_tokens=4096,
                primary_model=selected_model,
                fallback_model=fallback_model
            )
            answer = response.content
        else:
            response = await self._invoke_with_fallback(
                messages,
                max_tokens=1200 if simple_mode else 4096,
                primary_model=selected_model,
                fallback_model=fallback_model
            )
            answer = response.content

        if progress_cb:
            try:
                await progress_cb("verify", 82, "Kvalitetssäkrar mot källor...")
            except Exception:
                pass
        
        # Scrub AI output to prevent PII leakage in generated text
        scrubbed_answer, output_findings = await scrubber_service.scrub_text(answer)
        all_findings.extend(output_findings)
        scrubbed_answer = self._normalize_and_filter_citations(scrubbed_answer, allowed_id_set, enable_citations=show_citations)

        verified = False
        # Second-pass verification to aggressively remove hallucinations.
        # We skip only in explicit fast mode; auto/standard/deep are verified.
        # For very long answers we do per-section verification instead (global verification won't fit context).
        if response_mode != "fast" and not used_section_verification and settings.ENVIRONMENT != "production":
            try:
                verify_primary = selected_model
                # Use the cheaper model for verification unless user explicitly asked for deep/longform/template work.
                if not (response_mode == "deep" or longform_mode or template_structure):
                    verify_primary = fallback_model or selected_model
                scrubbed_answer = await self._verify_and_ground_answer(
                    query=scrubbed_query,
                    draft_answer=scrubbed_answer,
                    sources=all_sources,
                    allowed_source_ids=allowed_id_set,
                    show_citations=show_citations,
                    learned_prefs=learned_prefs,
                    persona=persona,
                    primary_model=verify_primary,
                    fallback_model=fallback_model,
                    longform_mode=bool(longform_mode)
                )
                verified = True
            except Exception as e:
                print(f"Answer verification failed: {e}")

        if progress_cb:
            try:
                await progress_cb("finalize", 95, "Slutför och sparar konversation...")
            except Exception:
                pass

        # Hard safety: if citations are enabled and we still cannot produce any valid citations,
        # return a conservative message instead of an untraceable answer.
        if show_citations and allowed_id_set:
            has_citation = bool(re.search(r"(\\[\\s*S\\d+\\s*\\]|\\(\\s*S\\d+\\s*\\))", scrubbed_answer or "", flags=re.IGNORECASE))
            if not has_citation:
                scrubbed_answer = (
                    "Jag kan tyvärr inte skapa ett källbelagt svar utifrån underlaget just nu. "
                    "Prova att omformulera frågan mer specifikt eller ladda upp mer relevant material."
                )

        # Save to history
        if conversation_id:
            conv_doc = db.collection("conversations").document(conversation_id).get()
            title = ""
            if conv_doc.exists:
                title = conv_doc.to_dict().get("title", scrubbed_query[:50] + "...")
            else:
                title = scrubbed_query[:50] + "..."

            history_sources = []
            for s in all_sources[:12]:
                meta = s.get("metadata", {})
                history_sources.append({
                    "source_ref": s.get("source_ref") or meta.get("source_ref"),
                    "type": s.get("type"),
                    "metadata": {
                        "filename": meta.get("filename"),
                        "page": meta.get("page"),
                        "library_id": meta.get("library_id"),
                        "library_name": meta.get("library_name"),
                        "library_type": meta.get("library_type"),
                        "library_priority": meta.get("library_priority"),
                        "library_priority_source": meta.get("library_priority_source"),
                        "doc_id": meta.get("doc_id")
                    },
                    # Enough to be useful in the UI/exports without risking Firestore doc size.
                    "content": (s.get("content") or "")[:520]
                })
            history_images = matched_images[:6] if matched_images else []

            new_messages = history + [
                {"role": "user", "content": scrubbed_query},
                {
                    "role": "ai",
                    "content": scrubbed_answer,
                    "sources": history_sources,
                    "matched_images": history_images
                }
            ]
            db.collection("conversations").document(conversation_id).set({
                "assistant_id": assistant_id,
                "user_id": user_id,
                "project_id": project_id,
                "title": title,
                "messages": new_messages,
                "updated_at": datetime.utcnow()
            })

        if progress_cb:
            try:
                await progress_cb("completed", 100, "Klar.")
            except Exception:
                pass

        return {
            "answer": scrubbed_answer,
            "sources": all_sources,
            "matched_images": matched_images,
            "scrubbed_query": scrubbed_query,
            "pii_findings": all_findings,
            "debug": {
                "source_count": len(all_sources),
                "k": base_k,
                "max_total_sources": max_total_sources,
                "max_chunk_chars": max_chunk_chars,
                "context_length": len(context_text),
                "target_words": target_words,
                "longform_mode": longform_mode,
                "use_outline": use_outline,
                "library_plan": library_plan,
                "suggest_images": suggest_images,
                "response_mode": response_mode,
                "simple_mode": simple_mode,
                "verified": verified,
                "model": {
                    "primary": selected_model,
                    "fallback": fallback_model,
                    **model_meta
                }
            }
        }

    async def edit_block(
        self,
        assistant_id: str,
        conversation_id: str,
        full_text: str,
        block_text: str,
        comment: str,
        user_id: str = None,
        project_id: str = None
    ):
        if not user_id:
            user_id = "anonymous"
        if not full_text or not full_text.strip():
            raise ValueError("full_text saknas.")
        if not block_text or not block_text.strip():
            raise ValueError("block_text saknas.")
        if not comment or not comment.strip():
            raise ValueError("comment saknas.")

        doc_ref = db.collection("assistants").document(assistant_id).get()
        if not doc_ref.exists:
            raise Exception("Assistant not found")
        asst_data = doc_ref.to_dict()
        persona = asst_data.get("system_prompt", settings.DEFAULT_PERSONA_PROMPT)
        selected_model, fallback_model, _model_meta = self._resolve_models_for_assistant(asst_data)

        learned_prefs = await self._build_learned_prefs_block(user_id)

        latest_sources = []
        latest_images = []
        history = []
        title = "Styckesredigering"
        conv_doc = None
        if conversation_id:
            conv_doc = db.collection("conversations").document(conversation_id).get()
            if conv_doc.exists:
                conv_data = conv_doc.to_dict()
                if conv_data.get("user_id") == user_id:
                    history = conv_data.get("messages", [])
                    title = conv_data.get("title", title)
                    for m in reversed(history):
                        if m.get("role") == "ai":
                            latest_sources = m.get("sources", []) or []
                            latest_images = m.get("matched_images", []) or []
                            break

        source_context_lines = []
        for s in latest_sources[:10]:
            meta = s.get("metadata", {}) or {}
            source_context_lines.append(
                f"{meta.get('source_ref', s.get('source_ref', '-'))}: "
                f"{meta.get('filename', 'Okänd fil')} "
                f"(bibliotek: {meta.get('library_name', 'okänt')}, sida: {meta.get('page', '-')})\n"
                f"{(s.get('content') or '')[:300]}"
            )
        source_context = "\n\n".join(source_context_lines) if source_context_lines else "Inga källor sparade."

        project_context = ""
        if project_id:
            proj_ref = db.collection("projects").document(project_id).get()
            if proj_ref.exists:
                proj_data = proj_ref.to_dict()
                project_context = f"Projekt: {proj_data.get('name', 'Okänt projekt')}"

        from langchain_core.messages import SystemMessage, HumanMessage
        system_instr = f"""DIN ROLL:
{persona}

DU SKA ENDAST REDIGERA ETT MARKERAT TEXTBLOCK.
Regler:
- Returnera ENDAST den reviderade versionen av blocket i markdown.
- Ändra inte andra delar av dokumentet.
- Behåll saklighet, ton, källhänvisningar och format.
- Om kommentaren är oklar: gör minsta möjliga ändring.
- HITTA ALDRIG PÅ nya fakta. Om källstödet inte räcker: gör minsta möjliga ändring och gissa inte.
- Använd endast käll-ID som faktiskt finns i KÄLLSTÖD (Sx). Hitta inte på nya käll-ID.
- Lägg aldrig till förklaringar före eller efter blocket.

STIL:
{learned_prefs}
"""
        human_instr = f"""HELA DOKUMENTET (för kontext):
{full_text}

PROJEKTKONTEXT:
{project_context}

MARKERAT BLOCK (detta block ska redigeras):
{block_text}

KÄLLSTÖD:
{source_context}

KOMMENTAR FRÅN ANVÄNDAREN:
{comment}
"""
        response = await self._invoke_with_fallback(
            [SystemMessage(content=system_instr), HumanMessage(content=human_instr)],
            max_tokens=1800,
            primary_model=selected_model,
            fallback_model=fallback_model
        )
        revised_block_raw = (response.content or "").strip()
        revised_block, output_findings = await scrubber_service.scrub_text(revised_block_raw)
        allowed_ids = {s.get("source_ref") for s in (latest_sources or []) if s.get("source_ref")}
        revised_block = self._normalize_and_filter_citations(revised_block, allowed_ids, enable_citations=True)
        revised_block = revised_block.strip() or block_text

        updated_text, matched_block = self._replace_first_matching_block(full_text, block_text, revised_block)
        if not matched_block:
            raise ValueError("Kunde inte hitta markerat block i texten. Markera ett tydligare stycke.")

        if conversation_id:
            user_msg = f"[Styckekommentar]\nBlock: {block_text[:180]}\nKommentar: {comment}"
            new_messages = history + [
                {"role": "user", "content": user_msg},
                {
                    "role": "ai",
                    "content": updated_text,
                    "sources": latest_sources,
                    "matched_images": latest_images
                }
            ]
            db.collection("conversations").document(conversation_id).set({
                "assistant_id": assistant_id,
                "user_id": user_id,
                "project_id": project_id,
                "title": title,
                "messages": new_messages,
                "updated_at": datetime.utcnow()
            }, merge=True)

        return {
            "answer": updated_text,
            "edited_block": revised_block,
            "sources": latest_sources,
            "matched_images": latest_images,
            "pii_findings": output_findings
        }

    async def _search_images(self, query: str, library_plan: list, k: int = 4) -> list:
        """Search for semantically relevant images in the specified libraries."""
        if not library_plan:
            return []
        
        embeddings = get_embeddings()
        query_vector = embeddings.embed_query(query)
        query_tokens = self._tokenize_query(query)
        found_images = []
        
        for lib in library_plan:
            lib_id = lib.get("id")
            if not lib_id:
                continue
            lib_priority = lib.get("priority", 50)
            try:
                results = db.collection("image_assets").where("library_id", "==", lib_id).find_nearest(
                    vector_field="embedding",
                    query_vector=Vector(query_vector),
                    distance_measure=DistanceMeasure.COSINE,
                    limit=max(3, k)
                ).get()
                
                for doc in results:
                    data = doc.to_dict()
                    overlap = self._image_query_overlap_score(query_tokens, data)
                    priority_bonus = int(lib_priority / 20)
                    rank_score = overlap + priority_bonus
                    found_images.append({
                        "id": data.get("id"),
                        "url": data.get("url"),
                        "description": data.get("description"),
                        "tags": data.get("tags", []),
                        "section_hints": data.get("section_hints", []),
                        "context_excerpt": data.get("context_excerpt"),
                        "source_doc_id": data.get("source_doc_id"),
                        "library_id": lib_id,
                        "library_priority": lib_priority,
                        "source_document": data.get("source_document"),
                        "page": data.get("page"),
                        "_rank_score": rank_score
                    })
            except Exception as e:
                print(f"Image search failed for library {lib_id}: {e}")
                continue
        found_images.sort(key=lambda x: x.get("_rank_score", 0), reverse=True)
        unique = []
        seen = set()
        for img in found_images:
            key = img.get("id") or f"{img.get('source_document')}::{img.get('page')}"
            if key in seen:
                continue
            seen.add(key)
            img.pop("_rank_score", None)
            unique.append(img)
            if len(unique) >= k:
                break
        return unique

rag_service = RAGService()
