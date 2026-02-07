import os
import uuid
import re
from typing import List
from datetime import datetime
import fitz  # PyMuPDF for image extraction
from langchain_community.document_loaders import PyMuPDFLoader, Docx2txtLoader, TextLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from backend.app.core.config import settings
from backend.app.core.firebase import db
from backend.app.services.vision import vision_service
from backend.app.core.storage import upload_image
from google.cloud.firestore_v1.vector import Vector
from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
from backend.app.services.embeddings import get_embeddings
from backend.app.services import vectorstore
from backend.app.services.scrubber import scrubber_service
from langchain_core.documents import Document

class IngestionService:
    _IMAGE_STOPWORDS = {
        "och", "att", "det", "som", "med", "för", "eller", "den", "detta", "denna",
        "från", "till", "har", "kan", "ska", "inte", "vid", "över", "under", "samt",
        "ochså", "också", "inom", "utan", "där", "här", "per", "en", "ett", "av",
        "the", "and", "with", "for", "from", "this", "that", "into", "over", "under",
        "image", "figure", "page", "karta", "bild", "figur", "diagram"
    }

    _IMAGE_SECTION_HINTS = [
        ({"buller", "noise", "ljud", "decibel", "db(a)"}, "Miljökonsekvenser / Buller"),
        ({"trafik", "väg", "gata", "parkering", "mobilitet", "flöde"}, "Trafik och mobilitet"),
        ({"dagvatten", "drän", "regn", "översvämning", "vatten", "flödesväg"}, "Vatten och dagvatten"),
        ({"risk", "farligt", "säkerhet", "olycka", "explosion", "brand"}, "Risk och säkerhet"),
        ({"geoteknik", "jord", "stabilitet", "sättningar", "berg"}, "Geotekniska förutsättningar"),
        ({"natur", "ekologi", "habitat", "träd", "grön", "art"}, "Naturmiljö och ekologi"),
        ({"kultur", "fornlämning", "miljö", "bevarande", "historisk"}, "Kulturmiljö"),
        ({"sol", "skugga", "ljus", "vind", "klimat"}, "Klimat, sol och skuggning"),
    ]

    def _coerce_text(self, value) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts = []
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    txt = item.get("text")
                    if txt:
                        parts.append(str(txt))
            return " ".join(parts).strip()
        return str(value)

    def _normalize_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", (text or "")).strip()

    def _tokenize(self, text: str) -> List[str]:
        return re.findall(r"[a-z0-9åäö\-]{3,}", (text or "").lower())

    def _extract_image_tags(self, description: str, page_text: str, max_tags: int = 10) -> List[str]:
        desc = self._normalize_text(description)
        page = self._normalize_text(page_text)[:1800]
        tokens = self._tokenize(f"{desc} {page}")

        counts = {}
        for token in tokens:
            if token in self._IMAGE_STOPWORDS:
                continue
            if token.isdigit():
                continue
            counts[token] = counts.get(token, 0) + 1

        # Prefer domain words first when available.
        domain_words = []
        for trigger_set, _ in self._IMAGE_SECTION_HINTS:
            for term in trigger_set:
                if term in counts and term not in domain_words:
                    domain_words.append(term)

        ranked = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
        tags = []
        for term in domain_words:
            tags.append(term)
            if len(tags) >= max_tags:
                return tags
        for term, _freq in ranked:
            if term in tags:
                continue
            tags.append(term)
            if len(tags) >= max_tags:
                break
        return tags

    def _infer_image_section_hints(self, tags: List[str], description: str, page_text: str) -> List[str]:
        blob = f"{' '.join(tags)} {description} {page_text}".lower()
        hints = []
        for trigger_set, hint in self._IMAGE_SECTION_HINTS:
            if any(trigger in blob for trigger in trigger_set):
                hints.append(hint)
        if not hints:
            hints.append("Placera i närmast relevanta sakavsnitt med bildförklaring")
        return hints[:4]

    async def process_document(
        self,
        file_path: str,
        filename: str,
        library_id: str,
        interpret_images: bool = False,
        gdpr_name_scrub: bool = False,
        initial_name_map: dict = None,
        gdpr_scrub_initiated_by: str = None
    ):
        """Processes a document in the background. Runs synchronous heavy tasks in a threadpool."""
        from fastapi.concurrency import run_in_threadpool
        
        try:
            print(f"Starting ingestion for {filename} in library {library_id}")
            extension = os.path.splitext(file_path)[1].lower()
            
            # Initial document record
            doc_id = str(uuid.uuid4())
            doc_ref = db.collection("libraries").document(library_id).collection("documents").document(doc_id)
            doc_ref.set({
                "id": doc_id,
                "filename": filename,
                "uploaded_at": datetime.utcnow(),
                "interpret_images": interpret_images,
                "gdpr_name_scrub": gdpr_name_scrub,
                "images_indexed": 0,
                "has_images": False,
                "extension": extension,
                "status": "processing"
            })

            # 1. Load document (Sync IO)
            def load_doc():
                if extension == ".pdf":
                    loader = PyMuPDFLoader(file_path)
                elif extension == ".docx":
                    loader = Docx2txtLoader(file_path)
                elif extension == ".txt":
                    loader = TextLoader(file_path)
                else:
                    raise ValueError(f"Unsupported file type: {extension}")
                return loader.load()

            documents = await run_in_threadpool(load_doc)
            print(f"Loaded {len(documents)} pages from {filename}")
            
            # Add metadata
            for doc in documents:
                doc.metadata["filename"] = filename
                doc.metadata["library_id"] = library_id
                doc.metadata["gdpr_name_scrub"] = gdpr_name_scrub

            # 1b. GDPR name scrubbing before chunking/indexing
            if gdpr_name_scrub:
                if not scrubber_service.is_configured():
                    raise ValueError("GDPR-namntvätt begärdes men Mistral API-nyckel saknas.")

                name_map = dict(initial_name_map or {})
                total_name_findings = 0
                total_occurrences = 0
                for doc in documents:
                    scrubbed_page, findings, name_map = await scrubber_service.scrub_person_names_with_cards(
                        doc.page_content,
                        existing_map=name_map
                    )
                    doc.page_content = scrubbed_page
                    total_name_findings += len(findings)
                    total_occurrences += sum(int(f.get("occurrences", 0)) for f in findings)

                try:
                    doc_ref.update({
                        "gdpr_scrub_mode": "NAMES_TO_DOCUMENT_CARD",
                        "gdpr_scrub_findings": total_name_findings,
                        "gdpr_scrub_replacements": total_occurrences,
                        "gdpr_scrub_cards_created": len(name_map),
                        "gdpr_scrub_status": "completed",
                        "gdpr_scrub_provider": settings.GDPR_SCRUB_PROVIDER,
                        "gdpr_scrub_model": settings.GDPR_NAME_SCRUB_MODEL,
                        "gdpr_scrub_at": datetime.utcnow(),
                        "gdpr_scrub_initiated_by": gdpr_scrub_initiated_by or "unknown"
                    })
                except Exception as e:
                    print(f"Failed to update GDPR scrub metadata: {e}")
            
            text_splitter = RecursiveCharacterTextSplitter(
                chunk_size=settings.CHUNK_SIZE,
                chunk_overlap=settings.CHUNK_OVERLAP
            )
            
            chunks = text_splitter.split_documents(documents)
            print(f"Split into {len(chunks)} chunks")

            # Add doc_id to chunk metadata for traceability
            for chunk in chunks:
                chunk.metadata["doc_id"] = doc_id
            
            # 2. Extract images if PDF (Sync IO + API)
            images_indexed = 0
            if interpret_images and extension == ".pdf":
                print(f"Extracting images from {filename}...")
                images_indexed = await self._extract_and_index_images(file_path, filename, library_id, doc_id)
                doc_ref.update({"images_indexed": images_indexed, "has_images": images_indexed > 0})

            # 3. Embed & Save (Sync CPU + IO)
            embeddings = get_embeddings()
            texts = [chunk.page_content for chunk in chunks]
            
            if texts:
                try:
                    doc_ref.update({
                        "total_chunks": len(chunks),
                        "processed_chunks": 0,
                        "progress": 0
                    })
                except Exception as e:
                    print(f"Failed to set initial progress: {e}")
                print(f"Generating embeddings for {len(texts)} chunks...")
                def get_all_embeddings():
                    try:
                        return embeddings.embed_documents(texts)
                    except Exception as e:
                        print(f"Batch embedding failed: {e}. Falling back to individual.")
                        return [embeddings.embed_query(t) for t in texts]
                
                embedded_texts = await run_in_threadpool(get_all_embeddings)
                
                print(f"Saving {len(chunks)} chunks to Firestore...")
                def save_chunks():
                    batch = db.batch()
                    count = 0
                    total = len(chunks)
                    last_update = 0
                    for chunk, vector in zip(chunks, embedded_texts):
                        chunk_ref = db.collection("libraries").document(library_id).collection("knowledge_base").document()
                        batch.set(chunk_ref, {
                            "text": chunk.page_content,
                            "metadata": {**chunk.metadata, "doc_id": doc_id},
                            "embedding": Vector(vector)
                        })
                        count += 1
                        if count % 400 == 0:
                            batch.commit()
                            batch = db.batch()
                        if (count - last_update) >= 50 or count == total:
                            try:
                                doc_ref.update({
                                    "processed_chunks": count,
                                    "progress": int((count / total) * 100)
                                })
                            except Exception:
                                pass
                            last_update = count
                    batch.commit()
                    try:
                        doc_ref.update({
                            "processed_chunks": total,
                            "progress": 100
                        })
                    except Exception:
                        pass
                
                await run_in_threadpool(save_chunks)

                # Also index in local Chroma for dev fallback
                if settings.ALLOW_LOCAL_FALLBACK:
                    try:
                        print(f"Indexing {len(chunks)} chunks in Chroma...")
                        await run_in_threadpool(lambda: vectorstore.add_documents(library_id, chunks))
                    except Exception as e:
                        print(f"Chroma indexing failed for library {library_id}: {e}")
            else:
                try:
                    doc_ref.update({
                        "total_chunks": 0,
                        "processed_chunks": 0,
                        "progress": 100
                    })
                except Exception:
                    pass
            
            # Update status to completed
            doc_ref.update({"status": "completed", "progress": 100})
            print(f"Successfully finished ingestion for {filename}")
            return {"text_chunks": len(chunks), "images_indexed": images_indexed, "doc_id": doc_id}

            
        except Exception as e:
            print(f"Error in background ingestion for {filename}: {e}")
            # Update status to failed
            try:
                payload = {"status": "failed", "error": str(e)}
                if gdpr_name_scrub:
                    payload["gdpr_scrub_status"] = "failed"
                doc_ref.update(payload)
            except:
                pass
            import traceback
            traceback.print_exc()
            raise e
        finally:
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    print(f"Cleanup: Removed temporary file {file_path}")
                except Exception as e:
                    print(f"Failed to remove temporary file {file_path}: {e}")


    async def _extract_and_index_images(self, pdf_path: str, source_filename: str, library_id: str, source_doc_id: str) -> int:
        """Extract images from PDF, upload to Storage, and index in Firestore."""
        doc = fitz.open(pdf_path)
        images_count = 0
        embeddings = get_embeddings()

        for page_index in range(len(doc)):
            page = doc[page_index]
            page_text = self._normalize_text(page.get_text("text"))
            image_list = page.get_images(full=True)

            for img_index, img in enumerate(image_list):
                try:
                    xref = img[0]
                    base_image = doc.extract_image(xref)
                    image_bytes = base_image["image"]
                    image_ext = base_image.get("ext", "png")
                    
                    # Generate unique filename for storage
                    unique_filename = f"page{page_index+1}_img{img_index+1}.{image_ext}"
                    
                    # Upload to Firebase Storage
                    public_url = upload_image(image_bytes, unique_filename, library_id)
                    
                    # Get description from Claude Vision
                    import base64
                    base64_image = base64.b64encode(image_bytes).decode('utf-8')
                    description = self._coerce_text(vision_service._describe_image(base64_image))
                    tags = self._extract_image_tags(description, page_text)
                    section_hints = self._infer_image_section_hints(tags, description, page_text)
                    context_excerpt = page_text[:420]
                    
                    # Generate embedding from description
                    semantic_text = f"{description}\nTaggar: {', '.join(tags)}\nSektionstips: {', '.join(section_hints)}"
                    desc_vector = embeddings.embed_query(semantic_text)
                    
                    # Store in Firestore image_assets collection
                    image_id = str(uuid.uuid4())
                    db.collection("image_assets").document(image_id).set({
                        "id": image_id,
                        "library_id": library_id,
                        "url": public_url,
                        "description": description,
                        "tags": tags,
                        "section_hints": section_hints,
                        "context_excerpt": context_excerpt,
                        "source_doc_id": source_doc_id,
                        "source_document": source_filename,
                        "page": page_index + 1,
                        "embedding": Vector(desc_vector),
                        "created_at": datetime.utcnow()
                    })
                    
                    images_count += 1
                except Exception as e:
                    print(f"Failed to process image on page {page_index + 1}: {e}")
                    continue
        
        return images_count


    def search(self, query: str, library_ids: List[str], k: int = 5, query_vector=None):
        # Generate query vector
        if query_vector is None:
            embeddings = get_embeddings()
            query_vector = embeddings.embed_query(query)
        
        found_docs = []
        
        # Firestore currently doesn't support vector search across multiple collections easily in one call.
        # We perform search on each library and merge.
        for lib_id in library_ids:
            collection_ref = db.collection("libraries").document(lib_id).collection("knowledge_base")
            
            try:
                results = collection_ref.find_nearest(
                    vector_field="embedding",
                    query_vector=Vector(query_vector),
                    distance_measure=DistanceMeasure.COSINE,
                    limit=k
                ).get()
                
                print(f"Firestore vector search returned {len(results)} results for library {lib_id}")
                for doc in results:
                    data = doc.to_dict()
                    found_docs.append(Document(
                        page_content=data["text"],
                        metadata=data["metadata"]
                    ))
            except Exception as e:
                # Vector search might fail if index is not configured
                # Fall back to returning no results for this library
                print(f"Vector search failed for library {lib_id}: {e}")
                if "index" in str(e).lower():
                    print(f"Index missing for library {lib_id}. Ensure vector index is created.")
                continue
        
        if len(found_docs) == 0 and settings.ALLOW_LOCAL_FALLBACK:
            print(f"No results in Firestore, trying local Chroma fallback for libraries {library_ids}")
            fallback_docs = []
            for lib_id in library_ids:
                try:
                    local_results = vectorstore.search(lib_id, query, k=k)
                    print(f"Chroma fallback returned {len(local_results)} results for library {lib_id}")
                    fallback_docs.extend(local_results)
                except Exception as e:
                    print(f"Chroma search failed for library {lib_id}: {e}")
            return fallback_docs[:k]

        # Optionally sort and limit if merging
        return found_docs[:k] 

ingestion_service = IngestionService()
