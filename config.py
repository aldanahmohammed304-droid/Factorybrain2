"""
Configuration for FactoryBrain.

The OpenAI key is read from a .env file (or the OPENAI_API_KEY environment variable).

Setup:
    1. Create a file named  .env  in this folder containing:
           OPENAI_API_KEY=sk-your-key-here
    2. The .env file is already in .gitignore, so it will NOT be committed.

A valid key is required: without it the AI features return an error.
"""

import os

# Load variables from a .env file if python-dotenv is installed.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv optional; falls back to real environment variables


class Config:
    # Flask
    SECRET_KEY = os.environ.get("SECRET_KEY", "factorybrain-dev-secret-change-me")

    # Admin account (seeded automatically on first run)
    ADMIN_EMAIL = "admin@gmail.com"
    ADMIN_PASSWORD = "admin123"

    # OpenAI -- read from .env file (OPENAI_API_KEY=sk-...) or environment.
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

    # Model
    OPENAI_MODEL = "gpt-4o"
