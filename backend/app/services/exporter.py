from docxtpl import DocxTemplate, InlineImage
from docx import Document
from docx.shared import Mm
from docx.text.paragraph import Paragraph
from docx.oxml import OxmlElement
import os
import io
import re
import requests
from datetime import datetime
from backend.app.services.template_parser import is_heading, is_instruction

class ExporterService:
    _IMAGE_SUGGESTION_RE = re.compile(r"\[BILDFÖRSLAG:\s*(.*?)\]", re.IGNORECASE)

    def __init__(self):
        self.output_dir = "exports"
        self.template_dir = "backend/app/templates"
        self.template_path = os.path.join(self.template_dir, "template.docx")
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.template_dir, exist_ok=True)
        
        # Ensure a template exists
        if not os.path.exists(self.template_path):
            self._create_default_template()

    def _create_default_template(self):
        doc = Document()
        doc.add_heading('{{ title }}', 0)
        doc.add_paragraph('Datum: {{ date }}')
        
        doc.add_heading('Fråga', level=1)
        doc.add_paragraph('{{ query }}')
        
        doc.add_heading('Svar', level=1)
        doc.add_paragraph('{{ answer }}')
        
        doc.add_heading('Källhänvisningar', level=1)
        doc.add_paragraph('{% for source in sources %}')
        p = doc.add_paragraph('• Källa: {{ source.filename }} ({{ source.page }})', style='List Bullet')
        doc.add_paragraph('  {{ source.snippet }}')
        doc.add_paragraph('{% endfor %}')
        
        # V9: Images section placeholder
        doc.add_heading('Bilagor (Bilder)', level=1)
        doc.add_paragraph('{% for img in images %}')
        doc.add_paragraph('{{ img.inline_image }}')
        doc.add_paragraph('Beskrivning: {{ img.description }}')
        doc.add_paragraph('{% endfor %}')
        
        doc.save(self.template_path)

    def _template_has_placeholders(self, template_path: str, names: list) -> bool:
        try:
            doc = Document(template_path)
        except Exception:
            return False

        def has_name(text: str) -> bool:
            for name in names:
                if f"{{{{ {name} }}}}" in text or f"{{{{{name}}}}}" in text:
                    return True
            return False

        for p in doc.paragraphs:
            if has_name(p.text):
                return True
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    if has_name(cell.text):
                        return True
        return False

    def _extract_sections(self, markdown_text: str):
        sections = []
        current = None
        for line in markdown_text.splitlines():
            line = line.strip()
            if not line:
                if current:
                    current["content"] += "\n"
                continue
            if line.startswith("#"):
                # close previous
                if current:
                    sections.append(current)
                title = line.lstrip("#").strip()
                current = {"title": title, "content": ""}
            else:
                if current is None:
                    # preamble before first heading
                    current = {"title": "Inledning", "content": ""}
                current["content"] += line + "\n"
        if current:
            sections.append(current)
        return sections

    def _slugify(self, text: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower())
        slug = re.sub(r"_+", "_", slug).strip("_")
        return slug or "section"

    def _append_source_appendix(self, doc, sources: list):
        if not sources:
            return

        available_styles = [s.name for s in doc.styles if s.type == 1]
        h1_style = next((s for s in ['Rubrik 1 med numrering', 'Rubrik 1', 'Huvudrubrik', 'Heading 1'] if s in available_styles), 'Heading 1')
        h2_style = next((s for s in ['Rubrik 2 med numrering', 'Rubrik 2', 'Underrubrik', 'Heading 2'] if s in available_styles), 'Heading 2')
        normal_style = next((s for s in ['Svarstext', 'Brödtext', 'Normal', 'Body Text'] if s in available_styles), 'Normal')

        unique = []
        seen = set()
        for s in sources:
            meta = s.get("metadata", {})
            key = (
                s.get("source_ref") or meta.get("source_ref") or "",
                meta.get("filename", ""),
                str(meta.get("page", "")),
                meta.get("library_id", "")
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(s)

        if not unique:
            return

        doc.add_paragraph("")
        doc.add_paragraph("Källbilaga (spårbarhet)", style=h1_style)
        doc.add_paragraph(
            "Nedan listas använda källor med käll-ID för spårbarhet till AI-svaret.",
            style=normal_style
        )

        for s in unique:
            meta = s.get("metadata", {})
            source_ref = s.get("source_ref") or meta.get("source_ref") or "-"
            filename = meta.get("filename", "Okänd fil")
            page = meta.get("page", "-")
            library_name = meta.get("library_name", "Okänt bibliotek")
            library_type = meta.get("library_type", s.get("type", "OKÄND"))
            doc_id = meta.get("doc_id", "-")
            snippet = (s.get("content") or "").strip()
            if len(snippet) > 450:
                snippet = snippet[:450] + "..."

            doc.add_paragraph(f"Källa {source_ref}", style=h2_style)
            doc.add_paragraph(f"Fil: {filename}", style=normal_style)
            doc.add_paragraph(f"Sida: {page}", style=normal_style)
            doc.add_paragraph(f"Bibliotek: {library_name} ({library_type})", style=normal_style)
            doc.add_paragraph(f"Dokument-ID: {doc_id}", style=normal_style)
            doc.add_paragraph(f"Utdrag: {snippet}", style=normal_style)

    def _tokenize(self, text: str):
        return re.findall(r"[a-z0-9åäö\-]{3,}", (text or "").lower())

    def _parse_image_suggestion(self, suggestion_text: str):
        parts = [p.strip() for p in (suggestion_text or "").split("|")]
        parts += ["", "", "", ""]
        what = parts[0]
        source = parts[1]
        page_text = parts[2]
        section = parts[3]

        page_num = None
        match = re.search(r"(\d{1,4})", page_text)
        if match:
            try:
                page_num = int(match.group(1))
            except Exception:
                page_num = None

        return {
            "raw": suggestion_text.strip(),
            "what": what.strip(),
            "source": source.strip().lower(),
            "section": section.strip(),
            "page": page_num,
        }

    def _score_image_candidate(self, suggestion: dict, image: dict, already_used_ids: set):
        source_document = str(image.get("source_document", ""))
        page = image.get("page")
        tags = image.get("tags") or []
        section_hints = image.get("section_hints") or []
        desc = image.get("description", "")
        context = image.get("context_excerpt", "")

        haystack = " ".join([
            source_document,
            str(page or ""),
            desc,
            context,
            " ".join([str(t) for t in tags]),
            " ".join([str(h) for h in section_hints]),
        ]).lower()

        suggestion_tokens = self._tokenize(
            f"{suggestion.get('what', '')} {suggestion.get('section', '')}"
        )
        overlap = sum(1 for token in suggestion_tokens if token in haystack)
        score = overlap * 3

        source_hint = suggestion.get("source")
        if source_hint:
            if source_hint in source_document.lower():
                score += 8
            else:
                score -= 1

        page_hint = suggestion.get("page")
        if page_hint is not None:
            try:
                if int(page or -1) == int(page_hint):
                    score += 8
                else:
                    score -= 1
            except Exception:
                pass

        image_id = image.get("id") or f"{source_document}:{page}:{image.get('url', '')}"
        if image_id in already_used_ids:
            score -= 4

        try:
            score += int(image.get("library_priority", 50)) // 30
        except Exception:
            pass

        return score

    def _select_image_for_suggestion(self, suggestion_text: str, matched_images: list, already_used_ids: set):
        if not matched_images:
            return None
        suggestion = self._parse_image_suggestion(suggestion_text)

        best = None
        best_score = -10**9
        for img in matched_images:
            score = self._score_image_candidate(suggestion, img, already_used_ids)
            if score > best_score:
                best_score = score
                best = img

        if best is None:
            return None

        # Require minimal relevance when a concrete hint exists.
        has_specific_hint = bool(suggestion.get("source") or suggestion.get("page") is not None or suggestion.get("what"))
        if has_specific_hint and best_score < 1:
            return None
        return best

    def _download_image_bytes(self, url: str):
        if not url:
            return None
        try:
            response = requests.get(url, timeout=12)
            if response.status_code == 200 and response.content:
                return response.content
        except Exception as e:
            print(f"Failed to download image {url}: {e}")
        return None

    def _replace_image_suggestions(self, doc, matched_images: list):
        if not matched_images:
            return 0

        available_styles = [s.name for s in doc.styles if s.type == 1]
        normal_style = next((s for s in ['Svarstext', 'Brödtext', 'Normal', 'Body Text'] if s in available_styles), 'Normal')

        inserted = 0
        used_ids = set()
        paragraphs = list(doc.paragraphs)

        for para in paragraphs:
            text = para.text or ""
            matches = list(self._IMAGE_SUGGESTION_RE.finditer(text))
            if not matches:
                continue

            fallback_notes = []
            anchor = para
            for match in matches:
                suggestion_raw = (match.group(1) or "").strip()
                if not suggestion_raw:
                    continue
                image = self._select_image_for_suggestion(suggestion_raw, matched_images, used_ids)
                if not image:
                    fallback_notes.append(f"Bildförslag: {suggestion_raw}")
                    continue

                image_bytes = self._download_image_bytes(image.get("url"))
                if not image_bytes:
                    fallback_notes.append(f"Bildförslag: {suggestion_raw}")
                    continue

                image_para = self._insert_paragraph_after(anchor)
                image_para.add_run().add_picture(io.BytesIO(image_bytes), width=Mm(130))

                caption = (
                    f"Figur: {image.get('description', 'Bild utan beskrivning')[:180]} "
                    f"(Källa: {image.get('source_document', 'okänd')}, sida {image.get('page', '-')})"
                )
                anchor = self._insert_paragraph_after(image_para, caption, style=normal_style)

                image_id = image.get("id") or f"{image.get('source_document')}:{image.get('page')}:{image.get('url', '')}"
                used_ids.add(image_id)
                inserted += 1

            cleaned = self._IMAGE_SUGGESTION_RE.sub("", text).strip()
            if fallback_notes:
                if cleaned:
                    cleaned = f"{cleaned}\n" + "\n".join(fallback_notes)
                else:
                    cleaned = "\n".join(fallback_notes)

            if cleaned:
                para.text = cleaned
            else:
                self._remove_paragraph(para)

        return inserted

    async def generate_word_response(self, query: str, answer: str, sources: list, 
                                      template_path: str = None, matched_images: list = None):
        path_to_use = template_path or self.template_path
        if not os.path.exists(path_to_use):
            self._create_default_template()
            path_to_use = self.template_path

        doc = DocxTemplate(path_to_use)
        
        # Prepare context for template
        formatted_sources = []
        for s in sources:
            metadata = s.get('metadata', {})
            formatted_sources.append({
                'source_ref': s.get('source_ref', metadata.get('source_ref')),
                'filename': metadata.get('filename', 'Okänd'),
                'page': metadata.get('page', 'N/A'),
                'library_name': metadata.get('library_name', 'Okänt bibliotek'),
                'library_type': metadata.get('library_type', s.get('type', 'OKÄND')),
                'doc_id': metadata.get('doc_id', '-'),
                'snippet': s.get('content', '')[:300] + "..."
            })

        # V9: Process matched images
        has_image_suggestions = bool(self._IMAGE_SUGGESTION_RE.search(answer or ""))
        formatted_images = []
        if matched_images and not has_image_suggestions:
            for img in matched_images[:5]:  # Limit to 5 images
                try:
                    # Download image from URL
                    response = requests.get(img.get('url'), timeout=10)
                    if response.status_code == 200:
                        image_stream = io.BytesIO(response.content)
                        inline_image = InlineImage(doc, image_stream, width=Mm(100))
                        formatted_images.append({
                            'inline_image': inline_image,
                            'description': img.get('description', 'Ingen beskrivning')[:200],
                            'source': img.get('source_document', 'Okänd källa')
                        })
                except Exception as e:
                    print(f"Failed to download image: {e}")
                    continue

        sections = self._extract_sections(answer)
        section_map = {}
        for sec in sections:
            key = f"section_{self._slugify(sec['title'])}"
            section_map[key] = sec["content"].strip()

        context = {
            'title': 'Textbehandlaren Export',
            'date': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'query': query,
            'answer': answer, # Fallback for old templates
            'content': answer,
            'ai_text': answer,
            'sources': formatted_sources,
            'images': formatted_images,
            'sections': sections,
            **section_map
        }
        
        doc.render(context)
        
        # V11: High-fidelity styling only if no explicit placeholder is present
        has_placeholder = self._template_has_placeholders(
            path_to_use,
            ["answer", "content", "ai_text"]
        )
        if not has_placeholder:
            # If the template contains headings with instruction text, replace per section
            try:
                self._apply_sections_by_headings(doc, answer)
            except Exception as e:
                print(f"Section injection failed: {e}. Falling back to append.")
                self._inject_styled_answer(doc, answer)

        # Replace explicit [BILDFÖRSLAG: ...] markers with actual images in-place.
        try:
            inserted = self._replace_image_suggestions(doc, matched_images or [])
            if inserted:
                print(f"Inserted {inserted} image(s) from BILDFÖRSLAG markers.")
        except Exception as e:
            print(f"Image suggestion replacement failed: {e}")

        # Always append a traceable source appendix for auditability.
        try:
            self._append_source_appendix(doc, sources)
        except Exception as e:
            print(f"Source appendix generation failed: {e}")
        
        filename = f"Textbehandlaren_Export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx"
        file_path = os.path.join(self.output_dir, filename)
        doc.save(file_path)
        
        return file_path

    def _inject_styled_answer(self, doc, markdown_text):
        """Append or inject the AI answer with appropriate template styles."""
        # Find best styles available in the template
        available_styles = [s.name for s in doc.styles if s.type == 1]
        
        # Style mapping logic - prioritize template specific names
        style_map = {
            'h1': next((s for s in ['Rubrik 1 med numrering', 'Rubrik 1', 'Huvudrubrik', 'Heading 1', 'Heading 1 med numrering'] if s in available_styles), 'Heading 1'),
            'h2': next((s for s in ['Rubrik 2 med numrering', 'Rubrik 2', 'Underrubrik', 'Heading 2'] if s in available_styles), 'Heading 2'),
            'h3': next((s for s in ['Rubrik 3 med numrering', 'Rubrik 3', 'Heading 3'] if s in available_styles), 'Heading 3'),
            'normal': next((s for s in ['Svarstext', 'Brödtext', 'Normal', 'Body Text'] if s in available_styles), 'Normal'),
            'bullet': next((s for s in ['List Bullet', 'Punktlista', 'ListBullet'] if s in available_styles), 'List Bullet')
        }

        # Simple Markdown line-by-line parser
        lines = markdown_text.split('\n')
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Headings
            if line.startswith('### '):
                doc.add_paragraph(line[4:], style=style_map['h3'])
            elif line.startswith('## '):
                doc.add_paragraph(line[3:], style=style_map['h2'])
            elif line.startswith('# '):
                doc.add_paragraph(line[2:], style=style_map['h1'])
            # Bullets
            elif line.startswith('- ') or line.startswith('* '):
                doc.add_paragraph(line[2:], style=style_map['bullet'])
            # Numbered lists (simplified)
            elif re.match(r'^\d+\.', line):
                content = re.sub(r'^\d+\.\s*', '', line)
                doc.add_paragraph(content, style='List Number' if 'List Number' in available_styles else None)
            # Normal text
            else:
                doc.add_paragraph(line, style=style_map['normal'])

    def _remove_paragraph(self, paragraph: Paragraph):
        p = paragraph._element
        p.getparent().remove(p)

    def _insert_paragraph_after(self, paragraph: Paragraph, text: str = "", style: str = None) -> Paragraph:
        new_p = OxmlElement("w:p")
        paragraph._p.addnext(new_p)
        new_para = Paragraph(new_p, paragraph._parent)
        if style:
            new_para.style = style
        if text:
            new_para.add_run(text)
        return new_para

    def _apply_sections_by_headings(self, doc, answer_markdown: str):
        available_styles = [s.name for s in doc.styles if s.type == 1]
        style_map = {
            'h1': next((s for s in ['Rubrik 1 med numrering', 'Rubrik 1', 'Huvudrubrik', 'Heading 1', 'Heading 1 med numrering'] if s in available_styles), 'Heading 1'),
            'h2': next((s for s in ['Rubrik 2 med numrering', 'Rubrik 2', 'Underrubrik', 'Heading 2'] if s in available_styles), 'Heading 2'),
            'h3': next((s for s in ['Rubrik 3 med numrering', 'Rubrik 3', 'Heading 3'] if s in available_styles), 'Heading 3'),
            'normal': next((s for s in ['Svarstext', 'Brödtext', 'Normal', 'Body Text'] if s in available_styles), 'Normal'),
            'bullet': next((s for s in ['List Bullet', 'Punktlista', 'ListBullet'] if s in available_styles), 'List Bullet')
        }

        sections = self._extract_sections(answer_markdown)
        section_by_title = {s["title"].strip().lower(): s["content"].strip() for s in sections}

        paragraphs = list(doc.paragraphs)
        i = 0
        while i < len(paragraphs):
            para = paragraphs[i]
            if is_heading(para):
                heading_title = (para.text or "").strip().lower()
                # Remove instruction paragraphs directly under heading
                j = i + 1
                while j < len(paragraphs) and not is_heading(paragraphs[j]):
                    if is_instruction(paragraphs[j]):
                        self._remove_paragraph(paragraphs[j])
                    j += 1

                # Insert generated content after heading
                content = section_by_title.get(heading_title)
                if content:
                    last = para
                    for line in content.splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        if line.startswith("### "):
                            last = self._insert_paragraph_after(last, line[4:], style_map['h3'])
                        elif line.startswith("## "):
                            last = self._insert_paragraph_after(last, line[3:], style_map['h2'])
                        elif line.startswith("# "):
                            last = self._insert_paragraph_after(last, line[2:], style_map['h1'])
                        elif line.startswith("- ") or line.startswith("* "):
                            last = self._insert_paragraph_after(last, line[2:], style_map['bullet'])
                        elif re.match(r'^\d+\.', line):
                            content_line = re.sub(r'^\d+\.\s*', '', line)
                            last = self._insert_paragraph_after(
                                last, content_line,
                                'List Number' if 'List Number' in available_styles else None
                            )
                        else:
                            last = self._insert_paragraph_after(last, line, style_map['normal'])
                i = j
            else:
                i += 1

exporter_service = ExporterService()
