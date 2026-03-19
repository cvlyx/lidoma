"""
Run this script to verify/create the school_reports table
"""
from db import engine
from models import Base

# Print all tables that will be created
print("Tables to create:")
for table in Base.metadata.sorted_tables:
    print(f"  - {table.name}")

# Create all tables
print("\nCreating tables...")
Base.metadata.create_all(bind=engine)
print("✅ Tables created successfully!")

# Verify school_reports exists
from sqlalchemy import inspect
inspector = inspect(engine)
tables = inspector.get_table_names()
print(f"\nTables in database: {tables}")

if 'school_reports' in tables:
    print("✅ school_reports table exists!")
else:
    print("❌ school_reports table NOT found!")
