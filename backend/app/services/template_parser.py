import re
from typing import List, Dict, Any
from docx import Document


def is_heading(paragraph) -> bool:
    text = (paragraph.text or "").strip()
    try:
        style_name = (paragraph.style.name or "").lower()
    except Exception:
        style_name = ""

    if any(token in style_name for token in ["heading", "rubrik", "huvudrubrik", "underrubrik"]):
        return True
    if re.match(r"^(rubrik|underrubrik|rubruk|heading)\s*:", text, re.IGNORECASE):
        return True
    return False


def _normalize_heading_title(raw_title: str) -> str:
    title = (raw_title or "").strip()
    title = re.sub(
        r"^(rubrik|underrubrik|rubruk|heading)\s*:\s*",
        "",
        title,
        flags=re.IGNORECASE
    )
    title = re.sub(r"\s+", " ", title).strip()
    return title


def _heading_level(paragraph) -> int:
    text = (paragraph.text or "").strip()
    try:
        style_name = (paragraph.style.name or "").lower()
    except Exception:
        style_name = ""

    match = re.search(r"(heading|rubrik)\s*([1-9])", style_name, re.IGNORECASE)
    if match:
        return int(match.group(2))
    if "huvudrubrik" in style_name:
        return 1
    if "underrubrik" in style_name:
        return 3
    if text.startswith("### "):
        return 3
    if text.startswith("## "):
        return 2
    if text.startswith("# "):
        return 1
    return 2


def _is_placeholder_text(text: str) -> bool:
    lower = text.lower().strip()
    placeholder_exact = {
        "text",
        "kursiv text",
        "alternativt",
        "slut",
        "ingen text direkt här"
    }
    if lower in placeholder_exact:
        return True
    if re.fullmatch(r"x{2,}", lower):
        return True
    return False


def _looks_like_guidance(text: str) -> bool:
    lower = text.lower()
    guidance_markers = [
        "ska ",
        "bör ",
        "använd",
        "ange",
        "redovisa",
        "beskriv",
        "fyll i",
        "kom ihåg",
        "ta bort",
        "stryk",
        "rubriken",
        "här redovisas",
        "här skrivs",
        "under denna rubrik"
    ]
    return any(marker in lower for marker in guidance_markers)


def is_instruction(paragraph) -> bool:
    try:
        style_name = (paragraph.style.name or "").lower()
    except Exception:
        style_name = ""
    text = (paragraph.text or "").strip()
    if not text:
        return False

    if _is_placeholder_text(text):
        return True

    lower = text.lower()
    keywords = [
        "instruktion", "skriv", "fyll i", "ange", "beskriv", "exempel",
        "kommentar", "här skriver", "todo", "tbd", "placeholder"
    ]
    if any(k in lower for k in keywords):
        return True
    if text.startswith("[") and text.endswith("]"):
        return True
    if "{" in text and "}" in text:
        return True
    if "instruktion" in style_name or "kommentar" in style_name or "note" in style_name or "svarstext" in style_name:
        return True
    if _looks_like_guidance(text):
        return True
    return False


def parse_template(path: str) -> Dict[str, Any]:
    doc = Document(path)
    sections: List[Dict[str, Any]] = []
    global_instructions: List[str] = []
    current = None

    for para in doc.paragraphs:
        text = (para.text or "").strip()
        if not text:
            continue
        if is_heading(para):
            if current:
                sections.append(current)
            current = {
                "title": _normalize_heading_title(text),
                "level": _heading_level(para),
                "instructions": []
            }
        else:
            if is_instruction(para):
                if current:
                    if len(current["instructions"]) < 6:
                        current["instructions"].append(text[:280])
                else:
                    if len(global_instructions) < 12:
                        global_instructions.append(text[:280])

    if current:
        sections.append(current)

    filtered = []
    for sec in sections:
        title = (sec.get("title") or "").strip()
        lower_title = title.lower()
        if not title:
            continue
        # Ignore TOC-only heading blocks that should not drive generation.
        if lower_title in {"innehåll", "contents"}:
            continue
        filtered.append(sec)

    return {"sections": filtered, "global_instructions": global_instructions}


def build_template_prompt(parsed: Dict[str, Any]) -> str:
    sections = parsed.get("sections", [])
    global_instructions = parsed.get("global_instructions", [])
    if not sections:
        if not global_instructions:
            return ""
        return "GLOBALA MALLINSTRUKTIONER:\n- " + "\n- ".join(global_instructions)
    lines = ["STRUKTURMALL (följ rubrikerna i denna ordning och använd rubriknamnen):"]
    for sec in sections:
        level = max(1, min(int(sec.get("level", 2)), 4))
        hashes = "#" * level
        lines.append(f"{hashes} {sec['title']}")
    if global_instructions:
        lines.append("")
        lines.append("GLOBALA MALLINSTRUKTIONER (gäller hela texten):")
        for instr in global_instructions:
            lines.append(f"- {instr}")
    lines.append("")
    lines.append("INSTRUKTIONER PER RUBRIK (ska INTE återges ordagrant i svaret):")
    for sec in sections:
        if sec.get("instructions"):
            lines.append(f"RUBRIK: {sec['title']}")
            for instr in sec["instructions"]:
                lines.append(f"  - {instr}")
    lines.append("")
    lines.append("Skriv aldrig ut hjälpord som 'Rubrik:', 'Underrubrik:', 'Text', 'Kursiv text' i slutresultatet.")
    return "\n".join(lines)
