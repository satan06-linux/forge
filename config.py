import os
from dotenv import load_dotenv

# Load local environment variables from .env file
load_dotenv()

# MySQL Database Configurations
MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "root")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "forge_db")

# Groq API Configuration
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

# OpenRouter API Configuration
OPEN_ROUTER_API_KEY = os.getenv("OPEN_ROUTER_API_KEY", "")

# Secondary Groq API Key Configuration
GROQ_API_KEY_SECONDARY = os.getenv("GROQ_API_KEY_SECONDARY", os.getenv("GROQ_API_KEY_2", ""))

# API Activation Flags
GROQ_ACTIVE = os.getenv("GROQ_ACTIVE", "false").lower() == "true"
OPEN_ROUTER_ACTIVE = os.getenv("OPEN_ROUTER_ACTIVE", "false").lower() == "true"

# Token Efficiency Flag
DEC_TOKEN_USAGE = os.getenv("DEC_TOKEN_USAGE", "false").lower() == "true"

# Flask Security
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY environment variable is not set!")

# Upload Configuration for Vision-to-Prompt screenshots
UPLOAD_FOLDER = os.path.join("static", "uploads")
MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # 5MB Max upload limit
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
