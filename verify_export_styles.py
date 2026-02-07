import asyncio
import os
import sys

# Add backend to path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from backend.app.services.exporter import exporter_service

async def test_export():
    markdown_test = """# Detta är en Huvudrubrik
Detta är ett stycke med normal text.

## En underrubrik nivå 2
- Punkt 1
- Punkt 2

### En underrubrik nivå 3
1. Första punkten i en numrerad lista
2. Andra punkten

Tack för samarbetet!"""

    query = "Hjälp med samråd"
    sources = [{"metadata": {"filename": "test.pdf", "page": 1}, "content": "Test context"}]
    
    # Use the user's specific template for a real test
    template_path = "backend/app/templates/27005009-a0bd-4fb4-ad16-68b077ccddeb_Mall - Samrådsredogörelse och granskningsutlåtande.docx"

    print("Generating export...")
    file_path = await exporter_service.generate_word_response(
        query, 
        markdown_test, 
        sources,
        template_path=template_path
    )
    print(f"Export generated at: {file_path}")
    
    # Inspect the first few paragraphs and their styles
    from docx import Document
    doc = Document(file_path)
    print("\nGenerated Document Paragraphs and Styles:")
    for i, p in enumerate(doc.paragraphs[-15:]): # Check the last 15 paragraphs (where our content is)
        if p.text.strip():
            print(f"{i}: [{p.style.name}] {p.text[:50]}...")

if __name__ == "__main__":
    asyncio.run(test_export())
