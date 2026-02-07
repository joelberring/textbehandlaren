from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from backend.app.core.database import Base
from datetime import datetime

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    
    assistants = relationship("Assistant", back_populates="owner")

class Assistant(Base):
    __tablename__ = "assistants"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    name = Column(String, index=True)
    system_prompt = Column(Text)
    collection_name = Column(String, unique=True)
    template_path = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    owner = relationship("User", back_populates="assistants")
    documents = relationship("Document", back_populates="assistant")

class Document(Base):
    __tablename__ = "documents"
    id = Column(Integer, primary_key=True, index=True)
    assistant_id = Column(Integer, ForeignKey("assistants.id"))
    filename = Column(String)
    status = Column(String, default="processed")
    
    assistant = relationship("Assistant", back_populates="documents")
