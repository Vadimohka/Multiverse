from app.bootstrap import seed
from app.database import Base, SessionLocal, engine
Base.metadata.create_all(engine)
with SessionLocal() as db: seed(db)
print("Seed data created")
