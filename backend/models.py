from sqlalchemy import Column, Integer, String, Float, Text
from database import Base

class RFP(Base):
    __tablename__ = "rfps"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String)
    description = Column(Text)
    url = Column(String)
    agency = Column(String)
    category = Column(String)
    summary = Column(Text)
    score = Column(Float)
