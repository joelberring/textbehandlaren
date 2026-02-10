from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List
from backend.app.services.rag import rag_service
from backend.app.services.learning import learning_service
from backend.app.services.quota import quota_service, QuotaExceededError
from backend.app.core.firebase import db
from backend.app.core.auth import get_current_user, require_role
from backend.app.schemas.user import UserProfile, UserRole
import logging
import asyncio
from backend.app.services.job_store import job_store

router = APIRouter()

class ChatRequest(BaseModel):
    assistant_id: str
    query: str
    conversation_id: Optional[str] = None
    custom_persona: Optional[str] = None
    show_citations: bool = True
    project_id: Optional[str] = None  # V10: Project context
    target_pages: Optional[int] = None
    target_words: Optional[int] = None
    longform: Optional[bool] = None
    suggest_images: Optional[bool] = True
    response_mode: Optional[str] = "auto"  # auto | fast | standard | deep

class ChatJobStartResponse(BaseModel):
    job_id: str
    status: str

class BlockCommentRequest(BaseModel):
    assistant_id: str
    conversation_id: str
    full_text: str
    block_text: str
    comment: str
    project_id: Optional[str] = None

class GlobalStyleRulesRequest(BaseModel):
    rules: List[str]

class PersonalStyleRulesRequest(BaseModel):
    rules: List[str]

@router.post("/ask")
async def ask_question(
    request: ChatRequest,
    current_user: UserProfile = Depends(get_current_user)
):
    try:
        if current_user.role != UserRole.SUPERADMIN:
            try:
                quota_service.enforce_chat_quotas(current_user.id, request.project_id)
            except QuotaExceededError as q:
                raise HTTPException(
                    status_code=429,
                    detail={"message": q.message, "retry_after_seconds": q.retry_after_seconds},
                    headers={"Retry-After": str(q.retry_after_seconds)}
                )
            except Exception as quota_err:
                logging.warning(f"Quota enforcement failed (ask): {quota_err}")

        try:
            await learning_service.capture_preferences_from_text(
                current_user.id,
                request.query,
                source="query"
            )
        except Exception as mem_err:
            logging.warning(f"Preference capture failed (ask): {mem_err}")

        response = await rag_service.ask(
            request.query, 
            request.assistant_id,
            request.conversation_id,
            request.custom_persona,
            request.show_citations,
            user_id=current_user.id,
            project_id=request.project_id,
            target_pages=request.target_pages,
            target_words=request.target_words,
            longform=request.longform,
            suggest_images=True if request.suggest_images is None else bool(request.suggest_images),
            response_mode=(request.response_mode or "auto")
        )
        return response
    except Exception as e:
        error_msg = str(e)
        if "api_key" in error_msg.lower() or "auth" in error_msg.lower():
            raise HTTPException(
                status_code=401, 
                detail=f"Anthropic API-nyckel saknas eller är ogiltig. Kontrollera .env-filen. (Fel: {error_msg})"
            )
        raise HTTPException(status_code=500, detail=error_msg)

@router.post("/ask-async", response_model=ChatJobStartResponse)
async def ask_question_async(
    request: ChatRequest,
    current_user: UserProfile = Depends(get_current_user)
):
    """
    Start a chat generation job and return immediately with a job_id.
    The frontend polls /api/chat/jobs/{job_id} for progress/result.
    """
    try:
        if current_user.role != UserRole.SUPERADMIN:
            try:
                quota_service.enforce_chat_quotas(current_user.id, request.project_id)
            except QuotaExceededError as q:
                raise HTTPException(
                    status_code=429,
                    detail={"message": q.message, "retry_after_seconds": q.retry_after_seconds},
                    headers={"Retry-After": str(q.retry_after_seconds)}
                )
            except Exception as quota_err:
                logging.warning(f"Quota enforcement failed (ask-async): {quota_err}")

        try:
            await learning_service.capture_preferences_from_text(
                current_user.id,
                request.query,
                source="query"
            )
        except Exception as mem_err:
            logging.warning(f"Preference capture failed (ask-async): {mem_err}")

        job = await job_store.create_chat_job(
            user_id=current_user.id,
            assistant_id=request.assistant_id,
            query=request.query,
            conversation_id=request.conversation_id,
            project_id=request.project_id
        )

        async def _progress_cb(stage: str, progress: int, message: str = "", partial_answer: str = None):
            fields = {
                "status": "running",
                "stage": stage,
                "progress": progress,
                "message": message or "",
            }
            if partial_answer is not None:
                fields["partial_answer"] = partial_answer
            await job_store.update(job.id, **fields)

        async def _runner():
            try:
                await job_store.update(job.id, status="running", stage="starting", progress=1, message="Startar...")
                response = await rag_service.ask(
                    request.query,
                    request.assistant_id,
                    request.conversation_id,
                    request.custom_persona,
                    request.show_citations,
                    user_id=current_user.id,
                    project_id=request.project_id,
                    target_pages=request.target_pages,
                    target_words=request.target_words,
                    longform=request.longform,
                    suggest_images=True if request.suggest_images is None else bool(request.suggest_images),
                    response_mode=(request.response_mode or "auto"),
                    progress_cb=_progress_cb
                )
                await job_store.update(
                    job.id,
                    status="completed",
                    stage="completed",
                    progress=100,
                    message="Klar.",
                    answer=response.get("answer") or "",
                    sources=response.get("sources") or [],
                    matched_images=response.get("matched_images") or [],
                    debug=response.get("debug") or {},
                    error=""
                )
            except Exception as e:
                await job_store.update(
                    job.id,
                    status="failed",
                    stage="failed",
                    progress=100,
                    message="Fel.",
                    error=str(e)
                )

        # Run the runner and wait for it to complete. 
        # This is necessary on Vercel to ensure the task finishes before the function returns.
        await _runner()

        # Return full result inline so the frontend doesn't need to poll a separate worker.
        final_job = await job_store.get(job.id)
        result = {
            "job_id": job.id,
            "status": final_job.status if final_job else "completed",
            "answer": final_job.answer if final_job else "",
            "sources": final_job.sources if final_job else [],
            "matched_images": final_job.matched_images if final_job else [],
            "debug": final_job.debug if final_job else {},
            "error": final_job.error if final_job else "",
        }
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/jobs/{job_id}")
async def get_job_status(
    job_id: str,
    current_user: UserProfile = Depends(get_current_user)
):
    job = await job_store.get(job_id)
    if not job or str(job.user_id) != str(current_user.id):
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_public_dict()

