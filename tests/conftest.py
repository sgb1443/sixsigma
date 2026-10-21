"""Lets the tests write `from app.qc import ...` by putting the repo root on sys.path."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
