"""Install the Belarus Market Data DRAFT registry into a configured database."""

from app.database import SessionLocal
from app.models import User
from app.services.belarus_market_pack import install_belarus_market_pack
from sqlalchemy import select


def main() -> None:
    with SessionLocal() as db:
        admin = db.scalar(select(User).order_by(User.created_at))
        if admin is None:
            raise SystemExit("Create an administrator first (API bootstrap has not run).")
        print(install_belarus_market_pack(db, admin))


if __name__ == "__main__":
    main()