@router.post("/comment-edit")
async def comment_edit(
    request: BlockCommentRequest,
    current_user: UserProfile = Depends(get_current_user)
):
    try:
        if current_user.role != UserRole.SUPERADMIN:
            try:
                quota_service.enforce_chat_quotas(current_user.id, request.project_id)
            except QuotaExceededError as q:
                raise HTTPException(
                    status_code=429,
                    detail={"message": q.message, "retry_after_seconds": q.retry_after_seconds},
                    headers={"Retry-After": str(q.retry_after_seconds)}
                )
            except Exception as quota_err:
                logging.warning(f"Quota enforcement failed (comment_edit): {quota_err}")

        try:
            await learning_service.capture_preferences_from_text(
                current_user.id,
                request.comment,
                source="comment_edit"
            )
        except Exception as mem_err:
            logging.warning(f"Preference capture failed (comment_edit): {mem_err}")

        response = await rag_service.edit_block(
            assistant_id=request.assistant_id,
            conversation_id=request.conversation_id,
            full_text=request.full_text,
            block_text=request.block_text,
            comment=request.comment,
            user_id=current_user.id,
            project_id=request.project_id
        )
        return response
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/learn/status")
async def get_learn_status(current_user: UserProfile = Depends(get_current_user)):
    """Get learning status including both global and personal style rules."""
    combined = await learning_service.get_combined_rules(current_user.id)
    return combined

@router.put("/learn/personal-rules")
async def set_personal_rules(
    request: PersonalStyleRulesRequest,
    current_user: UserProfile = Depends(get_current_user)
):
    try:
        rules = await learning_service.set_personal_style_rules(current_user.id, request.rules)
        return {"message": "Personliga regler uppdaterade", "personal_rules": rules}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/learn/{conversation_id}")
async def learn_from_chat(
    conversation_id: str,
    current_user: UserProfile = Depends(get_current_user)
):
    try:
        rules = await learning_service.learn_from_conversation(current_user.id, conversation_id)
        return {"message": "Inlärning slutförd", "learned_rules": rules}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/global-styles")
async def set_global_styles(
    request: GlobalStyleRulesRequest,
    current_user: UserProfile = Depends(require_role(UserRole.ADMIN))
):
    """Set global style rules that apply to all users. Admin/Superadmin only."""
    try:
        rules = await learning_service.set_global_style_rules(request.rules)
        return {"message": "Globala stilregler uppdaterade", "global_rules": rules}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

from google.cloud import firestore

@router.get("/conversations")
async def get_conversations(current_user: UserProfile = Depends(get_current_user)):
    """Get list of conversations for the current user."""
    convs = []
    # Fetch without explicit order_by to avoid composite index requirement in local dev
    docs = db.collection("conversations")\
             .where("user_id", "==", current_user.id)\
             .limit(100).stream()
    
    for d in docs:
        data = d.to_dict()
        data["id"] = d.id  # Ensure ID is included
        convs.append(data)
    
    # Sort in memory
    convs.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    return convs

@router.get("/conversations/{conversation_id}")
async def get_conversation_detail(
    conversation_id: str,
    current_user: UserProfile = Depends(get_current_user)
):
    """Get full history of a specific conversation."""
    conv_ref = db.collection("conversations").document(conversation_id).get()
    if not conv_ref.exists:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    data = conv_ref.to_dict()
    if data.get("user_id") != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to view this conversation")
    
    return data

@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    current_user: UserProfile = Depends(get_current_user)
):
    """Delete a conversation."""
    conv_ref = db.collection("conversations").document(conversation_id).get()
    if not conv_ref.exists:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    data = conv_ref.to_dict()
    if data.get("user_id") != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this conversation")
    
    db.collection("conversations").document(conversation_id).delete()
    return {"message": "Conversation deleted"}
