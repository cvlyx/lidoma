#!/usr/bin/env python3
"""Test script to diagnose import issues"""
import sys
import traceback

print("=" * 50)
print("Testing imports...")
print("=" * 50)

try:
    print("1. Testing settings import...")
    from settings import Settings
    print("   ✓ Settings imported successfully")
    
    print("2. Testing models import...")
    from models import Base
    print("   ✓ Models imported successfully")
    
    print("3. Testing db import...")
    from db import SessionLocal, init_db
    print("   ✓ DB imported successfully")
    
    print("4. Testing app import...")
    from app import app
    print("   ✓ App imported successfully")
    
    print("\n" + "=" * 50)
    print("ALL IMPORTS SUCCESSFUL!")
    print("=" * 50)
    sys.exit(0)
    
except Exception as e:
    print("\n" + "=" * 50)
    print("ERROR OCCURRED:")
    print("=" * 50)
    print(f"Error type: {type(e).__name__}")
    print(f"Error message: {str(e)}")
    print("\nFull traceback:")
    traceback.print_exc()
    print("=" * 50)
    sys.exit(1)
