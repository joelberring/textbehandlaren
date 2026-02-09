import os
import asyncio

from docx import Document

from backend.app.services.exporter import exporter_service


def test_word_export_injects_markdown_structure():
    answer = """# Rubrik 1

Detta ar en *kursiv* och **fet** mening.

## Underrubrik

- Punkt 1
- Punkt 2 med **fet** text

Bildtext: Figur 1. Exempelbild
"""

    path = asyncio.run(
        exporter_service.generate_word_response(
            query="Testfraga",
            answer=answer,
            sources=[],
            template_path="backend/app/templates/template.docx",
            matched_images=[],
        )
    )
    assert os.path.exists(path)

    doc = Document(path)
    paras = [(p.text or "", getattr(p.style, "name", "")) for p in doc.paragraphs]

    assert ("Rubrik 1", "Heading 1") in paras
    assert ("Underrubrik", "Heading 2") in paras
    assert any(t == "Punkt 1" and s.startswith("List Bullet") for (t, s) in paras)
    assert any(t.startswith("Bildtext:") and ("Caption" in s or "Bildtext" in s) for (t, s) in paras)

    # Keep workspace tidy
    try:
        os.remove(path)
    except Exception:
        pass

