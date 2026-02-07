import fitz  # PyMuPDF
from backend.app.core.config import settings
from langchain_anthropic import ChatAnthropic
import base64
import io
from PIL import Image

class VisionService:
    def __init__(self):
        self.llm = ChatAnthropic(
            model="claude-3-5-sonnet-20240620",
            anthropic_api_key=settings.ANTHROPIC_API_KEY
        )

    def extract_images_from_pdf(self, pdf_path: str):
        doc = fitz.open(pdf_path)
        image_descriptions = []

        for page_index in range(len(doc)):
            page = doc[page_index]
            image_list = page.get_images(full=True)

            for img_index, img in enumerate(image_list):
                xref = img[0]
                base_image = doc.extract_image(xref)
                image_bytes = base_image["image"]
                
                # Convert to base64 for Claude
                base64_image = base64.b64encode(image_bytes).decode('utf-8')
                
                description = self._describe_image(base64_image)
                image_descriptions.append({
                    "page": page_index + 1,
                    "description": description,
                    "type": "image_description"
                })

        return image_descriptions

    def _describe_image(self, base64_image: str):
        if not settings.ANTHROPIC_API_KEY:
            return "Image analysis skipped: API key missing."

        try:
            # Using LangChain with internal image support
            # Constructing the message for Vision
            message = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe this image in detail for a document management system. Focus on text, charts, or important visual features. Use Swedish."},
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": base64_image,
                            },
                        },
                    ],
                }
            ]
            response = self.llm.invoke(message)
            return response.content
        except Exception as e:
            print(f"Vision analysis failed: {e}")
            return "Kunde inte tolka bilden."

vision_service = VisionService()
