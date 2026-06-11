"""
Script to create a test user for the application.
Run this inside the backend container:
python create_test_user.py
"""
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from app.core.security import get_password_hash
from app.models.user import User
from app.core.config import settings

async def create_test_user():
    # Create async engine
    database_url = "postgresql+asyncpg://user:password@db:5432/sicms"
    
    engine = create_async_engine(database_url, echo=True)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as session:
        # Check if user already exists
        from sqlalchemy.future import select
        result = await session.execute(select(User).where(User.email == "analyst@example.com"))
        existing_user = result.scalars().first()
        
        if existing_user:
            print("User analyst@example.com already exists!")
            print(f"User ID: {existing_user.id}")
            print(f"Email: {existing_user.email}")
            print(f"Is Active: {existing_user.is_active}")
            return
        
        # Create new user
        user = User(
            email="analyst@example.com",
            hashed_password=get_password_hash("password123"),
            full_name="Security Analyst",
            is_active=True,
            role="analyst"
        )
        
        session.add(user)
        await session.commit()
        await session.refresh(user)
        
        print(f"✓ Created user: {user.email}")
        print(f"  Password: password123")
        print(f"  User ID: {user.id}")

if __name__ == "__main__":
    asyncio.run(create_test_user())
