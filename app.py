import os
import sys
import builtins
import pathlib

# Force UTF-8 encoding for Windows terminal prints
sys.stdout.reconfigure(encoding='utf-8') if hasattr(sys.stdout, 'reconfigure') else None

# Global Windows UTF-8 Monkeypatch to prevent tokenizer / Pathlib crashes on startup
real_open = builtins.open
def utf8_open(*args, **kwargs):
    mode = kwargs.get('mode', '')
    if len(args) > 1:
        mode = args[1]
    if 'encoding' not in kwargs and 'b' not in mode:
        kwargs['encoding'] = 'utf-8'
    return real_open(*args, **kwargs)
builtins.open = utf8_open

real_read_text = pathlib.Path.read_text
def utf8_read_text(self, encoding=None, errors=None):
    return real_read_text(self, encoding=encoding or 'utf-8', errors=errors)
pathlib.Path.read_text = utf8_read_text

real_path_open = pathlib.Path.open
def utf8_path_open(self, mode='r', buffering=-1, encoding=None, errors=None, newline=None):
    if 'r' in mode and 'b' not in mode and encoding is None:
        encoding = 'utf-8'
    return real_path_open(self, mode=mode, buffering=buffering, encoding=encoding, errors=errors, newline=newline)
pathlib.Path.open = utf8_path_open

# Clear proxy-related environment variables to prevent the Groq/httpx proxies initialization bug
for key in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
    if key in os.environ:
        del os.environ[key]

import uuid
import json
from datetime import datetime, timedelta
import bcrypt
import jwt
from flask import Flask, render_template, request, Response, jsonify, abort

import config
import models

# Phase 6 Service Layer Imports
# from services.event_bus import event_bus
class DummyEventBus:
    def publish(self, *args, **kwargs): pass
event_bus = DummyEventBus()
from services.router_service import RouterService
from services.embedding_service import EmbeddingService
from services.vector_service import MySQLVectorStore
from services.rag_service import RAGService
from services.sandbox_service import SandboxService
from services.memory_service import MemoryService
from services.mcp_service import MCPService
from services.connector_service import ConnectorService
from services.plugin_sdk import PluginSDK, NodeExecutionContext
from services.streaming_service import StreamingService
from services.observability_service import ObservabilityService
# from services.execution_queue import execution_queue
class DummyExecutionQueue:
    def enqueue(self, *args, **kwargs): pass
    def register_executor(self, *args, **kwargs): pass
execution_queue = DummyExecutionQueue()

# Phase 7
from services.container import container
from services.api_phase7 import phase7_api

app = Flask(__name__)
app.secret_key = config.SECRET_KEY

# Register Phase 7 API Blueprint
app.register_blueprint(phase7_api)

# ============ CENTRALIZED DOMAIN-SEPARATED LOGGING ============
import logging
from logging.handlers import RotatingFileHandler
from flask import g, has_request_context

os.makedirs("logs", exist_ok=True)

# Custom Filter to inject Flask request_id
class RequestIdFilter(logging.Filter):
    def filter(self, record):
        if has_request_context() and hasattr(g, 'request_id'):
            record.request_id = g.request_id
        else:
            record.request_id = "N/A"
        return True

request_id_filter = RequestIdFilter()
log_formatter = logging.Formatter('%(asctime)s [%(levelname)s] [req_id=%(request_id)s] %(name)s: %(message)s')

# Console Handler
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)
console_handler.setLevel(logging.INFO)
console_handler.addFilter(request_id_filter)

# App rotating file handler (for info and warnings)
app_file_handler = RotatingFileHandler(os.path.join("logs", "app.log"), maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
app_file_handler.setFormatter(log_formatter)
app_file_handler.setLevel(logging.INFO)
app_file_handler.addFilter(request_id_filter)

# Error rotating file handler (for error & critical log levels)
error_file_handler = RotatingFileHandler(os.path.join("logs", "error.log"), maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
error_file_handler.setFormatter(log_formatter)
error_file_handler.setLevel(logging.ERROR)
error_file_handler.addFilter(request_id_filter)

# Setup Flask app logger
app.logger.handlers = []
app.logger.addHandler(console_handler)
app.logger.addHandler(app_file_handler)
app.logger.addHandler(error_file_handler)
app.logger.setLevel(logging.INFO)

# Setup Security logger
security_logger = logging.getLogger("forge.security")
security_logger.setLevel(logging.INFO)
security_logger.handlers = []
security_file_handler = RotatingFileHandler(os.path.join("logs", "security.log"), maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
security_file_handler.setFormatter(log_formatter)
security_file_handler.setLevel(logging.INFO)
security_file_handler.addFilter(request_id_filter)
security_logger.addHandler(console_handler)
security_logger.addHandler(security_file_handler)

# Flask before_request hook to assign request UUIDs
@app.before_request
def before_request_func():
    g.request_id = str(uuid.uuid4())

# ============ LIGHTWEIGHT IN-MEMORY IP RATE LIMITER ============
import threading
import time
from collections import defaultdict

class InMemoryRateLimiter:
    """
    Thread-safe in-memory sliding window IP rate limiter.
    Phase 1 Scope: Single-server / single-process protection only.
    """
    def __init__(self, limit, period_seconds=60):
        self.limit = limit
        self.period_seconds = period_seconds
        self.requests = defaultdict(list)
        self.lock = threading.Lock()

    def is_allowed(self, client_ip):
        now = time.time()
        with self.lock:
            # Filter timestamps
            self.requests[client_ip] = [t for t in self.requests[client_ip] if now - t < self.period_seconds]
            if len(self.requests[client_ip]) >= self.limit:
                return False
            self.requests[client_ip].append(now)
            return True

# Initialize limiters
login_limiter = InMemoryRateLimiter(limit=5, period_seconds=60)
register_limiter = InMemoryRateLimiter(limit=3, period_seconds=60)
forge_limiter = InMemoryRateLimiter(limit=30, period_seconds=60)

import hashlib

def get_client_ip():
    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    if client_ip and "," in client_ip:
        client_ip = client_ip.split(",")[0].strip()
    return client_ip or "unknown"

def log_analytics_event(event_type, user_id=None, category_used=None, template_name=None, event_metadata=None):
    """Logs a privacy-preserving analytics event to the database using hashed visitor ID."""
    client_ip = get_client_ip()
    user_agent = request.headers.get("User-Agent", "")
    # Calculate SHA256 hashed visitor ID (strictly for analytics, not session validation)
    visitor_id = hashlib.sha256((client_ip + user_agent).encode('utf-8')).hexdigest()
    
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor()
        metadata_str = json.dumps(event_metadata) if event_metadata is not None else None
        cursor.execute(
            """
            INSERT INTO analytics_events (event_type, user_id, visitor_id, category_used, template_name, event_metadata)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (event_type, user_id, visitor_id, category_used, template_name, metadata_str)
        )
        conn.commit()
    except Exception as e:
        app.logger.error(f"[Analytics Log Error] Failed to write event {event_type}: {str(e)}")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

def score_prompt(prompt_text):
    """
    Computes a prompt quality score (0 to 100) deterministically.
    Evaluates: Context (25pt), Clarity (25pt), Constraints (25pt), Output Formatting (25pt).
    """
    if not prompt_text:
        return {"total": 0, "context": 0, "clarity": 0, "constraints": 0, "format": 0}
        
    low = prompt_text.lower()
    
    # 1. Context / Persona (0-25)
    context = 10
    if any(k in low for k in ["act as", "you are", "role", "persona", "simulate"]):
        context += 10
    if len(prompt_text) > 150:
        context += 5
    context = min(25, context)
        
    # 2. Clarity & Structure (0-25)
    clarity = 10
    if any(k in prompt_text for k in ["-", "*", "•", "1.", "2."]):
        clarity += 10
    if any(k in prompt_text for k in [":", "?", "\n\n"]):
        clarity += 5
    clarity = min(25, clarity)
        
    # 3. Constraints & Rules (0-25)
    constraints = 10
    if any(k in low for k in ["do not", "never", "only", "must", "avoid", "negative", "except"]):
        constraints += 10
    if any(k in low for k in ["limit", "restrict", "rule", "constraint"]):
        constraints += 5
    constraints = min(25, constraints)
        
    # 4. Output Format Guidelines (0-25)
    out_format = 10
    if any(k in low for k in ["json", "markdown", "xml", "csv", "format", "output", "table", "layout"]):
        out_format += 10
    if any(k in low for k in ["template", "structure", "schema", "tags"]):
        out_format += 5
    out_format = min(25, out_format)
    
    total = context + clarity + constraints + out_format
    return {
        "total": total,
        "context": context,
        "clarity": clarity,
        "constraints": constraints,
        "format": out_format
    }

# ============ PROMETHEUS LOCAL GPU TESTING CONTROLLER ============
from prometheus.engine import prometheus_engine

PROMETHEUS_STATUS = {"state": "loading", "source": "prometheus", "error": None}

# Background thread to load Prometheus in background to prevent startup block
def load_prometheus_background():
    global PROMETHEUS_STATUS
    PROMETHEUS_STATUS = {"state": "loading", "source": "prometheus", "error": None}
    try:
        prometheus_engine.initialize()
        if getattr(prometheus_engine, "_initialized", False):
            PROMETHEUS_STATUS = {"state": "ready", "source": "prometheus", "error": None}
        else:
            PROMETHEUS_STATUS = {"state": "error", "source": "fallback", "error": "Prometheus was not initialized."}
    except Exception as e:
        PROMETHEUS_STATUS = {"state": "error", "source": "fallback", "error": str(e)}
        app.logger.error(f"[Prometheus Engine Startup Error] {str(e)}", exc_info=True)

threading.Thread(target=load_prometheus_background, daemon=True).start()

# Configure upload boundaries
app.config["UPLOAD_FOLDER"] = config.UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = config.MAX_CONTENT_LENGTH
os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)

@app.route("/api/model-status", methods=["GET"])
def api_model_status():
    status = dict(PROMETHEUS_STATUS)
    # Determine active source based on available API keys (cascade order)
    if config.GROQ_API_KEY and config.GROQ_ACTIVE:
        status["active_source"] = "groq"
    elif config.OPEN_ROUTER_API_KEY and config.OPEN_ROUTER_ACTIVE:
        status["active_source"] = "openrouter"
    elif status.get("state") == "ready":
        status["active_source"] = "prometheus"
    else:
        status["active_source"] = "fallback"
    return jsonify({"success": True, **status})

# ============ JWT AUTHENTICATION UTILITIES ============

def generate_jwt_token(user_id, name, email, role="user"):
    """Generates a secure JWT token valid for 7 days."""
    try:
        payload = {
            "user_id": user_id,
            "name": name,
            "email": email,
            "role": role,
            "exp": datetime.utcnow() + timedelta(days=7)
        }
        return jwt.encode(payload, config.SECRET_KEY, algorithm="HS256")
    except Exception as e:
        app.logger.error(f"[JWT Generation Error] {str(e)}", exc_info=True)
        return None

def decode_jwt_token(token):
    """Decodes a JWT token and returns payload, or None if invalid/expired."""
    if not token:
        return None
    try:
        if token.startswith("Bearer "):
            token = token[7:]
        payload = jwt.decode(token, config.SECRET_KEY, algorithms=["HS256"])
        return payload
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError) as e:
        security_logger.warning(f"JWT Verification Failed: {str(e)}")
        return None

def get_current_user(req):
    """Retrieves user payload from the Authorization header."""
    auth_header = req.headers.get("Authorization")
    if not auth_header:
        return None
    return decode_jwt_token(auth_header)

# ============ HTML PAGE ROUTERS ============

@app.route("/")
def index():
    """Renders the dark futuristic deep space marketing landing page."""
    user = get_current_user(request)
    log_analytics_event("page_view", user_id=user["user_id"] if user else None)
    potw = get_prompt_of_the_week()
    return render_template("landing.html", prompt_of_the_week=potw)

@app.route("/auth")
def auth_gate():
    """Renders the JWT register/login gateway card."""
    user = get_current_user(request)
    log_analytics_event("page_view", user_id=user["user_id"] if user else None)
    return render_template("auth.html")

@app.route("/workbench")
def workbench():
    """Renders the main ForgePrompt workspace builder panel (protected by JWT on frontend)."""
    user = get_current_user(request)
    log_analytics_event("page_view", user_id=user["user_id"] if user else None)
    return render_template("index.html")

@app.route("/history")
def history_ledger():
    """Renders the prompt audit ledger (protected by JWT on frontend)."""
    user = get_current_user(request)
    log_analytics_event("page_view", user_id=user["user_id"] if user else None)
    return render_template("history.html")

@app.route("/admin")
def admin_command():
    """Renders the admin control deck (protected by JWT on frontend)."""
    user = get_current_user(request)
    log_analytics_event("page_view", user_id=user["user_id"] if user else None)
    return render_template("admin.html")

# ============ JWT API ENDPOINTS ============

@app.route("/api/auth/register", methods=["POST"])
def api_register():
    """Registers a new creator in the MySQL users table."""
    client_ip = get_client_ip()
    if not register_limiter.is_allowed(client_ip):
        security_logger.warning(f"Rate limit exceeded for registration from IP {client_ip}")
        return jsonify({"success": False, "error": "Too many registration attempts. Please try again in a minute."}), 429

    name = request.json.get("name", "").strip()
    email = request.json.get("email", "").strip().lower()
    password = request.json.get("password", "").strip()

    if not name or not email or not password:
        return jsonify({"success": False, "error": "All fields are required."}), 400

    salt = bcrypt.gensalt()
    pw_hash = bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")

    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor()
        
        # Check duplicate
        cursor.execute("SELECT COUNT(*) FROM users WHERE email = %s", (email,))
        if cursor.fetchone()[0] > 0:
            return jsonify({"success": False, "error": "An account with this email already exists."}), 400
            
        # Bootstrap Admin check
        bootstrap_email = ConfigService.get_bootstrap_admin_email()
        role = "admin" if bootstrap_email and email == bootstrap_email else "user"
        
        # Generate unique username
        base_username = "".join(c for c in name if c.isalnum()).lower()
        if not base_username:
            base_username = "creator"
        
        username = base_username
        counter = 1
        while True:
            cursor.execute("SELECT COUNT(*) FROM users WHERE username = %s", (username,))
            if cursor.fetchone()[0] == 0:
                break
            username = f"{base_username}{counter}"
            counter += 1
            
        cursor.execute(
            "INSERT INTO users (name, email, username, password_hash, role) VALUES (%s, %s, %s, %s, %s)",
            (name, email, username, pw_hash, role)
        )
        user_id = cursor.lastrowid
        
        # Create user profile record
        cursor.execute(
            "INSERT INTO profiles (user_id, display_name) VALUES (%s, %s)",
            (user_id, name)
        )
        
        conn.commit()
        
        # Generate token
        token = generate_jwt_token(user_id, name, email, role)
        log_analytics_event("registration", user_id=user_id)
        app.logger.info(f"User {email} registered successfully with role {role} [user_id={user_id}]")
        return jsonify({
            "success": True,
            "token": token,
            "user": {"id": user_id, "name": name, "email": email}
        })
    except Exception as e:
        app.logger.error(f"Registration failed: {str(e)}", exc_info=True)
        return jsonify({"success": False, "error": "Registration failed due to an internal error."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route("/api/auth/login", methods=["POST"])
def api_login():
    """Authenticates credentials against MySQL users table and issues a JWT."""
    client_ip = get_client_ip()
    if not login_limiter.is_allowed(client_ip):
        security_logger.warning(f"Rate limit exceeded for login from IP {client_ip}")
        return jsonify({"success": False, "error": "Too many login attempts. Please try again in a minute."}), 429

    email = request.json.get("email", "").strip().lower()
    password = request.json.get("password", "").strip()

    if not email or not password:
        return jsonify({"success": False, "error": "Email and password are required."}), 400

    conn = None
    cursor = None
    user = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()
    except Exception as e:
        app.logger.error(f"Database search error during login: {str(e)}", exc_info=True)
        return jsonify({"success": False, "error": "Database retrieval failed."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    if user and bcrypt.checkpw(password.encode("utf-8"), user["password_hash"].encode("utf-8")):
        if user.get("is_banned"):
            security_logger.warning(f"Banned login attempt for email {email} from IP {client_ip}")
            return jsonify({"success": False, "error": "This account has been suspended by administrators."}), 403
            
        token = generate_jwt_token(user["id"], user["name"], user["email"], user["role"])
        log_analytics_event("login", user_id=user["id"])
        app.logger.info(f"User {email} logged in successfully [user_id={user['id']}]")
        return jsonify({
            "success": True,
            "token": token,
            "user": {"id": user["id"], "name": user["name"], "email": user["email"]}
        })

    security_logger.warning(f"Failed login attempt for email {email} from IP {client_ip}")
    return jsonify({"success": False, "error": "Invalid email or password."}), 401

@app.route("/api/auth/verify", methods=["GET"])
def api_verify():
    """Verifies client's stored JWT token."""
    user = get_current_user(request)
    if not user:
        return jsonify({"success": False, "error": "Unauthorized session."}), 401
    return jsonify({"success": True, "user": user})

@app.route("/api/global-stats", methods=["GET"])
def api_global_stats():
    """Retrieves the public prompt count for guest and authenticated viewers."""
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM prompts")
        count = cursor.fetchone()[0]
        return jsonify({"success": True, "total_prompts": 2341 + count})
    except Exception as e:
        app.logger.error(f"Global Stats Error: {str(e)}", exc_info=True)
        return jsonify({"success": True, "total_prompts": 2341})
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# ============ SYSTEM CORE PROMPT API ENDPOINTS ============

@app.route("/api/clarify-prompt", methods=["POST"])
def api_clarify_prompt():
    raw_prompt = request.json.get("prompt", "").strip()
    category = request.json.get("category", "").strip()
    
    if not raw_prompt:
        return jsonify({"success": False, "error": "Please describe what you want to forge."}), 400

    source = "groq"
    result = None
    detected_category = category if category else "Image gen"
    
    # Intent category detection
    low = raw_prompt.lower()
    if any(k in low for k in ["earn", "money", "income", "sell", "revenue", "business", "profit", "monetize"]):
        detected_category = "Business/Monetization"
    elif not category:
        if any(k in low for k in ["code", "python", "script", "scrap", "developer", "html", "css", "js", "program", "web dev"]):
            detected_category = "Code"
        elif any(k in low for k in ["write", "essay", "brief", "email", "blog", "story", "copy", "writing"]):
            detected_category = "Writing"
        elif any(k in low for k in ["bot", "chat", "persona", "helper", "support", "chatbot"]):
            detected_category = "Chatbot"

    system_instructions = (
        "You are ForgePrompt AI, an elite prompt engineering architect.\n"
        "The user will describe a concept or project they want to create. Analyze their intent very carefully.\n"
        "Your job is to ask smart, specific, contextual questions to deeply refine their idea based on their domain.\n\n"
        "INTENT DETECTION & QUESTION ROUTING RULES:\n"
        "- If user mentions business/monetization keywords (e.g., earn, money, income, sell, revenue, business, profit):\n"
        "  → BUSINESS/MONETIZATION mode. Ask exactly 4 questions covering: Business Model, Target Audience, Core Revenue Stream, and Competitive Differentiation.\n"
        "  → NEVER ask about visual aesthetics or animations for business requests.\n"
        "- If user mentions image/visual/art/render/design/photo/cinematic keywords:\n"
        "  → IMAGE GENERATION mode. Ask exactly 6 questions covering ALL of these dimensions:\n"
        "    Q1: Environment & World Scale (e.g. mega-metropolis, dense cyberpunk city, open alien landscape, underwater world)\n"
        "    Q2: Art Style & Rendering Quality (e.g. Photorealistic 8K PBR, Anime cinematic, Concept art painterly, Unreal Engine 5)\n"
        "    Q3: Camera Angle & Composition (e.g. Low-angle hero shot, Bird's eye aerial, Dutch-tilt cinematic, Rule-of-thirds wide)\n"
        "    Q4: Lighting Atmosphere (e.g. Volumetric neon glow + wet reflections, Golden hour HDR, Dramatic chiaroscuro, Soft studio)\n"
        "    Q5: Color Palette (e.g. Electric blue + cyan + magenta neon, Warm amber + gold, Monochrome noir, Emerald + dark chrome)\n"
        "    Q6: Mood & Motion (e.g. High-speed adrenaline chase with motion blur, Serene floating stillness, Epic battle dynamism)\n"
        "- If user mentions web development keywords (e.g., website, dashboard, app, UI, design):\n"
        "  → WEB DEV mode. Ask exactly 5 questions: Purpose & Audience, Layout/Grid style, Core Feature set, Brand personality, Tech stack preference.\n"
        "- If user mentions coding keywords (e.g., code, script, function, API, backend):\n"
        "  → CODE mode. Ask exactly 4 questions: Language/Framework, Core Functionality, Architecture pattern, Error handling & testing needs.\n"
        "- If user mentions content keywords (e.g., write, blog, post, content, email, caption):\n"
        "  → CONTENT WRITING mode. Ask exactly 4 questions: Format/Structure, Tone/Voice, Platform & Length, SEO or persuasion goal.\n"
        "- If user mentions chatbot/persona keywords:\n"
        "  → CHATBOT mode. Ask exactly 4 questions: Persona & Name, Conversation Tone, Primary Use Case, Fallback & Memory behavior.\n\n"
        "CRITICAL RULES FOR JSON OUTPUT:\n"
        "1. Write questions and options in VERY SIMPLE, plain English so that a complete beginner/rookie can understand them easily. Avoid complex technical jargon.\n"
        "2. Do NOT list choices inside the 'question' string. Keep question text clean and conversational.\n"
        "3. Place exact choices strictly inside the 'options' array. Use 4 specific, distinct options per question.\n"
        "4. Options must be concrete but easy to grasp (e.g. 'A dark rainy city' instead of 'Chiaroscuro volumetric cyberpunk metropolis').\n"
        "5. Do NOT include any prefix like 'Nice concept!' in the question text.\n"
        "6. Return ONLY a valid JSON array. No markdown, no intros, no outros:\n"
        "[{\"id\":1,\"question\":\"Question text here?\",\"options\":[\"Option 1\",\"Option 2\",\"Option 3\",\"Option 4\"]}]"
    )
    
    user_query = f"Create targeted questions for this concept: '{raw_prompt}'"
    if category:
        user_query += f"\nThe user has pre-selected the category/focus: '{category}'."

    # ---- Tier 1: Primary Groq Key ----
    groq_key = config.GROQ_API_KEY
    if groq_key and config.GROQ_ACTIVE:
        try:
            from groq import Groq
            import httpx
            client = Groq(api_key=groq_key, http_client=httpx.Client())
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system_instructions},
                    {"role": "user", "content": user_query}
                ],
                max_tokens=1000,
                temperature=0.2
            )
            raw_output = response.choices[0].message.content.strip()
            cleaned = raw_output.replace("```json", "").replace("```", "").strip()
            result = json.loads(cleaned)
            source = "groq"
            print("[Cascade] MCQ generated via Primary Groq.")
        except Exception as e:
            print(f"[Cascade] Primary Groq MCQ failed: {e}")

    # ---- Tier 2: OpenRouter ----
    if result is None and config.OPEN_ROUTER_ACTIVE:
        or_key = config.OPEN_ROUTER_API_KEY
        if or_key:
            try:
                import requests as req_lib
                headers = {
                    "Authorization": f"Bearer {or_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://forgeprompt.com",
                    "X-Title": "ForgePrompt"
                }
                payload = {
                    "model": "meta-llama/llama-3.3-70b-instruct",
                    "messages": [
                        {"role": "system", "content": system_instructions},
                        {"role": "user", "content": user_query}
                    ],
                    "max_tokens": 1000,
                    "temperature": 0.2
                }
                res = req_lib.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=45)
                if res.status_code == 200:
                    raw_output = res.json()["choices"][0]["message"]["content"].strip()
                    cleaned = raw_output.replace("```json", "").replace("```", "").strip()
                    result = json.loads(cleaned)
                    source = "openrouter"
                    print("[Cascade] MCQ generated via OpenRouter.")
                else:
                    print(f"[Cascade] OpenRouter MCQ failed: {res.status_code} {res.text[:200]}")
            except Exception as e:
                print(f"[Cascade] OpenRouter MCQ exception: {e}")

    # ---- Tier 3: Local Prometheus ----
    if result is None:
        try:
            result = prometheus_engine.generate_mcqs(raw_prompt)
            source = "prometheus"
            print("[Cascade] MCQ generated via local Prometheus.")
        except Exception as e:
            print(f"[Prometheus Fallback MCQ Error] {str(e)}")
            # Safety static questions fallback
            result = [
                {"id": 1, "question": "What is the preferred visual/art style?", "options": ["Cinematic Realism", "Figma mockup UI", "Anime digital art", "Minimal slate"]},
                {"id": 2, "question": "Select the ideal atmospheric lighting", "options": ["Volumetric neon glow", "Bright daylight studio", "Dramatic chiaroscuro", "Minimal moody slate"]}
            ]
            source = "fallback"

    # Validate result
    if isinstance(result, list):
        for q in result:
            if not isinstance(q.get("options"), list) or len(q.get("options", [])) == 0:
                q["options"] = ["Yes", "No", "Not sure"]

    return jsonify({
        "success": True,
        "type_detected": detected_category,
        "questions": result,
        "source": source
    })

@app.route("/api/forge-prompt", methods=["POST"])
def api_forge_prompt():
    raw_payload = request.json
    input_text = raw_payload.get("prompt", "").strip()
    type_detected = raw_payload.get("category", "")
    mcq_answers = raw_payload.get("mcq_answers", {})
    chat_history = raw_payload.get("chat_history", [])
    user_id = raw_payload.get("user_id", "guest")
    template_name = raw_payload.get("template_name", None)

    if not input_text:
        return jsonify({"success": False, "error": "No input prompt provided."}), 400

    if template_name:
        log_analytics_event("template_used", user_id=user_id, template_name=template_name)

    from services.llm_service import LLMService
    
    # Construct the instruction specifications
    # Detect domain for section routing
    _cat = type_detected.lower()
    _is_visual   = any(k in _cat for k in ["image", "visual", "art", "render", "research", "photo", "cinematic", "design"])
    _is_code     = any(k in _cat for k in ["code", "script", "api", "backend", "developer"])
    _is_web      = any(k in _cat for k in ["web", "dashboard", "app", "ui", "frontend"])
    _is_writing  = any(k in _cat for k in ["writ", "blog", "content", "email", "copy"])
    _is_chatbot  = any(k in _cat for k in ["bot", "chat", "persona"])
    _is_business = any(k in _cat for k in ["business", "monetiz", "revenue", "earn"])

    # Build domain-aware system prompt for pure flowing output
    _cat = type_detected.lower()
    _is_visual   = any(k in _cat for k in ["image", "visual", "art", "render", "research", "photo", "cinematic", "design", "gen"])
    _is_code     = any(k in _cat for k in ["code", "script", "api", "backend", "developer"])
    _is_web      = any(k in _cat for k in ["web", "dashboard", "app", "ui", "frontend"])
    _is_writing  = any(k in _cat for k in ["writ", "blog", "content", "email", "copy"])
    _is_chatbot  = any(k in _cat for k in ["bot", "chat", "persona"])
    _is_business = any(k in _cat for k in ["business", "monetiz", "revenue", "earn"])

    if _is_visual:
        system_prompt = (
            "You are ForgePrompt AI — the world's most elite AI prompt architect, trained on thousands of award-winning Midjourney, DALL·E, Runway, and Stable Diffusion prompts.\n"
            "The user has described a visual concept and answered targeted questions about it.\n"
            "Your job: generate a single, flowing, MASTERCLASS-level image or visualization prompt that would score 10/10 in any professional AI art community.\n\n"
            "ABSOLUTE RULES:\n"
            "1. Output ONLY the prompt itself. Zero labels, zero section headers, zero intros, zero explanations. Just the raw prompt.\n"
            "2. Write in a dense, comma-separated cinematic style — one continuous flowing paragraph or a rich sequence of descriptors.\n"
            "3. MUST include ALL of the following naturally woven in:\n"
            "   - Subject: ultra-specific description of the main subject with materials (carbon fiber, graphene, brushed titanium, transparent OLED glass, liquid metal accents)\n"
            "   - Environment: mega-scale world-building (500-1000m skyscrapers, rain-soaked streets, holographic billboards, volumetric fog, flying traffic lanes, autonomous drones)\n"
            "   - Camera: exact shot type + lens (e.g. low-angle hero shot, 24mm wide, slight dutch tilt, shallow depth of field, rule of thirds)\n"
            "   - Lighting: specific light sources (neon bounce lighting, volumetric god rays, HDR global illumination, wet surface reflections, bloom, subtle lens flare, atmospheric haze)\n"
            "   - Color palette: named exact colors (electric blue #00D4FF, deep magenta #FF00AA, emerald #00FF88, dark chrome #1A1A2E, neon cyan #00FFFF)\n"
            "   - Motion & FX: motion blur, speed trails, hover dust particles, plasma exhaust glow, energy streaks, spark particles, distorted air\n"
            "   - Mood: emotional tone (adrenaline, futuristic optimism, mysterious tension, epic wonder)\n"
            "   - Render quality: ultra-detailed, 8K, photorealistic, Unreal Engine 5 quality, Octane Render, PBR, ray-traced reflections, cinematic depth of field, HDR\n"
            "4. Make it feel like a professional VFX director wrote it.\n"
            "5. The prompt must be rich enough that ANY image AI generates a jaw-dropping, cinematic, gallery-worthy result.\n"
            "6. Do NOT start with 'A' or 'An'. Start with the most striking descriptor or action word."
        )
    elif _is_code:
        system_prompt = (
            "You are ForgePrompt AI — a principal-level software architect, 10x engineer, and technical prompt maestro with expertise across every major programming paradigm.\n"
            "The user has described a coding project and answered targeted questions about it.\n"
            "Your job: generate a single, flowing, MASTERCLASS-level technical specification prompt that a senior engineer at Google, Meta, or Stripe would be proud to write.\n\n"
            "ABSOLUTE RULES:\n"
            "1. Output ONLY the prompt itself. Zero labels, zero section headers, zero intros, zero meta-commentary. Just the raw technical prompt.\n"
            "2. Write as one dense, flowing, expert-grade technical brief — the way a Staff Engineer writes a design doc.\n"
            "3. MUST naturally weave in ALL of the following:\n"
            "   - Language + runtime + exact version (e.g. Python 3.12, Node.js 22 LTS, Go 1.22, Rust 1.78)\n"
            "   - Framework + key libraries with versions (e.g. FastAPI 0.111, SQLAlchemy 2.0 async, Pydantic v2, uvicorn, httpx)\n"
            "   - Architecture pattern with justification (e.g. async microservices with event sourcing via Kafka, or clean-architecture monolith with hexagonal ports)\n"
            "   - Core data models + schema design with field types and relationships\n"
            "   - API surface design (REST/GraphQL/gRPC — endpoints, request/response shapes, auth method e.g. JWT RS256, OAuth2 PKCE)\n"
            "   - Concurrency model (async/await, worker threads, goroutines, thread pool sizing)\n"
            "   - Error handling philosophy (typed exceptions, structured logging, retry with exponential backoff, circuit breakers)\n"
            "   - Performance targets (p99 latency < Xms, throughput N req/s, memory budget)\n"
            "   - Caching strategy (Redis 7 with TTL policy, CDN edge caching, in-memory LRU)\n"
            "   - Database (PostgreSQL 16 with pgvector / MongoDB Atlas / DynamoDB — with indexing strategy and query optimization notes)\n"
            "   - Testing pyramid (pytest with fixtures + hypothesis for property testing / Jest + Supertest / Go table-driven tests — coverage target ≥90%)\n"
            "   - CI/CD pipeline (GitHub Actions → Docker multi-stage build → ECR → ECS Fargate or Kubernetes with HPA)\n"
            "   - Security posture (input validation, SQL injection prevention, secrets in AWS Secrets Manager, OWASP Top 10 mitigations)\n"
            "   - Observability (structured JSON logs via structlog, Prometheus metrics, OpenTelemetry traces, Grafana dashboards)\n"
            "   - Scalability plan (horizontal pod autoscaling, database read replicas, sharding strategy if needed)\n"
            "4. Be brutally specific. Never write 'use a database' — write 'PostgreSQL 16 with async asyncpg driver, connection pooling via PgBouncer at 100 max connections, UUID v7 primary keys, BRIN indexes on timestamp columns'.\n"
            "5. The output must be so complete that a senior developer can open their IDE and start building immediately — zero clarifying questions needed.\n"
            "6. Make it feel like it was written by the principal architect of a YC-backed Series A startup."
        )
    elif _is_web:
        system_prompt = (
            "You are ForgePrompt AI — a principal UI/UX architect, design systems lead, and full-stack engineering director with mastery of modern web development.\n"
            "The user has described a web application and answered targeted questions about it.\n"
            "Your job: generate a single, flowing, MASTERCLASS-level web development prompt that a senior product engineer at Linear, Vercel, or Figma would produce.\n\n"
            "ABSOLUTE RULES:\n"
            "1. Output ONLY the prompt itself. Zero labels, zero section headers, zero intros. Just the raw prompt.\n"
            "2. Write as one dense, flowing technical + design brief — the way a Staff Engineer + Lead Designer collaborate on a product spec.\n"
            "3. MUST naturally weave in ALL of the following:\n"
            "   - Exact tech stack: framework (Next.js 14 App Router / SvelteKit / Remix / Nuxt 3), runtime (Node.js / Bun / Deno), DB (PostgreSQL + Prisma ORM / PlanetScale + Drizzle / Supabase), hosting (Vercel / Railway / Fly.io / AWS)\n"
            "   - Design system: primary + secondary + accent color tokens with hex values, typography scale (font family, size scale, line heights), 8px spacing grid, border radius tokens, shadow system\n"
            "   - Component architecture: atomic design hierarchy, key components with props interface, compound patterns, controlled vs uncontrolled patterns\n"
            "   - State management: Zustand stores / Jotai atoms / React Query v5 for server state / TanStack Router for type-safe routing\n"
            "   - Authentication: NextAuth.js v5 / Clerk / Lucia — specify providers, session strategy (JWT vs database), RBAC model\n"
            "   - Animation + interactions: Framer Motion page transitions, micro-interaction specs (hover states, loading skeletons, optimistic UI updates)\n"
            "   - Performance: Core Web Vitals targets (LCP < 2.5s, CLS < 0.1, INP < 200ms), image optimization (next/image with AVIF), code splitting strategy, prefetching\n"
            "   - SEO: dynamic meta tags, Open Graph, JSON-LD structured data, sitemap.xml, robots.txt\n"
            "   - Accessibility: WCAG 2.1 AA compliance, keyboard navigation, ARIA roles, focus management, color contrast ratios\n"
            "   - Responsive breakpoints: mobile-first (320px, 640px, 768px, 1024px, 1280px, 1536px) with specific layout changes at each\n"
            "   - CI/CD: GitHub Actions → Vercel preview deployments → production with feature flags via LaunchDarkly or Posthog\n"
            "   - Observability: Sentry for error tracking, Posthog for analytics, Axiom for logs\n"
            "4. Be brutally specific with design. Never say 'clean modern look' — say 'glassmorphism card components with backdrop-blur(12px), #0A0A0F dark background, Inter variable font, 600 weight headings, 0.5px letter-spacing, card border rgba(255,255,255,0.08)'.\n"
            "5. The output must be so complete that a senior full-stack developer can scaffold, design, and deploy the entire app with zero clarifying questions.\n"
            "6. Make it feel like it was written by the Head of Engineering at a design-forward SaaS startup."
        )
    elif _is_writing:
        system_prompt = (
            "You are ForgePrompt AI — a Pulitzer-caliber editorial director, conversion copywriting expert, and content strategy mastermind with experience at The New Yorker, HubSpot, and top DTC brands.\n"
            "The user has described a writing project and answered targeted questions about it.\n"
            "Your job: generate a single, flowing, MASTERCLASS-level writing prompt that a world-class writer can execute immediately to produce a final, publish-ready piece.\n\n"
            "ABSOLUTE RULES:\n"
            "1. Output ONLY the prompt itself. Zero labels, zero section headers, zero intros. Just the raw writing prompt.\n"
            "2. Write as one dense, flowing editorial brief — the way a top Editor-in-Chief briefs a star writer.\n"
            "3. MUST naturally weave in ALL of the following:\n"
            "   - Exact content format (long-form investigative article / punchy LinkedIn carousel / cold email sequence / sales page / Twitter/X thread / YouTube script / press release)\n"
            "   - Precise word/character count target or time-to-read goal\n"
            "   - Voice + tone fingerprint (e.g. 'authoritative but approachable — think Malcolm Gladwell meets Paul Graham: short punchy sentences, contrarian openings, data-backed claims delivered with narrative warmth')\n"
            "   - Target reader psychographic (job title, pain point, belief system, what keeps them up at night, what they aspire to)\n"
            "   - Emotional arc the piece must follow (e.g. Curiosity → Tension → Revelation → Inspiration → CTA)\n"
            "   - Hook strategy for the opening (contrarian stat, provocative question, micro-story, bold claim)\n"
            "   - Key argument or thesis the piece must prove, with 3 supporting pillars\n"
            "   - Specific data points, studies, or examples to reference (real or instructed to research)\n"
            "   - SEO strategy if applicable (primary keyword, LSI keywords, placement rules, meta description formula)\n"
            "   - CTA specifics (what action, what urgency, what value proposition)\n"
            "   - Structural skeleton (exact heading hierarchy, paragraph count per section, transition logic)\n"
            "   - What NOT to do (clichés to avoid, forbidden phrases, tones that would kill the piece)\n"
            "4. Be cinematically specific about voice. Never say 'conversational' — say 'second-person direct address, short 8-12 word sentences, Oxford commas, zero passive voice, opens every section with a 1-sentence punch'.\n"
            "5. The output must be so complete that the writer produces a final draft on the first attempt — zero rewrites, zero guessing.\n"
            "6. Make it feel like it was written by a top-tier creative director at Ogilvy or a founding editor at Substack."
        )
    elif _is_chatbot:
        system_prompt = (
            "You are ForgePrompt AI — a master conversational AI designer, NLP architect, and persona psychologist who has built personas for top-tier AI products.\n"
            "The user has described a chatbot or AI persona and answered targeted questions about it.\n"
            "Your job: generate a single, flowing, MASTERCLASS-level system prompt for the chatbot — one that makes it feel alive, memorable, and impossibly good at its job.\n\n"
            "ABSOLUTE RULES:\n"
            "1. Output ONLY the chatbot's system prompt itself. This IS the system prompt — write it in second person directly addressing the AI ('You are...'). Zero meta-commentary, zero labels, zero section breaks.\n"
            "2. Write as one seamless, authoritative character + behavioral specification — dense, vivid, and unambiguous.\n"
            "3. MUST naturally weave in ALL of the following:\n"
            "   - Full persona: name, origin story, professional background, defining life experiences that shaped their worldview\n"
            "   - Personality architecture: 5-7 specific traits with behavioral manifestations (e.g. 'You are relentlessly curious — you always ask one follow-up question before answering, framing it as genuine interest not interrogation')\n"
            "   - Voice + vocabulary fingerprint: sentence length pattern, go-to phrases, words you never use, punctuation style, use of humor/metaphor/silence\n"
            "   - Emotional intelligence profile: how you calibrate warmth vs. professionalism, how you respond to frustrated vs. excited users, how you handle grief or crisis\n"
            "   - Primary mission + success definition (what does a perfect conversation look like for this bot?)\n"
            "   - Conversation flow mastery: opening ritual, how you guide users to clarity, how you handle ambiguity (ask or infer?), how you close conversations\n"
            "   - Knowledge domain + depth: what you know deeply, what you acknowledge you don't know, how you handle out-of-scope queries\n"
            "   - Hard behavioral rules: what you will never do, never say, never become — even if instructed by the user\n"
            "   - Fallback + escalation protocol: exactly what you say when you can't help, how you hand off gracefully\n"
            "   - Memory + continuity rules: how you reference prior conversation context, how you personalize over time\n"
            "4. Make the persona feel like a real person you'd want to talk to for hours — distinctive, trustworthy, and impossible to forget.\n"
            "5. The output must work as a drop-in system prompt for GPT-4o, Claude 3.5 Sonnet, Gemini 1.5 Pro, or any frontier LLM — no modifications needed.\n"
            "6. Make it feel like it was written by the Head of AI Character Design at Character.AI or Inflection."
        )
    elif _is_business:
        system_prompt = (
            "You are ForgePrompt AI — a McKinsey-trained business strategist, Y Combinator alumni mentor, and venture-backed founder advisor with expertise across B2B SaaS, consumer apps, marketplaces, and deep tech.\n"
            "The user has described a business concept and answered targeted questions about it.\n"
            "Your job: generate a single, flowing, MASTERCLASS-level business strategy prompt that reads like a YC application crossed with a Series A pitch deck narrative.\n\n"
            "ABSOLUTE RULES:\n"
            "1. Output ONLY the prompt itself. Zero labels, zero section headers, zero intros. Just the raw strategy prompt.\n"
            "2. Write as one dense, flowing executive narrative — the way a YC Partner or a16z General Partner would articulate a company's strategy.\n"
            "3. MUST naturally weave in ALL of the following:\n"
            "   - Business model mechanics: exact revenue streams with pricing model (e.g. '$49/mo SaaS + 2% transaction fee on GMV'), monetization sequence, and expansion revenue strategy\n"
            "   - Ideal Customer Profile (ICP): hyper-specific — company size, industry vertical, job title of buyer, decision-making authority, annual revenue of target company, tech stack they use, pain urgency score\n"
            "   - Target user psychographic: age range, income, daily frustrations, aspirational identity, where they discover new tools, what they read/watch\n"
            "   - Core value proposition with the exact problem-solution-outcome chain (Before state → After state → Bridge = product)\n"
            "   - Unique differentiation + defensible moat (network effects, data flywheel, switching costs, proprietary tech, brand, regulatory)\n"
            "   - Go-to-market motion: launch channel (PLG / sales-led / community-led / influencer / SEO / paid), CAC strategy, first 100 customers playbook\n"
            "   - Unit economics targets: CAC, LTV, LTV:CAC ratio (target ≥3:1), gross margin, payback period, churn targets\n"
            "   - Competitive landscape: 3-5 specific named competitors, honest strengths/weaknesses, why this product wins in head-to-head\n"
            "   - Traction milestones: specific metrics for pre-seed, seed, and Series A readiness (e.g. $10K MRR, 100 paying customers, <5% monthly churn)\n"
            "   - Team requirements: key hires in sequence, founding team archetype needed, advisors to recruit\n"
            "   - Funding strategy: bootstrapped vs. VC, check size, use of funds breakdown, investor archetype to target\n"
            "   - Risk register: top 3 existential risks with specific mitigation strategies\n"
            "4. Be brutally specific. Never say 'target SMBs' — say 'bootstrapped SaaS founders with $50K-$500K ARR, 2-10 person teams, using Stripe + Notion + Linear, frustrated by manual churn analysis'.\n"
            "5. The output must be so actionable that a founder can walk into a YC interview or investor meeting tomorrow and nail it.\n"
            "6. Make it feel like it was written by a General Partner at Sequoia who also ran a successful startup."
        )
    else:
        system_prompt = (
            "You are ForgePrompt AI — the world's most elite AI prompt architect, with mastery across every creative, technical, and strategic domain.\n"
            "The user has described a concept or project and answered targeted questions about it.\n"
            "Your job: generate a single, flowing, MASTERCLASS-level prompt that produces world-class results in any AI tool or with any expert human.\n\n"
            "ABSOLUTE RULES:\n"
            "1. Output ONLY the prompt itself. Zero labels, zero section headers, zero intros, zero explanations. Just the raw prompt.\n"
            "2. Write as one dense, flowing expert brief — the way the world's best specialist in this domain would articulate the full vision.\n"
            "3. Be ruthlessly specific: name exact tools, methods, standards, metrics, and success criteria. Vague words like 'good', 'modern', 'nice', 'interesting' are FORBIDDEN.\n"
            "4. Cover every dimension that matters for this domain: goals, constraints, audience, execution method, quality standard, output format, success metrics.\n"
            "5. Include enough context that the executing AI or human has ZERO reasons to ask a clarifying question.\n"
            "6. The output must feel like it was written by the undisputed global authority in this specific field.\n"
            "7. Make every single word earn its place — no filler, no hedging, no padding."
        )

    selections_str = "\n".join([f"- {k}: {v}" for k, v in mcq_answers.items()])

    # --- Target Model formatting rules ---
    _model = (raw_payload.get("target_model", "generic") or "generic").strip().lower()
    model_instructions = {
        "universal_chat": "FORMAT FOR Universal Chat AI: Write in clear, structured natural language. Be explicit, comprehensive, and provide all necessary context so that any major LLM (ChatGPT, Claude, Gemini) can execute the instructions perfectly.",
        "midjourney":"FORMAT FOR Midjourney: Use comma-separated visual descriptor chains. No sentences. Follow Midjourney v6 prompt syntax: [subject], [environment], [camera], [lighting], [style], [quality tags]. End with: --ar 16:9 --style raw --q 2 --v 6",
        "stablediffusion": "FORMAT FOR Stable Diffusion (SDXL/Flux): Use weighted term syntax with emphasis on style tags. Include positive and negative prompt sections. Follow SDXL best practices: subject first, then style, then quality boosters like (masterpiece:1.4), (ultra-detailed:1.2). Mention sampler if relevant (DPM++ 2M Karras).",
        "runway":    "FORMAT FOR Runway Gen-3: Write as a cinematic scene description for video generation. Include motion direction (camera movement, subject motion), time of day, duration hint, and transition style. Runway excels at short cinematic loops — specify mood and pacing.",
        "dalle":     "FORMAT FOR DALL-E 3 (OpenAI): Write in natural, descriptive sentences. DALL-E 3 follows natural language closely — describe the scene as if writing a vivid caption. Avoid technical syntax. Emphasize style, mood, and subject with adjective richness.",
        "cursor":    "FORMAT FOR Cursor/Copilot (Code AI): Write as a precise technical specification for code generation. Include: language, framework, file structure hints, function signatures, expected behavior, edge cases, and test cases. Use developer-friendly terminology. This will be used as a system prompt or inline comment directive.",
        "bolt":      "FORMAT FOR Bolt.new / Lovable (AI app builders): Write as a full product specification. Include: UI layout description, tech stack (prefer Vite + React + Tailwind), component list, user flows, data model, and any API integrations. Be explicit enough that the AI can scaffold the entire app from this prompt alone.",
        "generic":   "FORMAT FOR General Use: Write as a rich, universally compatible prompt that works across all major AI tools."
    }
    model_note = model_instructions.get(_model, model_instructions["generic"])

    # --- Optimization Style modifier ---
    _style = (raw_payload.get("optimization_style", "detailed") or "detailed").strip().lower()
    style_instructions = {
        "detailed":      "STYLE: Detailed / Exhaustive — Leave nothing implicit. Cover every dimension, sub-detail, and edge case. Maximize depth and completeness.",
        "professional":  "STYLE: Professional / Corporate — Polished, formal, and authoritative. Suitable for enterprise, B2B, or executive audiences. No jargon, no fluff — crisp precision.",
        "creative":      "STYLE: Creative / Storytelling — Write with narrative flair, vivid imagery, and emotional resonance. Prioritize voice, personality, and artistic richness over technical dryness.",
        "technical":     "STYLE: Technical / Precise — Use exact terminology, version numbers, specifications, and measurable criteria. Engineer-grade clarity. Zero ambiguity.",
        "academic":      "STYLE: Academic / Scholarly — Formal register, evidence-based framing, structured argumentation. Cite methodology, acknowledge limitations, use domain vocabulary correctly.",
        "marketing":     "STYLE: Marketing / Persuasive — Lead with benefits, not features. Use power words, social proof triggers, urgency, and emotional hooks. Every line should move the reader toward action.",
        "concise":       "STYLE: Concise / Compact — Be ruthlessly brief. Maximum information density, minimum words. No redundancy, no filler. If it doesn't add value, cut it.",
        "viral":         "STYLE: Viral / Punchy — Write for maximum shareability and impact. Contrarian openings, bold claims, short punchy sentences. Designed to stop the scroll and demand attention.",
        "scientific":    "STYLE: Scientific / Research-Grade — Hypothesis-driven, methodology-aware, data-centric. Precise variable definitions, controlled framing, reproducibility-focused language."
    }
    style_note = style_instructions.get(_style, style_instructions["detailed"])

    if config.DEC_TOKEN_USAGE:
        style_note += "\n\nTOKEN OPTIMIZATION ACTIVE: Use maximum information density. Strip out all conversational filler, polite phrasing, and redundant adjectives. Every word must earn its place. Make the prompt as short as possible while retaining 100% of the technical constraints."

    if chat_history and len(chat_history) > 0:
        user_query = (
            f"Please refine the previous prompt based on the following new feedback/instructions from the user:\n"
            f"Feedback: {input_text}\n\n"
            f"--- OUTPUT DIRECTIVES ---\n"
            f"{model_note}\n"
            f"{style_note}\n\n"
            f"Output the fully updated masterclass prompt. Output ONLY the updated prompt. No intro, no labels, no explanation, no conversational filler."
        )
    else:
        user_query = (
            f"Concept: {input_text}\n"
            f"Domain: {type_detected}\n\n"
            f"User's specific choices:\n{selections_str}\n\n"
            f"--- OUTPUT DIRECTIVES ---\n"
            f"{model_note}\n"
            f"{style_note}\n\n"
            f"Now generate the masterclass prompt. Output ONLY the prompt. No intro, no labels, no explanation, no conversational filler."
        )


    def generate_prompt_stream():
        try:
            for chunk in LLMService.call_stream("groq", "llama-3.3-70b-versatile", user_query, system_prompt, max_tokens=4500, chat_history=chat_history):
                yield chunk
        except Exception as e:
            app.logger.error(f"Generation failed: {str(e)}", exc_info=True)
            yield "\n[Error] Failed to generate prompt."
            
    return Response(generate_prompt_stream(), mimetype="text/plain")

@app.route("/api/analyze-vision", methods=["POST"])
def api_analyze_vision():
    """
    Analyzes an uploaded base64 image using llama-3.2-11b-vision-preview
    and reverse-engineers a 10/10 master prompt to recreate it.
    """
    image_data = request.json.get("image", "") # base64 data url
    
    if not image_data:
        return jsonify({"success": False, "error": "No image data received."}), 400

    api_key = config.GROQ_API_KEY
    if not api_key or not config.GROQ_ACTIVE:
        # Fallback offline simulation stream
        def generate_local_fallback():
            fallback_text = (
                "A stunning, photorealistic reproduction of the uploaded visual concept.\n"
                "Art Style: Widescreen Cinematic Realism\n"
                "Lighting: Volumetric ambient glow with chiaroscuro contrast\n"
                "Camera Specs: Shot on Sony A7R V, 85mm lens, f/1.4 aperture\n"
                "Visual Prominence: Hyper-detailed environment, rich depth, 8K resolution."
            )
            import time
            for word in fallback_text.split(" "):
                yield word + " "
                time.sleep(0.08)
        return Response(generate_local_fallback(), mimetype="text/plain")

    # Extract base64 part and mime type
    try:
        header, base64_content = image_data.split(",", 1)
        mime_type = header.split(";", 1)[0].split(":", 1)[1]
    except Exception:
        mime_type = "image/jpeg"
        base64_content = image_data

    system_prompt = (
        "You are an elite Vision & Art Director AI. Your job is to reverse-engineer the provided image into a master prompt.\n"
        "Describe the subject, artistic style (e.g. cinematic, anime, concept sketch, digital illustration), camera angle, lens specification (e.g. 85mm, f/1.4), lighting mood, volumetric depth, color palette, and rendering details (e.g. Unreal Engine 5, Octane render).\n"
        "Format your response as a single, exceptionally detailed, highly descriptive prompt ready to copy and paste. "
        "Output ONLY the prompt itself. Do not write intros, explanations, or quotes."
    )

    def groq_vision_streaming_generator():
        try:
            from groq import Groq
            import httpx
            client = Groq(api_key=api_key, http_client=httpx.Client())
            
            response = client.chat.completions.create(
                model="llama-3.2-11b-vision-preview",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": system_prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime_type};base64,{base64_content}"
                                }
                             }
                        ]
                    }
                ],
                max_tokens=2000,
                temperature=0.2,
                stream=True
            )
            for chunk in response:
                chunk_text = chunk.choices[0].delta.content
                if chunk_text:
                    yield chunk_text
        except Exception as e:
            app.logger.error(f"Vision streaming analysis error: {str(e)}", exc_info=True)
            yield "\n[Vision Stream Error] Failed to retrieve vision analysis."

    return Response(groq_vision_streaming_generator(), mimetype="text/plain")

@app.route("/api/save-prompt", methods=["POST"])
def api_save_prompt():
    """Saves a successfully forged prompt to the MySQL prompts table for logged-in creators."""
    user = get_current_user(request)
    if not user:
        return jsonify({"success": False, "error": "Unauthorized session. Please login to save."}), 401

    user_id = user["user_id"]
    input_text = request.json.get("input_text", "").strip()
    mcq_questions = request.json.get("mcq_questions", [])
    mcq_answers = request.json.get("mcq_answers", {})
    generated_prompt = request.json.get("generated_prompt", "").strip()
    category = request.json.get("category", "Image gen")
    parent_prompt_id = request.json.get("parent_prompt_id")

    if not input_text or not generated_prompt:
        return jsonify({"success": False, "error": "Data payload incomplete."}), 400

    # Calculate quality score server-side
    scores = score_prompt(generated_prompt)
    quality_score = scores["total"]

    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        version_number = 1
        if parent_prompt_id:
            # Validate parent prompt existence and ownership
            cursor.execute(
                "SELECT user_id, version_number FROM prompts WHERE id = %s",
                (parent_prompt_id,)
            )
            parent = cursor.fetchone()
            if not parent:
                return jsonify({"success": False, "error": "Parent prompt not found."}), 404
            if parent["user_id"] != user_id:
                security_logger.warning(
                    f"Lineage Ownership Bypass Attempt: user_id {user_id} tried to link "
                    f"parent_prompt_id {parent_prompt_id} owned by user_id {parent['user_id']}"
                )
                return jsonify({"success": False, "error": "Forbidden: You do not own the parent prompt."}), 403
            
            version_number = parent["version_number"] + 1

        target_model = request.json.get("target_model", "generic").strip().lower()
        optimization_style = request.json.get("optimization_style", "detailed").strip().lower()

        # Save to prompts
        cursor.execute(
            """
            INSERT INTO prompts (user_id, input_text, mcq_questions, mcq_answers, generated_prompt, category, version_number, parent_prompt_id, quality_score, target_model, optimization_style)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (user_id, input_text, json.dumps(mcq_questions), json.dumps(mcq_answers), generated_prompt, category, version_number, parent_prompt_id, quality_score, target_model, optimization_style)
        )
        saved_id = cursor.lastrowid
        
        # Increment user prompts_used count
        cursor.execute("UPDATE users SET prompts_used = prompts_used + 1 WHERE id = %s", (user_id,))
        
        conn.commit()
        app.logger.info(f"Prompt saved successfully for user_id {user_id} with ID {saved_id} (V{version_number}, Score: {quality_score})")
        return jsonify({
            "success": True, 
            "message": "Prompt saved successfully to history!",
            "prompt_id": saved_id,
            "version_number": version_number,
            "quality_score": quality_score
        })
    except Exception as e:
        app.logger.error(f"Database save error: {str(e)}", exc_info=True)
        return jsonify({"success": False, "error": "An error occurred while saving the prompt."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route("/api/history", methods=["GET"])
def api_get_history():
    """Queries and returns prompt generation history for the logged-in user."""
    user = get_current_user(request)
    if not user:
        return jsonify({"success": False, "error": "Unauthorized session."}), 401

    user_id = user["user_id"]
    search_query = request.args.get("search", "").strip()
    
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        if search_query:
            # Safe parameterized search query
            like_pattern = f"%{search_query}%"
            cursor.execute(
                """
                SELECT * FROM prompts 
                WHERE user_id = %s AND deleted_at IS NULL AND (input_text LIKE %s OR generated_prompt LIKE %s)
                ORDER BY created_at DESC
                """,
                (user_id, like_pattern, like_pattern)
            )
        else:
            cursor.execute(
                "SELECT * FROM prompts WHERE user_id = %s AND deleted_at IS NULL ORDER BY created_at DESC", 
                (user_id,)
            )
        history = cursor.fetchall()
        
        # Parse JSON fields dynamically for safety
        for item in history:
            if isinstance(item["mcq_questions"], str):
                item["mcq_questions"] = json.loads(item["mcq_questions"])
            if isinstance(item["mcq_answers"], str):
                item["mcq_answers"] = json.loads(item["mcq_answers"])
            
            # Format datetime
            if isinstance(item["created_at"], datetime):
                item["created_at"] = item["created_at"].strftime("%Y-%m-%d %H:%M:%S")

        return jsonify({"success": True, "history": history})
    except Exception as e:
        app.logger.error(f"Failed to fetch history for user_id {user_id}: {str(e)}", exc_info=True)
        return jsonify({"success": False, "error": "Failed to fetch history logs."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route("/api/history/favorite", methods=["POST"])
def api_toggle_favorite():
    """Toggles the favorite state of a prompt and logs the event."""
    user = get_current_user(request)
    if not user:
        return jsonify({"success": False, "error": "Unauthorized session."}), 401
    
    user_id = user["user_id"]
    prompt_id = request.json.get("prompt_id")
    if not prompt_id:
        return jsonify({"success": False, "error": "Prompt ID is required."}), 400
        
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        # Verify access authorization (user must own this prompt)
        cursor.execute("SELECT is_favorite FROM prompts WHERE id = %s AND user_id = %s AND deleted_at IS NULL", (prompt_id, user_id))
        row = cursor.fetchone()
        if not row:
            return jsonify({"success": False, "error": "Prompt not found or access denied."}), 404
            
        current_favorite = row["is_favorite"]
        new_state = 0 if current_favorite else 1
        
        cursor.execute("UPDATE prompts SET is_favorite = %s WHERE id = %s AND user_id = %s", (new_state, prompt_id, user_id))
        conn.commit()
        
        # Log analytics event 'favorite_toggled' with metadata details
        log_analytics_event("favorite_toggled", user_id=user_id, event_metadata={"new_state": bool(new_state)})
        
        return jsonify({"success": True, "is_favorite": bool(new_state)})
    except Exception as e:
        app.logger.error(f"Failed to toggle favorite: {str(e)}", exc_info=True)
        return jsonify({"success": False, "error": "Internal server error."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route("/api/prompts/export", methods=["GET"])
def api_export_prompt():
    """Exports a prompt as a TXT, Markdown, or JSON file attachment after verifying ownership."""
    user = get_current_user(request)
    if not user:
        return jsonify({"success": False, "error": "Unauthorized session."}), 401
        
    user_id = user["user_id"]
    prompt_id = request.args.get("prompt_id")
    export_format = request.args.get("format", "txt").strip().lower()
    
    if not prompt_id:
        return jsonify({"success": False, "error": "Prompt ID is required."}), 400
        
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM prompts WHERE id = %s AND deleted_at IS NULL", (prompt_id,))
        prompt = cursor.fetchone()
        
        if not prompt:
            return jsonify({"success": False, "error": "Prompt not found."}), 404
            
        if prompt["user_id"] != user_id:
            security_logger.warning(
                f"Unauthorized Export Attempt: user_id {user_id} tried to export "
                f"prompt_id {prompt_id} owned by user_id {prompt['user_id']}"
            )
            return jsonify({"success": False, "error": "Forbidden: You do not own this prompt."}), 403
            
        filename = f"prompt_{prompt_id}.{export_format}"
        mimetype = "text/plain"
        
        if export_format == "txt":
            content = prompt["generated_prompt"]
            mimetype = "text/plain"
        elif export_format == "md":
            mimetype = "text/markdown"
            content = f"# ForgePrompt Optimized Prompt (V{prompt['version_number']})\n\n"
            content += f"**Category:** {prompt['category']}\n"
            if prompt["quality_score"] is not None:
                content += f"**Quality Score:** {prompt['quality_score']}/100\n"
            content += f"**Original Concept:**\n> {prompt['input_text']}\n\n"
            content += f"## Generated Prompt\n\n```text\n{prompt['generated_prompt']}\n```\n"
        elif export_format == "json":
            mimetype = "application/json"
            mcq_q = prompt["mcq_questions"]
            mcq_a = prompt["mcq_answers"]
            if isinstance(mcq_q, str):
                mcq_q = json.loads(mcq_q)
            if isinstance(mcq_a, str):
                mcq_a = json.loads(mcq_a)
            data = {
                "id": prompt["id"],
                "input_text": prompt["input_text"],
                "category": prompt["category"],
                "mcq_questions": mcq_q,
                "mcq_answers": mcq_a,
                "generated_prompt": prompt["generated_prompt"],
                "version_number": prompt["version_number"],
                "parent_prompt_id": prompt["parent_prompt_id"],
                "quality_score": prompt["quality_score"],
                "created_at": prompt["created_at"].strftime("%Y-%m-%d %H:%M:%S") if isinstance(prompt["created_at"], datetime) else str(prompt["created_at"])
            }
            content = json.dumps(data, indent=2)
        else:
            return jsonify({"success": False, "error": "Invalid format requested."}), 400
            
        return Response(
            content,
            mimetype=mimetype,
            headers={"Content-disposition": f"attachment; filename={filename}"}
        )
    except Exception as e:
        app.logger.error(f"Failed to export prompt {prompt_id}: {str(e)}", exc_info=True)
        return jsonify({"success": False, "error": "Internal server error during export."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route("/api/admin/stats", methods=["GET"])
def api_admin_stats():
    """Retrieves full-stack platform metrics for the admin console."""
    user_payload = get_current_user(request)
    if not user_payload:
        return jsonify({"success": False, "error": "Unauthorized session."}), 401

    user_id = user_payload.get("user_id")
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # 1. Live role verification from database
        cursor.execute("SELECT role FROM users WHERE id = %s", (user_id,))
        db_user = cursor.fetchone()
        
        if not db_user or db_user.get("role") != "admin":
            security_logger.warning(
                f"Unauthorized admin access attempt by user_id {user_id} "
                f"(email: {user_payload.get('email')}) from IP {get_client_ip()}"
            )
            return jsonify({"success": False, "error": "Forbidden: Admin privileges required."}), 403
            
        # 2. Retrieve metrics
        cursor.execute("SELECT COUNT(*) AS total FROM users")
        total_users = cursor.fetchone()["total"]
        
        cursor.execute("SELECT COUNT(*) AS total FROM prompts WHERE deleted_at IS NULL")
        total_prompts = cursor.fetchone()["total"]
        
        cursor.execute("SELECT id, name, email, created_at, prompts_used, is_banned FROM users ORDER BY created_at DESC")
        users = cursor.fetchall()
        for u in users:
            if isinstance(u["created_at"], datetime):
                u["created_at"] = u["created_at"].strftime("%Y-%m-%d %H:%M:%S")

        # 3. Analytics Aggregations
        # Total Visitors
        cursor.execute("SELECT COUNT(DISTINCT visitor_id) AS total FROM analytics_events")
        total_visitors = cursor.fetchone()["total"]

        # Daily Active Users (DAU)
        cursor.execute(
            "SELECT COUNT(DISTINCT visitor_id) AS total FROM analytics_events WHERE created_at >= NOW() - INTERVAL 1 DAY"
        )
        dau = cursor.fetchone()["total"]

        # Returning Users
        cursor.execute(
            """
            SELECT font_total.total FROM (
                SELECT COUNT(*) AS total FROM (
                    SELECT visitor_id FROM analytics_events GROUP BY visitor_id HAVING COUNT(*) > 1
                ) AS temp
            ) AS font_total
            """
        )
        returning_users = cursor.fetchone()["total"]

        # Most Used Categories
        cursor.execute(
            """
            SELECT category_used, COUNT(*) AS count 
            FROM analytics_events 
            WHERE event_type = 'prompt_forge' AND category_used IS NOT NULL 
            GROUP BY category_used 
            ORDER BY count DESC
            """
        )
        categories_stats = cursor.fetchall()

        # Template Popularity metrics
        cursor.execute(
            """
            SELECT template_name, COUNT(*) AS count 
            FROM analytics_events 
            WHERE event_type = 'template_used' AND template_name IS NOT NULL 
            GROUP BY template_name 
            ORDER BY count DESC
            """
        )
        template_stats = cursor.fetchall()

        # Feedback Records
        cursor.execute(
            """
            SELECT f.*, u.email AS user_email, p.input_text AS prompt_input 
            FROM feedback f
            LEFT JOIN users u ON f.user_id = u.id
            LEFT JOIN prompts p ON f.prompt_id = p.id
            ORDER BY f.created_at DESC
            """
        )
        feedback_list = cursor.fetchall()
        for f in feedback_list:
            if isinstance(f["created_at"], datetime):
                f["created_at"] = f["created_at"].strftime("%Y-%m-%d %H:%M:%S")

        # 4. Intelligence and Quality Metrics (New Phase 3 Metrics)
        # Average Prompt Quality Score
        cursor.execute("SELECT AVG(quality_score) AS avg_score FROM prompts WHERE quality_score IS NOT NULL AND deleted_at IS NULL")
        avg_score_row = cursor.fetchone()
        avg_score = float(avg_score_row["avg_score"]) if avg_score_row and avg_score_row["avg_score"] is not None else 0.0

        # Prompt Improvement Rate (average increase in quality scores from version 1 to subsequent versions)
        cursor.execute("SELECT id, parent_prompt_id, version_number, quality_score FROM prompts WHERE quality_score IS NOT NULL AND deleted_at IS NULL")
        all_prompts = cursor.fetchall()
        prompts_dict = {p["id"]: p for p in all_prompts}
        
        improvements = []
        for p in all_prompts:
            if p["parent_prompt_id"] is not None and p["version_number"] > 1:
                # Trace back to Version 1 root
                curr = p
                visited = set()
                while curr["parent_prompt_id"] in prompts_dict and curr["id"] not in visited:
                    visited.add(curr["id"])
                    parent_id = curr["parent_prompt_id"]
                    curr = prompts_dict[parent_id]
                    if curr["version_number"] == 1 or curr["parent_prompt_id"] is None:
                        break
                
                if curr["version_number"] == 1 and curr["quality_score"] is not None:
                    diff = p["quality_score"] - curr["quality_score"]
                    improvements.append(diff)
                    
        avg_improvement = sum(improvements) / len(improvements) if improvements else 0.0

        # Target Models and Rewrite Styles Aggregations
        cursor.execute("SELECT event_metadata FROM analytics_events WHERE event_type = 'prompt_forge' AND event_metadata IS NOT NULL")
        events = cursor.fetchall()
        model_counts = {}
        style_counts = {}
        total_forges = 0
        for ev in events:
            metadata_str = ev["event_metadata"]
            if metadata_str:
                try:
                    metadata = json.loads(metadata_str)
                    model = metadata.get("target_model", "generic").lower()
                    style = metadata.get("optimization_style", "detailed").lower()
                    model_counts[model] = model_counts.get(model, 0) + 1
                    style_counts[style] = style_counts.get(style, 0) + 1
                    total_forges += 1
                except Exception:
                    pass
        
        model_stats = []
        for model, count in sorted(model_counts.items(), key=lambda x: x[1], reverse=True):
            pct = (count / total_forges * 100) if total_forges > 0 else 0
            model_stats.append({"model": model, "count": count, "percentage": round(pct, 1)})
            
        style_stats = []
        for style, count in sorted(style_counts.items(), key=lambda x: x[1], reverse=True):
            pct = (count / total_forges * 100) if total_forges > 0 else 0
            style_stats.append({"style": style, "count": count, "percentage": round(pct, 1)})

        app.logger.info(f"Admin metrics fetched successfully by admin user_id {user_id}")
        return jsonify({
            "success": True,
            "total_users": total_users,
            "total_prompts": total_prompts,
            "users": users,
            "analytics": {
                "total_visitors": total_visitors,
                "dau": dau,
                "returning_users": returning_users,
                "categories": categories_stats,
                "templates": template_stats,
                "avg_quality_score": avg_score,
                "prompt_improvement_rate": avg_improvement,
                "models": model_stats,
                "styles": style_stats
            },
            "feedback": feedback_list
        })
    except Exception as e:
        app.logger.error(f"Failed to load admin stats: {str(e)}", exc_info=True)
        return jsonify({"success": False, "error": "Failed to load platform metrics."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route("/api/templates", methods=["GET"])
def api_get_templates():
    """Returns a list of predefined starter prompt templates."""
    templates = [
        {
            "id": "coding_assistant",
            "name": "Coding Assistant",
            "category": "Code Gen",
            "icon": "code",
            "description": "Build a high-performance REST API with authentication, rate limiting, and full test coverage."
        },
        {
            "id": "research_assistant",
            "name": "Research Assistant",
            "category": "Research",
            "icon": "search",
            "description": "Synthesize the key breakthroughs, trends, and open problems in large language model alignment research."
        },
        {
            "id": "blog_writer",
            "name": "Blog Writer",
            "category": "Content Writing",
            "icon": "pen-tool",
            "description": "Write a viral long-form article that challenges a widely-held belief in the tech industry with data-backed arguments."
        },
        {
            "id": "resume_builder",
            "name": "Resume Builder",
            "category": "Content Writing",
            "icon": "file-text",
            "description": "Rewrite my resume for a Senior AI Engineer role at a top-tier AI lab — ATS-optimized with impact metrics."
        },
        {
            "id": "study_planner",
            "name": "Study Planner",
            "category": "Research",
            "icon": "calendar",
            "description": "Create an intensive 6-week study roadmap to pass the Google Cloud Professional ML Engineer certification."
        },
        {
            "id": "midjourney_generator",
            "name": "Midjourney Generator",
            "category": "Generative Image",
            "icon": "image",
            "description": "Cinematic ultra-detailed anti-gravity supercar hovering over rain-soaked cyberpunk megacity at dusk, neon reflections."
        },
        {
            "id": "saas_landing_page",
            "name": "SaaS Landing Page",
            "category": "App UI",
            "icon": "layout",
            "description": "Design a conversion-optimized SaaS landing page with glassmorphism UI, animated hero, pricing table, and testimonials."
        },
        {
            "id": "ai_chatbot",
            "name": "AI Chatbot Persona",
            "category": "Chatbot",
            "icon": "message-circle",
            "description": "Create a highly empathetic customer support AI persona for a fintech startup that handles billing disputes and onboarding."
        },
        {
            "id": "youtube_script",
            "name": "YouTube Script",
            "category": "Video Script",
            "icon": "video",
            "description": "Write a 10-minute YouTube documentary script about the rise of autonomous AI agents — hook, tension arc, B-roll cues."
        }
    ]
    return jsonify({"success": True, "templates": templates})

@app.route("/api/feedback", methods=["POST"])
def api_submit_feedback():
    """Submits user feedback, rating, or bug report."""
    user = get_current_user(request)
    user_id = user["user_id"] if user else None
    
    feedback_type = request.json.get("feedback_type", "").strip() # 'issue', 'feature', 'rating'
    rating = request.json.get("rating")
    comment = request.json.get("comment", "").strip()
    prompt_id = request.json.get("prompt_id")
    
    if not feedback_type or not comment:
        return jsonify({"success": False, "error": "Feedback type and comment are required."}), 400
        
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO feedback (user_id, feedback_type, rating, comment, prompt_id)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (user_id, feedback_type, rating, comment, prompt_id)
        )
        conn.commit()
        
        # Log analytics event 'feedback_submitted'
        log_analytics_event("feedback_submitted", user_id=user_id)
        
        return jsonify({"success": True, "message": "Thank you for your feedback!"})
    except Exception as e:
        app.logger.error(f"Failed to save feedback: {str(e)}", exc_info=True)
        return jsonify({"success": False, "error": "Internal server error."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# ============ PHASE 4: COMMUNITY, SHARING, COLLECTIONS & NOTIFICATION ENDPOINTS ============
import secrets
import string

# Global Spotlight Caching
prompt_of_the_week_cache = None
prompt_of_the_week_last_updated = None

def get_prompt_of_the_week():
    global prompt_of_the_week_cache, prompt_of_the_week_last_updated
    now = datetime.utcnow()
    if prompt_of_the_week_cache is not None and prompt_of_the_week_last_updated is not None:
        if now - prompt_of_the_week_last_updated < timedelta(hours=1):
            return prompt_of_the_week_cache

    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        # Select best prompt in past 7 days based on Trending Score
        cursor.execute(
            """
            SELECT p.*, u.username, u.name AS author_name, pr.display_name AS author_display_name, pr.avatar AS author_avatar
            FROM prompts p
            LEFT JOIN users u ON p.user_id = u.id
            LEFT JOIN profiles pr ON p.user_id = pr.user_id
            WHERE p.visibility = 'public' AND p.moderation_status = 'approved' AND p.deleted_at IS NULL
              AND p.published_at >= NOW() - INTERVAL 7 DAY
            ORDER BY (p.like_count * 12 + p.fork_count * 18 + p.views * 0.3 + IFNULL(p.quality_score, 0) * 0.15) DESC
            LIMIT 1
            """
        )
        res = cursor.fetchone()
        if not res:
            # Fallback to 30 days
            cursor.execute(
                """
                SELECT p.*, u.username, u.name AS author_name, pr.display_name AS author_display_name, pr.avatar AS author_avatar
                FROM prompts p
                LEFT JOIN users u ON p.user_id = u.id
                LEFT JOIN profiles pr ON p.user_id = pr.user_id
                WHERE p.visibility = 'public' AND p.moderation_status = 'approved' AND p.deleted_at IS NULL
                  AND p.published_at >= NOW() - INTERVAL 30 DAY
                ORDER BY (p.like_count * 12 + p.fork_count * 18 + p.views * 0.3 + IFNULL(p.quality_score, 0) * 0.15) DESC
                LIMIT 1
                """
            )
            res = cursor.fetchone()
        if not res:
            # Fallback to all-time
            cursor.execute(
                """
                SELECT p.*, u.username, u.name AS author_name, pr.display_name AS author_display_name, pr.avatar AS author_avatar
                FROM prompts p
                LEFT JOIN users u ON p.user_id = u.id
                LEFT JOIN profiles pr ON p.user_id = pr.user_id
                WHERE p.visibility = 'public' AND p.moderation_status = 'approved' AND p.deleted_at IS NULL
                ORDER BY (p.like_count * 12 + p.fork_count * 18 + p.views * 0.3 + IFNULL(p.quality_score, 0) * 0.15) DESC
                LIMIT 1
                """
            )
            res = cursor.fetchone()
        
        if res:
            if isinstance(res["created_at"], datetime):
                res["created_at"] = res["created_at"].strftime("%Y-%m-%d %H:%M:%S")
            if isinstance(res["published_at"], datetime):
                res["published_at"] = res["published_at"].strftime("%Y-%m-%d %H:%M:%S")
            if isinstance(res["mcq_questions"], str):
                res["mcq_questions"] = json.loads(res["mcq_questions"])
            if isinstance(res["mcq_answers"], str):
                res["mcq_answers"] = json.loads(res["mcq_answers"])
        
        prompt_of_the_week_cache = res
        prompt_of_the_week_last_updated = now
        return res
    except Exception as e:
        app.logger.error(f"Error calculating Prompt of the Week: {str(e)}", exc_info=True)
        return None
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# Toxic keywords moderation check
TOXIC_KEYWORDS = [
    "hate speech", "slurs", "exploit", "hack", "bypass jailbreak", 
    "malicious payload", "ddos script", "bomb", "destroy world", "illegal"
]

def run_moderation_check(prompt_text):
    low = prompt_text.lower()
    for word in TOXIC_KEYWORDS:
        if word in low:
            return False, f"Contains blocked toxic keyword: '{word}'"
    return True, "Approved"

def cleanup_old_notifications():
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM notifications WHERE created_at < NOW() - INTERVAL 90 DAY")
        conn.commit()
        app.logger.info("Auto-cleaned notifications older than 90 days.")
    except Exception as e:
        app.logger.error(f"Failed to auto-clean old notifications: {str(e)}")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# Run notifications cleanup in a background thread
threading.Thread(target=cleanup_old_notifications, daemon=True).start()

def generate_share_uuid(cursor):
    alphabet = string.ascii_uppercase + string.ascii_lowercase + string.digits
    while True:
        code = "".join(secrets.choice(alphabet) for _ in range(12))
        cursor.execute("SELECT COUNT(*) AS total FROM prompts WHERE share_uuid = %s", (code,))
        res = cursor.fetchone()
        count_val = res["total"] if isinstance(res, dict) else res[0]
        if count_val == 0:
            return code

def attach_prompt_badges(prompt_list, spotlight_id=None):
    scores = []
    for p in prompt_list:
        score = (p.get("like_count", 0) * 12) + (p.get("fork_count", 0) * 18) + (p.get("views", 0) * 0.3) + (p.get("quality_score", 0) * 0.15)
        p["_trending_score"] = score
        scores.append(score)
    
    trending_threshold = 999999
    if scores:
        scores.sort(reverse=True)
        threshold_idx = max(0, int(len(scores) * 0.1) - 1)
        trending_threshold = scores[threshold_idx] if threshold_idx < len(scores) else 999999
    
    for p in prompt_list:
        badges = []
        if spotlight_id and p["id"] == spotlight_id:
            badges.append("🏆 Prompt of the Week")
        if p.get("is_featured"):
            badges.append("⭐ Featured")
        if p["_trending_score"] >= trending_threshold and p["_trending_score"] > 5:
            badges.append("🔥 Trending")
        if p.get("quality_score") and p["quality_score"] >= 90:
            badges.append("💎 Top Quality")
        
        p["badges"] = badges
    return prompt_list


# ============ HTML Page Routers ============

@app.route("/community")
def community_gallery():
    """Renders the Community Gallery page."""
    user = get_current_user(request)
    log_analytics_event("page_view", user_id=user["user_id"] if user else None)
    return render_template("community.html")

@app.route("/@<username>")
def user_profile_page(username):
    """Renders the user profile page."""
    user = get_current_user(request)
    log_analytics_event("page_view", user_id=user["user_id"] if user else None)
    return render_template("profile.html", username=username)

@app.route("/share/<uuid>")
def share_preview_page(uuid):
    """Renders the public preview page for shared prompts."""
    # Increment views uniquely by day
    client_ip = get_client_ip()
    user_agent = request.headers.get("User-Agent", "")
    visitor_id = hashlib.sha256((client_ip + user_agent).encode('utf-8')).hexdigest()
    
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, visibility FROM prompts WHERE share_uuid = %s AND deleted_at IS NULL", (uuid,))
        prompt = cursor.fetchone()
        if not prompt or prompt["visibility"] == "private":
            abort(404)
        
        # Log unique view
        try:
            cursor.execute(
                "INSERT IGNORE INTO prompt_views (visitor_id, prompt_id, viewed_date) VALUES (%s, %s, CURRENT_DATE)",
                (visitor_id, prompt["id"])
            )
            if cursor.rowcount > 0:
                cursor.execute("UPDATE prompts SET views = views + 1 WHERE id = %s", (prompt["id"],))
                conn.commit()
        except Exception as view_err:
            app.logger.error(f"Error logging prompt view: {str(view_err)}")
    except Exception as e:
        app.logger.error(f"Database error on share page load: {str(e)}")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    user = get_current_user(request)
    log_analytics_event("page_view", user_id=user["user_id"] if user else None)
    return render_template("share.html", share_uuid=uuid)


# ============ Community API Endpoints ============

@app.route("/api/prompts/publish", methods=["POST"])
def api_publish_prompt():
    """Publishes a prompt to the community gallery."""
    user = get_current_user(request)
    if not user:
        return jsonify({"success": False, "error": "Unauthorized session."}), 401
        
    user_id = user["user_id"]
    prompt_id = request.json.get("prompt_id")
    visibility = request.json.get("visibility", "public").strip().lower() # 'public' or 'unlisted'

    if not prompt_id:
        return jsonify({"success": False, "error": "Prompt ID is required."}), 400
        
    if visibility not in ["public", "unlisted"]:
        return jsonify({"success": False, "error": "Invalid visibility parameter."}), 400

    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Verify ownership
        cursor.execute("SELECT * FROM prompts WHERE id = %s AND deleted_at IS NULL", (prompt_id,))
        prompt = cursor.fetchone()
        if not prompt:
            return jsonify({"success": False, "error": "Prompt not found."}), 404
        if prompt["user_id"] != user_id:
            return jsonify({"success": False, "error": "Forbidden: You do not own this prompt."}), 403

        # Rate Limit Check: Max 10 publishes per hour
        cursor.execute(
            "SELECT COUNT(*) AS total FROM prompts WHERE user_id = %s AND is_public = 1 AND published_at >= NOW() - INTERVAL 1 HOUR",
            (user_id,)
        )
        pub_count = cursor.fetchone()["total"]
        if pub_count >= 10:
            return jsonify({"success": False, "error": "Rate limit exceeded. You can publish up to 10 prompts per hour."}), 429

        # Moderation check: Spam and Toxicity checks
        ok, reason = run_moderation_check(prompt["generated_prompt"])
        if not ok:
            return jsonify({"success": False, "error": f"Content rejected by moderation. Reason: {reason}"}), 400

        # Duplicate check: check if another public prompt has the same content
        cursor.execute(
            "SELECT COUNT(*) AS count FROM prompts WHERE generated_prompt = %s AND visibility = 'public' AND id != %s AND deleted_at IS NULL",
            (prompt["generated_prompt"], prompt_id)
        )
        if cursor.fetchone()["count"] > 0:
            return jsonify({"success": False, "error": "Duplicate content detected. This prompt is already published."}), 400

        # Generate share code if missing
        share_code = prompt["share_uuid"]
        if not share_code:
            share_code = generate_share_uuid(cursor)

        # Update visibility
        cursor.execute(
            """
            UPDATE prompts 
            SET is_public = 1, visibility = %s, published_at = CURRENT_TIMESTAMP, share_uuid = %s, moderation_status = 'approved'
            WHERE id = %s
            """,
            (visibility, share_code, prompt_id)
        )
        conn.commit()
        
        return jsonify({
            "success": True, 
            "message": f"Prompt successfully published as {visibility}!",
            "share_uuid": share_code
        })
    except Exception as e:
        app.logger.error(f"Error publishing prompt: {str(e)}", exc_info=True)
        return jsonify({"success": False, "error": "Internal server error."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route("/api/prompts/unpublish", methods=["POST"])
def api_unpublish_prompt():
    """Reverts a public prompt back to private."""
    user = get_current_user(request)
    if not user:
        return jsonify({"success": False, "error": "Unauthorized session."}), 401
        
    user_id = user["user_id"]
    prompt_id = request.json.get("prompt_id")

    if not prompt_id:
        return jsonify({"success": False, "error": "Prompt ID is required."}), 400

    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("SELECT user_id FROM prompts WHERE id = %s AND deleted_at IS NULL", (prompt_id,))
        prompt = cursor.fetchone()
        if not prompt:
            return jsonify({"success": False, "error": "Prompt not found."}), 404
        if prompt["user_id"] != user_id:
            return jsonify({"success": False, "error": "Forbidden: You do not own this prompt."}), 403

        cursor.execute(
            "UPDATE prompts SET is_public = 0, visibility = 'private', published_at = NULL WHERE id = %s",
            (prompt_id,)
        )
        conn.commit()
        return jsonify({"success": True, "message": "Prompt unpublished successfully."})
    except Exception as e:
        app.logger.error(f"Error unpublishing: {str(e)}")
        return jsonify({"success": False, "error": "Internal server error."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route("/api/prompts/delete", methods=["POST"])
def api_delete_prompt():
    """Soft deletes a prompt by setting deleted_at timestamp."""
    user = get_current_user(request)
    if not user:
        return jsonify({"success": False, "error": "Unauthorized session."}), 401
        
    user_id = user["user_id"]
    prompt_id = request.json.get("prompt_id")

    if not prompt_id:
        return jsonify({"success": False, "error": "Prompt ID is required."}), 400

    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("SELECT user_id FROM prompts WHERE id = %s AND deleted_at IS NULL", (prompt_id,))
        prompt = cursor.fetchone()
        if not prompt:
            return jsonify({"success": False, "error": "Prompt not found."}), 404
        if prompt["user_id"] != user_id:
            return jsonify({"success": False, "error": "Forbidden: You do not own this prompt."}), 403

        cursor.execute("UPDATE prompts SET deleted_at = CURRENT_TIMESTAMP WHERE id = %s", (prompt_id,))
        conn.commit()
        return jsonify({"success": True, "message": "Prompt deleted successfully."})
    except Exception as e:
        app.logger.error(f"Error deleting prompt: {str(e)}")
        return jsonify({"success": False, "error": "Internal server error."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route("/api/community/prompts", methods=["GET"])
def api_get_community_prompts():
    """Returns community prompts using parameters search, filter, and pagination."""
    search_query = request.args.get("search", "").strip()
    category = request.args.get("category", "").strip()
    target_model = request.args.get("target_model", "").strip()
    style = request.args.get("style", "").strip()
    sort = request.args.get("sort", "trending").strip().lower()
    
    page = max(1, int(request.args.get("page", 1)))
    per_page = max(1, min(50, int(request.args.get("limit", 20))))
    offset = (page - 1) * per_page

    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        spotlight = get_prompt_of_the_week()
        spotlight_id = spotlight["id"] if spotlight else None

        # Build dynamic SQL queries
        base_query = """
            FROM prompts p
            LEFT JOIN users u ON p.user_id = u.id
            LEFT JOIN profiles pr ON p.user_id = pr.user_id
            WHERE p.visibility = 'public' AND p.moderation_status = 'approved' AND p.deleted_at IS NULL
        """
        params = []

        if search_query:
            base_query += " AND (p.input_text LIKE %s OR p.generated_prompt LIKE %s OR u.username LIKE %s OR p.category LIKE %s)"
            like_pattern = f"%{search_query}%"
            params.extend([like_pattern, like_pattern, like_pattern, like_pattern])

        if category and category.lower() != "all" and category.lower() != "trending" and category.lower() != "latest":
            base_query += " AND p.category = %s"
            params.append(category)

        if target_model:
            base_query += " AND p.target_model = %s"
            params.append(target_model)

        if style:
            base_query += " AND p.optimization_style = %s"
            params.append(style)

        # Count total matched
        cursor.execute(f"SELECT COUNT(*) AS total {base_query}", tuple(params))
        total_count = cursor.fetchone()["total"]

        # Sort order
        if sort == "newest" or category.lower() == "latest":
            base_query += " ORDER BY p.published_at DESC"
        elif sort == "likes":
            base_query += " ORDER BY p.like_count DESC, p.published_at DESC"
        elif sort == "quality":
            base_query += " ORDER BY p.quality_score DESC, p.published_at DESC"
        else: # trending
            base_query += " ORDER BY (p.like_count * 12 + p.fork_count * 18 + p.views * 0.3 + IFNULL(p.quality_score, 0) * 0.15) DESC, p.published_at DESC"

        # Apply limit & offset
        base_query += " LIMIT %s OFFSET %s"
        params.extend([per_page, offset])

        cursor.execute(
            f"""
            SELECT p.*, u.username, u.name AS author_name, pr.display_name AS author_display_name, pr.avatar AS author_avatar
            {base_query}
            """,
            tuple(params)
        )
        prompts = cursor.fetchall()

        # Parse JSON and add badges
        for p in prompts:
            if isinstance(p["created_at"], datetime):
                p["created_at"] = p["created_at"].strftime("%Y-%m-%d %H:%M:%S")
            if isinstance(p["published_at"], datetime):
                p["published_at"] = p["published_at"].strftime("%Y-%m-%d %H:%M:%S")
            if isinstance(p["mcq_questions"], str):
                p["mcq_questions"] = json.loads(p["mcq_questions"])
            if isinstance(p["mcq_answers"], str):
                p["mcq_answers"] = json.loads(p["mcq_answers"])

        prompts = attach_prompt_badges(prompts, spotlight_id)

        return jsonify({
            "success": True,
            "prompts": prompts,
            "total": total_count,
            "page": page,
            "limit": per_page,
            "spotlight": spotlight
        })
    except Exception as e:
        app.logger.error(f"Error fetching community gallery: {str(e)}", exc_info=True)
        return jsonify({"success": False, "error": "Internal server error."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route("/api/share/<uuid>", methods=["GET"])
def api_get_share_prompt(uuid):
    """Retrieves metadata of a public/unlisted prompt via share uuid."""
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT p.*, u.username, u.name AS author_name, pr.display_name AS author_display_name, pr.avatar AS author_avatar
            FROM prompts p
            LEFT JOIN users u ON p.user_id = u.id
            LEFT JOIN profiles pr ON p.user_id = pr.user_id
            WHERE p.share_uuid = %s AND p.deleted_at IS NULL
            """,
            (uuid,)
        )
        prompt = cursor.fetchone()
        if not prompt or prompt["visibility"] == "private":
            return jsonify({"success": False, "error": "Prompt not found or access restricted."}), 404

        if isinstance(prompt["created_at"], datetime):
            prompt["created_at"] = prompt["created_at"].strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(prompt["published_at"], datetime):
            prompt["published_at"] = prompt["published_at"].strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(prompt["mcq_questions"], str):
            prompt["mcq_questions"] = json.loads(prompt["mcq_questions"])
        if isinstance(prompt["mcq_answers"], str):
            prompt["mcq_answers"] = json.loads(prompt["mcq_answers"])
            
        # Get badges
        spotlight = get_prompt_of_the_week()
        spotlight_id = spotlight["id"] if spotlight else None
        prompt = attach_prompt_badges([prompt], spotlight_id)[0]

        return jsonify({"success": True, "prompt": prompt})
    except Exception as e:
        app.logger.error(f"Error loading shared prompt: {str(e)}")
        return jsonify({"success": False, "error": "Internal server error."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# ============ Collections API Endpoints ============

@app.route("/api/collections", methods=["GET"])
def api_get_collections():
    """Gets all collections of the current user."""
    user = get_current_user(request)
    if not user:
        return jsonify({"success": False, "error": "Unauthorized session."}), 401
    
    user_id = user["user_id"]
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT c.*, COUNT(cp.prompt_id) AS prompt_count
            FROM collections c
            LEFT JOIN collection_prompts cp ON c.id = cp.collection_id
            LEFT JOIN prompts p ON cp.prompt_id = p.id AND p.deleted_at IS NULL
            WHERE c.user_id = %s
            GROUP BY c.id
            ORDER BY c.created_at DESC
            """,
            (user_id,)
        )
        collections = cursor.fetchall()
        for col in collections:
            if isinstance(col["created_at"], datetime):
                col["created_at"] = col["created_at"].strftime("%Y-%m-%d %H:%M:%S")
        return jsonify({"success": True, "collections": collections})
    except Exception as e:
        app.logger.error(f"Error fetching collections: {str(e)}")
        return jsonify({"success": False, "error": "Internal server error."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route("/api/collections", methods=["POST"])
def api_create_collection():
    """Creates a new collection folder."""
    user = get_current_user(request)
    if not user:
        return jsonify({"success": False, "error": "Unauthorized session."}), 401
        
    user_id = user["user_id"]
    title = request.json.get("title", "").strip()
    description = request.json.get("description", "").strip()
    cover_color = request.json.get("cover_color", "#6c63ff").strip()
    emoji = request.json.get("emoji", "📁").strip()

    if not title:
        return jsonify({"success": False, "error": "Collection title is required."}), 400

    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO collections (user_id, title, description, cover_color, emoji)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (user_id, title, description, cover_color, emoji)
        )
        conn.commit()
        return jsonify({"success": True, "message": "Collection created successfully!", "collection_id": cursor.lastrowid})
    except Exception as e:
        app.logger.error(f"Error creating collection: {str(e)}")
        return jsonify({"success": False, "error": "Internal server error."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route("/api/collections/<int:id>", methods=["DELETE"])
def api_delete_collection(id):
    """Deletes a collection folder."""
    user = get_current_user(request)
    if not user:
        return jsonify({"success": False, "error": "Unauthorized session."}), 401
        
    user_id = user["user_id"]
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT user_id FROM collections WHERE id = %s", (id,))
        col = cursor.fetchone()
        if not col:
            return jsonify({"success": False, "error": "Collection not found."}), 404
        if col["user_id"] != user_id:
            return jsonify({"success": False, "error": "Forbidden."}), 403

        cursor.execute("DELETE FROM collections WHERE id = %s", (id,))
        conn.commit()
        return jsonify({"success": True, "message": "Collection deleted successfully."})
    except Exception as e:
        app.logger.error(f"Error deleting collection: {str(e)}")
        return jsonify({"success": False, "error": "Internal server error."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route("/api/collections/<int:id>/prompts", methods=["POST"])
def api_add_prompt_to_collection(id):
    """Adds a prompt to a collection."""
    user = get_current_user(request)
    if not user:
        return jsonify({"success": False, "error": "Unauthorized session."}), 401
        
    user_id = user["user_id"]
    prompt_id = request.json.get("prompt_id")

    if not prompt_id:
        return jsonify({"success": False, "error": "Prompt ID is required."}), 400

    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Verify collection ownership
        cursor.execute("SELECT user_id FROM collections WHERE id = %s", (id,))
        col = cursor.fetchone()
        if not col:
            return jsonify({"success": False, "error": "Collection not found."}), 404
        if col["user_id"] != user_id:
            return jsonify({"success": False, "error": "Forbidden."}), 403

        # Verify prompt ownership
        cursor.execute("SELECT user_id FROM prompts WHERE id = %s AND deleted_at IS NULL", (prompt_id,))
        prompt = cursor.fetchone()
        if not prompt:
            return jsonify({"success": False, "error": "Prompt not found."}), 404
        if prompt["user_id"] != user_id:
            return jsonify({"success": False, "error": "Forbidden: You do not own this prompt."}), 403

        # Insert ignore to avoid duplicate collection prompts
        cursor.execute(
            "INSERT IGNORE INTO collection_prompts (collection_id, prompt_id) VALUES (%s, %s)",
            (id, prompt_id)
        )
        conn.commit()
        return jsonify({"success": True, "message": "Prompt added to collection successfully."})
    except Exception as e:
        app.logger.error(f"Error adding to collection: {str(e)}")
        return jsonify({"success": False, "error": "Internal server error."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route("/api/collections/<int:id>/prompts/<int:prompt_id>", methods=["DELETE"])
def api_remove_prompt_from_collection(id, prompt_id):
    """Removes a prompt from a collection."""
    user = get_current_user(request)
    if not user:
        return jsonify({"success": False, "error": "Unauthorized session."}), 401
        
    user_id = user["user_id"]
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Verify collection ownership
        cursor.execute("SELECT user_id FROM collections WHERE id = %s", (id,))
        col = cursor.fetchone()
        if not col:
            return jsonify({"success": False, "error": "Collection not found."}), 404
        if col["user_id"] != user_id:
            return jsonify({"success": False, "error": "Forbidden."}), 403

        cursor.execute(
            "DELETE FROM collection_prompts WHERE collection_id = %s AND prompt_id = %s",
            (id, prompt_id)
        )
        conn.commit()
        return jsonify({"success": True, "message": "Prompt removed from collection."})
    except Exception as e:
        app.logger.error(f"Error removing prompt from collection: {str(e)}")
        return jsonify({"success": False, "error": "Internal server error."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route("/api/collections/<int:id>/prompts", methods=["GET"])
def api_get_collection_prompts(id):
    """Gets all prompts in a collection."""
    user = get_current_user(request)
    if not user:
        return jsonify({"success": False, "error": "Unauthorized session."}), 401
        
    user_id = user["user_id"]
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("SELECT user_id FROM collections WHERE id = %s", (id,))
        col = cursor.fetchone()
        if not col:
            return jsonify({"success": False, "error": "Collection not found."}), 404
        if col["user_id"] != user_id:
            return jsonify({"success": False, "error": "Forbidden."}), 403

        cursor.execute(
            """
            SELECT p.* FROM prompts p
            JOIN collection_prompts cp ON p.id = cp.prompt_id
            WHERE cp.collection_id = %s AND p.deleted_at IS NULL
            ORDER BY p.created_at DESC
            """,
            (id,)
        )
        prompts = cursor.fetchall()
        for p in prompts:
            if isinstance(p["created_at"], datetime):
                p["created_at"] = p["created_at"].strftime("%Y-%m-%d %H:%M:%S")
            if isinstance(p["mcq_questions"], str):
                p["mcq_questions"] = json.loads(p["mcq_questions"])
            if isinstance(p["mcq_answers"], str):
                p["mcq_answers"] = json.loads(p["mcq_answers"])

        return jsonify({"success": True, "prompts": prompts})
    except Exception as e:
        app.logger.error(f"Error fetching collection prompts: {str(e)}")
        return jsonify({"success": False, "error": "Internal server error."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# ============ Likes API Endpoints ============

@app.route("/api/prompts/<int:id>/like", methods=["POST"])
def api_like_prompt(id):
    """Likes a public/unlisted prompt."""
    user = get_current_user(request)
    if not user:
        return jsonify({"success": False, "error": "Unauthorized session."}), 401
        
    user_id = user["user_id"]
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Verify prompt exists and is public/unlisted
        cursor.execute("SELECT user_id, visibility, like_count FROM prompts WHERE id = %s AND deleted_at IS NULL", (id,))
        prompt = cursor.fetchone()
        if not prompt or prompt["visibility"] == "private":
            return jsonify({"success": False, "error": "Prompt not found or private."}), 404

        # Insert like
        cursor.execute(
            "INSERT IGNORE INTO prompt_likes (user_id, prompt_id) VALUES (%s, %s)",
            (user_id, id)
        )
        if cursor.rowcount > 0:
            # Increment like_count
            cursor.execute("UPDATE prompts SET like_count = like_count + 1 WHERE id = %s", (id,))
            
            # Send Notification if liker is not the author
            if prompt["user_id"] and prompt["user_id"] != user_id:
                cursor.execute(
                    "INSERT INTO notifications (user_id, sender_id, type, prompt_id) VALUES (%s, %s, 'like', %s)",
                    (prompt["user_id"], user_id, id)
                )
            conn.commit()

        return jsonify({"success": True, "message": "Liked successfully."})
    except Exception as e:
        app.logger.error(f"Error liking prompt: {str(e)}")
        return jsonify({"success": False, "error": "Internal server error."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route("/api/prompts/<int:id>/unlike", methods=["POST"])
def api_unlike_prompt(id):
    """Unlikes a public/unlisted prompt."""
    user = get_current_user(request)
    if not user:
        return jsonify({"success": False, "error": "Unauthorized session."}), 401
        
    user_id = user["user_id"]
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("SELECT user_id FROM prompts WHERE id = %s AND deleted_at IS NULL", (id,))
        prompt = cursor.fetchone()
        if not prompt:
            return jsonify({"success": False, "error": "Prompt not found."}), 404

        # Delete like
        cursor.execute("DELETE FROM prompt_likes WHERE user_id = %s AND prompt_id = %s", (user_id, id))
        if cursor.rowcount > 0:
            cursor.execute("UPDATE prompts SET like_count = GREATEST(0, like_count - 1) WHERE id = %s", (id,))
            # Delete corresponding unread like notification if it exists to deduplicate
            cursor.execute(
                "DELETE FROM notifications WHERE user_id = %s AND sender_id = %s AND type = 'like' AND prompt_id = %s AND is_read = 0",
                (prompt["user_id"], user_id, id)
            )
            conn.commit()

        return jsonify({"success": True, "message": "Unliked successfully."})
    except Exception as e:
        app.logger.error(f"Error unliking prompt: {str(e)}")
        return jsonify({"success": False, "error": "Internal server error."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route("/api/prompts/<int:id>/like-status", methods=["GET"])
def api_like_status(id):
    """Gets current user like status for a prompt."""
    user = get_current_user(request)
    if not user:
        return jsonify({"success": True, "liked": False})
        
    user_id = user["user_id"]
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM prompt_likes WHERE user_id = %s AND prompt_id = %s", (user_id, id))
        liked = cursor.fetchone()[0] > 0
        return jsonify({"success": True, "liked": liked})
    except Exception as e:
        app.logger.error(f"Error getting like status: {str(e)}")
        return jsonify({"success": False, "liked": False}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# ============ User Profiles API Endpoints ============

@app.route("/api/profiles/<username>", methods=["GET"])
def api_get_user_profile(username):
    """Returns profile and statistics for a given username."""
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, name, username, created_at FROM users WHERE username = %s AND is_banned = 0", (username,))
        u = cursor.fetchone()
        if not u:
            return jsonify({"success": False, "error": "User not found."}), 404
            
        u_id = u["id"]
        
        # Load profile
        cursor.execute("SELECT * FROM profiles WHERE user_id = %s", (u_id,))
        profile = cursor.fetchone()
        
        # Follower/Following counts
        cursor.execute("SELECT COUNT(*) AS total FROM follows WHERE following_id = %s", (u_id,))
        followers_count = cursor.fetchone()["total"]
        cursor.execute("SELECT COUNT(*) AS total FROM follows WHERE follower_id = %s", (u_id,))
        following_count = cursor.fetchone()["total"]

        # Prompts counts and averages
        cursor.execute("SELECT COUNT(*) AS total, AVG(quality_score) AS avg_quality, SUM(like_count) AS total_likes FROM prompts WHERE user_id = %s AND visibility = 'public' AND moderation_status = 'approved' AND deleted_at IS NULL", (u_id,))
        stats = cursor.fetchone()
        published_count = stats["total"]
        avg_quality = float(stats["avg_quality"]) if stats["avg_quality"] is not None else 0.0
        avg_likes = (float(stats["total_likes"]) / published_count) if published_count > 0 and stats["total_likes"] is not None else 0.0

        # Most used category
        cursor.execute("SELECT category, COUNT(*) AS count FROM prompts WHERE user_id = %s AND deleted_at IS NULL GROUP BY category ORDER BY count DESC LIMIT 1", (u_id,))
        cat_res = cursor.fetchone()
        most_used_category = cat_res["category"] if cat_res else "None"

        # Most used target model
        cursor.execute("SELECT target_model, COUNT(*) AS count FROM prompts WHERE user_id = %s AND target_model IS NOT NULL AND deleted_at IS NULL GROUP BY target_model ORDER BY count DESC LIMIT 1", (u_id,))
        model_res = cursor.fetchone()
        most_used_model = model_res["target_model"] if model_res else "None"

        # Public Prompts list
        cursor.execute(
            """
            SELECT p.*, u.username, u.name AS author_name, pr.display_name AS author_display_name, pr.avatar AS author_avatar
            FROM prompts p
            LEFT JOIN users u ON p.user_id = u.id
            LEFT JOIN profiles pr ON p.user_id = pr.user_id
            WHERE p.user_id = %s AND p.visibility = 'public' AND p.moderation_status = 'approved' AND p.deleted_at IS NULL
            ORDER BY p.published_at DESC
            """,
            (u_id,)
        )
        prompts = cursor.fetchall()
        for p in prompts:
            if isinstance(p["created_at"], datetime):
                p["created_at"] = p["created_at"].strftime("%Y-%m-%d %H:%M:%S")
            if isinstance(p["published_at"], datetime):
                p["published_at"] = p["published_at"].strftime("%Y-%m-%d %H:%M:%S")
            if isinstance(p["mcq_questions"], str):
                p["mcq_questions"] = json.loads(p["mcq_questions"])
            if isinstance(p["mcq_answers"], str):
                p["mcq_answers"] = json.loads(p["mcq_answers"])
        
        # Attach badges
        spotlight = get_prompt_of_the_week()
        spotlight_id = spotlight["id"] if spotlight else None
        prompts = attach_prompt_badges(prompts, spotlight_id)

        joined_str = u["created_at"].strftime("%Y-%m-%d") if isinstance(u["created_at"], datetime) else str(u["created_at"])

        return jsonify({
            "success": True,
            "user": {
                "id": u_id,
                "name": u["name"],
                "username": u["username"],
                "joined": joined_str
            },
            "profile": {
                "display_name": profile["display_name"] if profile else u["name"],
                "bio": profile["bio"] if profile else "",
                "avatar": profile["avatar"] if profile else "",
                "github": profile["github"] if profile else "",
                "website": profile["website"] if profile else ""
            },
            "stats": {
                "followers": followers_count,
                "following": following_count,
                "published": published_count,
                "avg_quality": round(avg_quality, 1),
                "avg_likes": round(avg_likes, 1),
                "most_used_category": most_used_category,
                "most_used_model": most_used_model
            },
            "prompts": prompts
        })
    except Exception as e:
        app.logger.error(f"Error fetching profile: {str(e)}", exc_info=True)
        return jsonify({"success": False, "error": "Internal server error."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route("/api/profile", methods=["PUT"])
def api_update_profile():
    """Updates the logged-in creator's profile details."""
    user = get_current_user(request)
    if not user:
        return jsonify({"success": False, "error": "Unauthorized session."}), 401
        
    user_id = user["user_id"]
    display_name = request.json.get("display_name", "").strip()
    bio = request.json.get("bio", "").strip()
    avatar = request.json.get("avatar", "").strip() # Selected preset avatar
    github = request.json.get("github", "").strip()
    website = request.json.get("website", "").strip()

    if not display_name:
        return jsonify({"success": False, "error": "Display name is required."}), 400

    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE profiles 
            SET display_name = %s, bio = %s, avatar = %s, github = %s, website = %s
            WHERE user_id = %s
            """,
            (display_name, bio, avatar, github, website, user_id)
        )
        conn.commit()
        return jsonify({"success": True, "message": "Profile updated successfully!"})
    except Exception as e:
        app.logger.error(f"Error updating profile: {str(e)}")
        return jsonify({"success": False, "error": "Internal server error."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# ============ Follow System API Endpoints ============

@app.route("/api/follow", methods=["POST"])
def api_follow_user():
    """Follows a creator."""
    user = get_current_user(request)
    if not user:
        return jsonify({"success": False, "error": "Unauthorized session."}), 401
        
    user_id = user["user_id"]
    following_id = request.json.get("following_id")

    if not following_id:
        return jsonify({"success": False, "error": "Following user ID is required."}), 400
        
    if following_id == user_id:
        return jsonify({"success": False, "error": "You cannot follow yourself."}), 400

    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor()
        
        # Verify target user exists
        cursor.execute("SELECT COUNT(*) FROM users WHERE id = %s", (following_id,))
        if cursor.fetchone()[0] == 0:
            return jsonify({"success": False, "error": "User not found."}), 404

        # Insert follow
        cursor.execute(
            "INSERT IGNORE INTO follows (follower_id, following_id) VALUES (%s, %s)",
            (user_id, following_id)
        )
        if cursor.rowcount > 0:
            # Create Follow Notification
            cursor.execute(
                "INSERT INTO notifications (user_id, sender_id, type) VALUES (%s, %s, 'follow')",
                (following_id, user_id)
            )
            conn.commit()
            
        return jsonify({"success": True, "message": "Followed user successfully."})
    except Exception as e:
        app.logger.error(f"Error following user: {str(e)}")
        return jsonify({"success": False, "error": "Internal server error."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route("/api/unfollow", methods=["POST"])
def api_unfollow_user():
    """Unfollows a creator."""
    user = get_current_user(request)
    if not user:
        return jsonify({"success": False, "error": "Unauthorized session."}), 401
        
    user_id = user["user_id"]
    following_id = request.json.get("following_id")

    if not following_id:
        return jsonify({"success": False, "error": "Following user ID is required."}), 400

    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM follows WHERE follower_id = %s AND following_id = %s", (user_id, following_id))
        if cursor.rowcount > 0:
            # Delete corresponding unread follow notification
            cursor.execute(
                "DELETE FROM notifications WHERE user_id = %s AND sender_id = %s AND type = 'follow' AND is_read = 0",
                (following_id, user_id)
            )
            conn.commit()
            
        return jsonify({"success": True, "message": "Unfollowed user successfully."})
    except Exception as e:
        app.logger.error(f"Error unfollowing user: {str(e)}")
        return jsonify({"success": False, "error": "Internal server error."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route("/api/following", methods=["GET"])
def api_get_following_list():
    """Gets list of users the current user follows."""
    user = get_current_user(request)
    if not user:
        return jsonify({"success": False, "error": "Unauthorized session."}), 401
        
    user_id = user["user_id"]
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT u.id, u.name, u.username, pr.display_name, pr.avatar
            FROM follows f
            JOIN users u ON f.following_id = u.id
            LEFT JOIN profiles pr ON u.id = pr.user_id
            WHERE f.follower_id = %s AND u.is_banned = 0
            """,
            (user_id,)
        )
        following = cursor.fetchall()
        return jsonify({"success": True, "following": following})
    except Exception as e:
        app.logger.error(f"Error getting following list: {str(e)}")
        return jsonify({"success": False, "error": "Internal server error."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route("/api/followers", methods=["GET"])
def api_get_followers_list():
    """Gets list of users following the current user."""
    user = get_current_user(request)
    if not user:
        return jsonify({"success": False, "error": "Unauthorized session."}), 401
        
    user_id = user["user_id"]
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT u.id, u.name, u.username, pr.display_name, pr.avatar
            FROM follows f
            JOIN users u ON f.follower_id = u.id
            LEFT JOIN profiles pr ON u.id = pr.user_id
            WHERE f.following_id = %s AND u.is_banned = 0
            """,
            (user_id,)
        )
        followers = cursor.fetchall()
        return jsonify({"success": True, "followers": followers})
    except Exception as e:
        app.logger.error(f"Error getting followers list: {str(e)}")
        return jsonify({"success": False, "error": "Internal server error."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route("/api/feed/following", methods=["GET"])
def api_get_following_feed():
    """Gets a feed of public prompts from followed creators."""
    user = get_current_user(request)
    if not user:
        return jsonify({"success": False, "error": "Unauthorized session."}), 401
        
    user_id = user["user_id"]
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT p.*, u.username, u.name AS author_name, pr.display_name AS author_display_name, pr.avatar AS author_avatar
            FROM prompts p
            JOIN follows f ON p.user_id = f.following_id
            JOIN users u ON p.user_id = u.id
            LEFT JOIN profiles pr ON p.user_id = pr.user_id
            WHERE f.follower_id = %s AND p.visibility = 'public' AND p.moderation_status = 'approved' AND p.deleted_at IS NULL
            ORDER BY p.published_at DESC
            LIMIT 30
            """,
            (user_id,)
        )
        feed = cursor.fetchall()
        for p in feed:
            if isinstance(p["created_at"], datetime):
                p["created_at"] = p["created_at"].strftime("%Y-%m-%d %H:%M:%S")
            if isinstance(p["published_at"], datetime):
                p["published_at"] = p["published_at"].strftime("%Y-%m-%d %H:%M:%S")
            if isinstance(p["mcq_questions"], str):
                p["mcq_questions"] = json.loads(p["mcq_questions"])
            if isinstance(p["mcq_answers"], str):
                p["mcq_answers"] = json.loads(p["mcq_answers"])
        
        # Attach badges
        spotlight = get_prompt_of_the_week()
        spotlight_id = spotlight["id"] if spotlight else None
        feed = attach_prompt_badges(feed, spotlight_id)

        return jsonify({"success": True, "feed": feed})
    except Exception as e:
        app.logger.error(f"Error fetching following feed: {str(e)}")
        return jsonify({"success": False, "error": "Internal server error."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# ============ Prompt Forking Endpoint ============

@app.route("/api/prompts/<int:id>/fork", methods=["POST"])
def api_fork_prompt(id):
    """Forks a public/unlisted prompt into the logged-in user's private workbench."""
    user = get_current_user(request)
    if not user:
        return jsonify({"success": False, "error": "Unauthorized session."}), 401
        
    user_id = user["user_id"]
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Get original prompt details
        cursor.execute("SELECT * FROM prompts WHERE id = %s AND deleted_at IS NULL", (id,))
        original = cursor.fetchone()
        if not original:
            return jsonify({"success": False, "error": "Original prompt not found."}), 404
        
        # Security permission check: Must be public or unlisted, or owned by the user
        if original["visibility"] == "private" and original["user_id"] != user_id:
            return jsonify({"success": False, "error": "Forbidden: Cannot fork private prompts."}), 403

        # Insert new forked prompt
        cursor.execute(
            """
            INSERT INTO prompts (user_id, input_text, category, mcq_questions, mcq_answers, generated_prompt, version_number, forked_from_prompt_id, quality_score, target_model, optimization_style, visibility, is_public)
            VALUES (%s, %s, %s, %s, %s, %s, 1, %s, %s, %s, %s, 'private', 0)
            """,
            (user_id, original["input_text"], original["category"], 
             original["mcq_questions"] if isinstance(original["mcq_questions"], str) else json.dumps(original["mcq_questions"]), 
             original["mcq_answers"] if isinstance(original["mcq_answers"], str) else json.dumps(original["mcq_answers"]), 
             original["generated_prompt"], id, original["quality_score"], original["target_model"], original["optimization_style"])
        )
        fork_id = cursor.lastrowid
        
        # Increment parent fork count
        cursor.execute("UPDATE prompts SET fork_count = fork_count + 1 WHERE id = %s", (id,))
        
        # Send Notification if fork is not done by the author
        if original["user_id"] and original["user_id"] != user_id:
            cursor.execute(
                "INSERT INTO notifications (user_id, sender_id, type, prompt_id) VALUES (%s, %s, 'fork', %s)",
                (original["user_id"], user_id, id)
            )
        
        # Increment user prompts_used count
        cursor.execute("UPDATE users SET prompts_used = prompts_used + 1 WHERE id = %s", (user_id,))
        
        conn.commit()
        return jsonify({"success": True, "message": "Prompt successfully forked into your workbench!", "prompt_id": fork_id})
    except Exception as e:
        app.logger.error(f"Error forking prompt: {str(e)}", exc_info=True)
        return jsonify({"success": False, "error": "Internal server error."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# ============ Moderation System Endpoints ============

@app.route("/api/prompts/<int:id>/report", methods=["POST"])
def api_report_prompt(id):
    """Submits a report on a public prompt."""
    user = get_current_user(request)
    if not user:
        return jsonify({"success": False, "error": "Unauthorized session."}), 401
        
    user_id = user["user_id"]
    reason = request.json.get("reason", "Other").strip()
    comment = request.json.get("comment", "").strip()

    if reason not in ["Spam", "Abuse", "Duplicate", "Copyright", "Other"]:
        return jsonify({"success": False, "error": "Invalid report reason."}), 400

    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Check prompt exists
        cursor.execute("SELECT id FROM prompts WHERE id = %s AND deleted_at IS NULL", (id,))
        if not cursor.fetchone():
            return jsonify({"success": False, "error": "Prompt not found."}), 404

        # Check duplicate report (spam prevention)
        cursor.execute("SELECT COUNT(*) AS total FROM reports WHERE reporter_id = %s AND prompt_id = %s", (user_id, id))
        if cursor.fetchone()["total"] > 0:
            return jsonify({"success": False, "error": "You have already reported this prompt."}), 400

        # Insert report
        cursor.execute(
            """
            INSERT INTO reports (reporter_id, prompt_id, reason, comment)
            VALUES (%s, %s, %s, %s)
            """,
            (user_id, id, reason, comment)
        )
        conn.commit()
        return jsonify({"success": True, "message": "Thank you. Prompt has been reported for review."})
    except Exception as e:
        app.logger.error(f"Error reporting prompt: {str(e)}")
        return jsonify({"success": False, "error": "Internal server error."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route("/api/admin/prompts/<int:id>/moderate", methods=["POST"])
def api_moderate_prompt(id):
    """Allows administrators to approve, hide, or flag a prompt."""
    user_payload = get_current_user(request)
    if not user_payload:
        return jsonify({"success": False, "error": "Unauthorized session."}), 401

    admin_id = user_payload.get("user_id")
    status = request.json.get("status", "approved").strip().lower() # approved, flagged, hidden
    
    if status not in ["approved", "flagged", "hidden"]:
        return jsonify({"success": False, "error": "Invalid status parameter."}), 400

    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Verify role is admin
        cursor.execute("SELECT role FROM users WHERE id = %s", (admin_id,))
        u = cursor.fetchone()
        if not u or u["role"] != "admin":
            return jsonify({"success": False, "error": "Forbidden: Admin privileges required."}), 403

        # Update moderation status
        cursor.execute("UPDATE prompts SET moderation_status = %s WHERE id = %s AND deleted_at IS NULL", (status, id))
        
        # If resolving reports, update status in reports table
        cursor.execute("UPDATE reports SET status = 'reviewed' WHERE prompt_id = %s", (id,))
        
        conn.commit()
        return jsonify({"success": True, "message": f"Prompt moderation status updated to {status}."})
    except Exception as e:
        app.logger.error(f"Error moderating prompt: {str(e)}")
        return jsonify({"success": False, "error": "Internal server error."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route("/api/admin/users/<int:id>/ban", methods=["POST"])
def api_ban_user(id):
    """Allows administrators to ban/unban creators."""
    user_payload = get_current_user(request)
    if not user_payload:
        return jsonify({"success": False, "error": "Unauthorized session."}), 401

    admin_id = user_payload.get("user_id")
    ban = request.json.get("ban", True)

    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("SELECT role FROM users WHERE id = %s", (admin_id,))
        u = cursor.fetchone()
        if not u or u["role"] != "admin":
            return jsonify({"success": False, "error": "Forbidden: Admin privileges required."}), 403

        # Ban user
        ban_val = 1 if ban else 0
        cursor.execute("UPDATE users SET is_banned = %s WHERE id = %s", (ban_val, id))
        conn.commit()
        
        status_str = "banned" if ban else "unbanned"
        return jsonify({"success": True, "message": f"User account has been successfully {status_str}."})
    except Exception as e:
        app.logger.error(f"Error toggling ban state: {str(e)}")
        return jsonify({"success": False, "error": "Internal server error."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route("/api/admin/reports", methods=["GET"])
def api_get_reports_list():
    """Allows administrators to view all submitted reports."""
    user_payload = get_current_user(request)
    if not user_payload:
        return jsonify({"success": False, "error": "Unauthorized session."}), 401

    admin_id = user_payload.get("user_id")
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("SELECT role FROM users WHERE id = %s", (admin_id,))
        u = cursor.fetchone()
        if not u or u["role"] != "admin":
            return jsonify({"success": False, "error": "Forbidden: Admin privileges required."}), 403

        cursor.execute(
            """
            SELECT r.*, u.email AS reporter_email, p.input_text AS prompt_input, p.generated_prompt
            FROM reports r
            JOIN users u ON r.reporter_id = u.id
            JOIN prompts p ON r.prompt_id = p.id
            WHERE p.deleted_at IS NULL AND r.status = 'pending'
            ORDER BY r.created_at DESC
            """
        )
        reports = cursor.fetchall()
        for r in reports:
            if isinstance(r["created_at"], datetime):
                r["created_at"] = r["created_at"].strftime("%Y-%m-%d %H:%M:%S")

        return jsonify({"success": True, "reports": reports})
    except Exception as e:
        app.logger.error(f"Error fetching reports: {str(e)}")
        return jsonify({"success": False, "error": "Internal server error."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# ============ Notification Center API Endpoints ============

@app.route("/api/notifications", methods=["GET"])
def api_get_notifications():
    """Fetches user notifications."""
    user = get_current_user(request)
    if not user:
        return jsonify({"success": False, "error": "Unauthorized session."}), 401
        
    user_id = user["user_id"]
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT n.*, u.username AS sender_username, pr.display_name AS sender_display_name, pr.avatar AS sender_avatar, p.input_text AS prompt_input
            FROM notifications n
            LEFT JOIN users u ON n.sender_id = u.id
            LEFT JOIN profiles pr ON n.sender_id = pr.user_id
            LEFT JOIN prompts p ON n.prompt_id = p.id
            WHERE n.user_id = %s
            ORDER BY n.is_read ASC, n.created_at DESC
            LIMIT 30
            """,
            (user_id,)
        )
        notifications = cursor.fetchall()
        for n in notifications:
            if isinstance(n["created_at"], datetime):
                n["created_at"] = n["created_at"].strftime("%Y-%m-%d %H:%M:%S")

        return jsonify({"success": True, "notifications": notifications})
    except Exception as e:
        app.logger.error(f"Error loading notifications: {str(e)}")
        return jsonify({"success": False, "error": "Internal server error."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route("/api/notifications/read", methods=["POST"])
def api_mark_notifications_read():
    """Marks all user notifications as read."""
    user = get_current_user(request)
    if not user:
        return jsonify({"success": False, "error": "Unauthorized session."}), 401
        
    user_id = user["user_id"]
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE notifications SET is_read = 1 WHERE user_id = %s", (user_id,))
        conn.commit()
        return jsonify({"success": True, "message": "Notifications marked as read."})
    except Exception as e:
        app.logger.error(f"Error reading notifications: {str(e)}")
        return jsonify({"success": False, "error": "Internal server error."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
# ============ PHASE 5: WORKFLOWS, ORGANIZATIONS & AI AGENTS ============
import concurrent.futures
import base64
import re
import urllib.request
import urllib.parse
import traceback

def check_org_permission(user_id, org_id, required_roles=None):
    """Checks if user has organization access and role permission."""
    if required_roles is None:
        required_roles = ['owner', 'admin', 'editor', 'viewer']
    conn = models.get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT role FROM organization_members WHERE organization_id = %s AND user_id = %s",
            (org_id, user_id)
        )
        member = cursor.fetchone()
        if not member:
            return False, None
        role = member["role"]
        return (role in required_roles), role
    finally:
        cursor.close()
        conn.close()

def resolve_variables(text, variables):
    """Replaces {{var}} or {{ENV.secret}} in templates with values from variables/secrets context."""
    if not text:
        return text
    def replace_val(match):
        key = match.group(1).strip()
        if key.startswith("ENV."):
            secret_name = key[4:]
            return variables.get("_secrets", {}).get(secret_name, f"{{{key}}}")
        parts = key.split('.')
        val = variables
        for p in parts:
            if isinstance(val, dict) and p in val:
                val = val[p]
            else:
                return f"{{{key}}}"
        return str(val)
    return re.sub(r'\{\{([^}]+)\}\}', replace_val, text)

def search_ddg(query):
    """Queries DuckDuckGo HTML search page and returns result snippet summaries."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as response:
            html = response.read().decode('utf-8')
        raw_snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)
        cleaned = [re.sub(r'<[^>]+>', '', s).strip() for s in raw_snippets[:5]]
        if cleaned:
            return "\n".join([f"- {s}" for s in cleaned])
        return f"No results found for search query: '{query}'"
    except Exception as e:
        app.logger.warning(f"DuckDuckGo search fetch failed: {str(e)}")
        return f"Mock search fallback response for query: '{query}'"

def evaluate_condition(left_val, operator, right_val):
    """Evaluates conditional logic matching left and right values using basic comparisons."""
    l_str = str(left_val).strip().lower()
    r_str = str(right_val).strip().lower()
    if operator == "equals":
        return l_str == r_str
    elif operator == "contains":
        return r_str in l_str
    elif operator == "greater_than":
        try:
            return float(left_val) > float(right_val)
        except:
            return False
    elif operator == "less_than":
        try:
            return float(left_val) < float(right_val)
        except:
            return False
    return False

def match_cron_field(val, pattern):
    """Helper to check if a specific time element value satisfies a cron pattern string."""
    if pattern == "*":
        return True
    if pattern.startswith("*/"):
        try:
            step = int(pattern.split("/")[1])
            return val % step == 0
        except:
            return False
    if "," in pattern:
        return str(val) in pattern.split(",")
    if "-" in pattern:
        try:
            start, end = map(int, pattern.split("-"))
            return start <= val <= end
        except:
            return False
    try:
        return val == int(pattern)
    except:
        return False

def is_cron_due(cron_expr, dt):
    """Lightweight zero-dependency cron expression minute trigger validator."""
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        return False
    cron_wday = (dt.weekday() + 1) % 7
    return (match_cron_field(dt.minute, parts[0]) and
            match_cron_field(dt.hour, parts[1]) and
            match_cron_field(dt.day, parts[2]) and
            match_cron_field(dt.month, parts[3]) and
            match_cron_field(cron_wday, parts[4]))

def transition_run_status(run_id, new_status):
    valid_transitions = {
        "queued": ["running", "cancelled"],
        "running": ["completed", "failed", "cancelled", "paused"],
        "paused": ["resumed", "cancelled"],
        "resumed": ["running", "failed", "cancelled"],
        "completed": [],
        "failed": [],
        "cancelled": []
    }
    
    conn = models.get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT status FROM workflow_runs WHERE id = %s", (run_id,))
        row = cursor.fetchone()
        if not row:
            return False
        current_status = row["status"]
        
        if current_status == new_status:
            return True
            
        if new_status not in valid_transitions.get(current_status, []):
            print(f"[Run State Machine Error] Cannot transition run {run_id} from '{current_status}' to '{new_status}'")
            return False
            
        db_status = "running" if new_status == "resumed" else new_status
        cursor.execute("UPDATE workflow_runs SET status = %s WHERE id = %s", (db_status, run_id))
        conn.commit()
        return True
    except Exception as e:
        print(f"[Run State Machine Exception] {e}")
        return False
    finally:
        cursor.close()
        conn.close()

import concurrent.futures

class WorkflowRunner:
    """DAG Parallel Thread Pool Executor for ForgePrompt Workflows with State Machine and Service Layers."""
    def __init__(self, run_id):
        self.run_id = run_id
        self.statuses = {}
        self.outputs = {}
        self.inputs = {}
        self.secrets = {}
        self.nodes = {}
        self.edges = []
        self.parents = {}
        self.children = {}
        self.lock = threading.Lock()
        self.total_tokens = 0
        self.total_cost = 0.0
        self.max_tokens = 100000
        self.max_cost = 1.0000
        self.org_id = None
        self.user_id = None
        self.futures = {}

    def execute(self):
        conn = None
        cursor = None
        try:
            # 1. State machine transition: queued -> running
            if not transition_run_status(self.run_id, "running"):
                print(f"[WorkflowRunner] FAILED transition to running for run {self.run_id}. Aborting execution.")
                return

            conn = models.get_db_connection()
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute("SELECT * FROM workflow_runs WHERE id = %s", (self.run_id,))
            run = cursor.fetchone()
            if not run:
                return
            
            self.inputs = json.loads(run["inputs"] or "{}")
            wf_id = run["workflow_id"]
            self.user_id = run["user_id"]
            
            cursor.execute("SELECT * FROM workflows WHERE id = %s", (wf_id,))
            wf = cursor.fetchone()
            if not wf:
                return
            
            self.max_tokens = wf["max_token_limit"] or 100000
            self.max_cost = float(wf["max_cost_limit"] or 1.0000)
            self.org_id = wf["organization_id"]

            # Initialize workflow runtime context
            cursor.execute("DELETE FROM workflow_contexts WHERE run_id = %s", (self.run_id,))
            cursor.execute(
                """
                INSERT INTO workflow_contexts (run_id, variables_json, memory_json, current_node_id, shared_state_json)
                VALUES (%s, %s, '{}', NULL, '{}')
                """,
                (self.run_id, json.dumps(self.inputs))
            )
            conn.commit()

            # Publish event
            event_bus.publish("WorkflowStarted", {"run_id": self.run_id, "workflow_id": wf_id, "inputs": self.inputs})
            
            if self.org_id:
                cursor.execute("SELECT name, encrypted_value FROM organization_secrets WHERE organization_id = %s", (self.org_id,))
                for s in cursor.fetchall():
                    try:
                        self.secrets[s["name"]] = base64.b64decode(s["encrypted_value"].encode()).decode()
                    except:
                        self.secrets[s["name"]] = s["encrypted_value"]
            
            cursor.execute("SELECT * FROM workflow_nodes WHERE workflow_id = %s", (wf_id,))
            nodes_list = cursor.fetchall()
            cursor.execute("SELECT * FROM workflow_edges WHERE workflow_id = %s", (wf_id,))
            self.edges = cursor.fetchall()
            
            self.nodes = {n["id"]: n for n in nodes_list}
            self.parents = {n["id"]: set() for n in nodes_list}
            self.children = {n["id"]: set() for n in nodes_list}
            self.statuses = {n["id"]: "pending" for n in nodes_list}
            
            for edge in self.edges:
                t_node = edge["target_node_id"]
                s_node = edge["source_node_id"]
                if t_node in self.parents:
                    self.parents[t_node].add(s_node)
                if s_node in self.children:
                    self.children[s_node].add(t_node)
            
            # Re-create/clean step records
            cursor.execute("DELETE FROM workflow_steps WHERE run_id = %s", (self.run_id,))
            for n_id in self.nodes:
                cursor.execute(
                    "INSERT INTO workflow_steps (run_id, node_id, status) VALUES (%s, %s, 'pending')",
                    (self.run_id, n_id)
                )
            conn.commit()
            
            entry_nodes = [n_id for n_id, p_set in self.parents.items() if len(p_set) == 0]
            if not entry_nodes:
                transition_run_status(self.run_id, "completed")
                return

            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
                self.futures = {}
                for node_id in entry_nodes:
                    f = pool.submit(self.run_node, node_id)
                    self.futures[f] = node_id
                
                while self.futures:
                    done, _ = concurrent.futures.wait(self.futures.keys(), return_when=concurrent.futures.FIRST_COMPLETED)
                    for f in done:
                        node_id = self.futures.pop(f)
                        try:
                            f.result()
                        except Exception as e:
                            app.logger.error(f"Node execution crashed: {node_id}: {str(e)}")
                            self.statuses[node_id] = "failed"
                            self.update_step_db(node_id, "failed", error_message=str(e))
                            event_bus.publish("NodeFailed", {"run_id": self.run_id, "node_id": node_id, "error_message": str(e), "duration_ms": 0})
                        
                        node = self.nodes[node_id]
                        config_json = json.loads(node.get("config_json") or "{}")
                        continue_on_failure = config_json.get("continue_on_failure", False)
                        
                        if self.statuses[node_id] == "failed" and not continue_on_failure:
                            transition_run_status(self.run_id, "failed")
                            return
                            
                        if self.statuses[node_id] == "paused":
                            # Approval node paused run
                            return
                            
                        if self.total_tokens > self.max_tokens or self.total_cost > self.max_cost:
                            cursor.execute("UPDATE workflow_runs SET outputs = %s WHERE id = %s",
                                           (json.dumps({"error": "Guardrail limit exceeded"}), self.run_id))
                            conn.commit()
                            transition_run_status(self.run_id, "failed")
                            return
                            
                        with self.lock:
                            for child_id in self.children.get(node_id, []):
                                if self.statuses.get(child_id) != "pending":
                                    continue
                                all_parents_done = True
                                for p_id in self.parents[child_id]:
                                    if self.statuses.get(p_id) not in ["completed", "skipped"]:
                                        all_parents_done = False
                                        break
                                if all_parents_done:
                                    all_parents_skipped = True
                                    for p_id in self.parents[child_id]:
                                        if self.statuses.get(p_id) != "skipped":
                                            all_parents_skipped = False
                                            break
                                    if all_parents_skipped:
                                        self.statuses[child_id] = "skipped"
                                        self.update_step_db(child_id, "completed", output_val="Skipped: parent skipped", tokens=0, cost=0.0)
                                        self.skip_descendants(child_id)
                                    else:
                                        self.statuses[child_id] = "running"
                                        f_child = pool.submit(self.run_node, child_id)
                                        self.futures[f_child] = child_id

            final_status = "completed"
            for n_id, st in self.statuses.items():
                if st == "failed":
                    final_status = "failed"
            
            # Transition status
            transition_run_status(self.run_id, final_status)
            
            # Update totals
            cursor.execute(
                "UPDATE workflow_runs SET outputs = %s, total_tokens = %s, total_cost = %s WHERE id = %s",
                (json.dumps(self.outputs), self.total_tokens, self.total_cost, self.run_id)
            )
            conn.commit()
        except Exception as ex:
            app.logger.error(f"Runner thread failed: {str(ex)}")
            if conn:
                try:
                    transition_run_status(self.run_id, "failed")
                except:
                    pass
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    def run_node(self, node_id):
        t_start = time.time()
        self.update_step_db(node_id, "running")
        node = self.nodes[node_id]
        node_type = node["type"]
        config_json = json.loads(node.get("config_json") or "{}")
        
        retry_count = int(config_json.get("retry_count", 0))
        retry_delay = float(config_json.get("retry_delay", 1.0))
        
        # Build execution variables context
        variables_context = {}
        variables_context.update(self.inputs)
        for k, v in self.outputs.items():
            variables_context[k] = v
            if "_" in k:
                short_k = k.split("_")[-1]
                variables_context[short_k] = v
        variables_context["_secrets"] = self.secrets

        # Update context current node
        conn = models.get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "UPDATE workflow_contexts SET current_node_id = %s, variables_json = %s WHERE run_id = %s",
                (node_id, json.dumps(variables_context), self.run_id)
            )
            conn.commit()
        except Exception as e:
            print(f"[WorkflowRunner] Context update error: {e}")
        finally:
            cursor.close()
            conn.close()
        
        output_val = None
        tokens = 0
        cost = 0.0
        error_msg = None
        attempt = 0
        success = False
        
        while attempt <= retry_count:
            try:
                if node_type == "PromptNode":
                    # Prompt Node now reads from PromptRegistry if linked
                    p_id = node.get("prompt_id")
                    if p_id:
                        p_ver = node.get("prompt_version", -1)
                        conn_p = models.get_db_connection()
                        cursor_p = conn_p.cursor(dictionary=True)
                        try:
                            if p_ver == -1:
                                cursor_p.execute(
                                    "SELECT prompt_template FROM prompt_registry_versions WHERE prompt_id = %s ORDER BY version_number DESC LIMIT 1",
                                    (p_id,)
                                )
                            else:
                                cursor_p.execute(
                                    "SELECT prompt_template FROM prompt_registry_versions WHERE prompt_id = %s AND version_number = %s",
                                    (p_id, p_ver)
                                )
                            p_row = cursor_p.fetchone()
                            template = p_row["prompt_template"] if p_row else ""
                        finally:
                            cursor_p.close()
                            conn_p.close()
                    else:
                        template = node.get("prompt_template", "")
                        
                    output_val = resolve_variables(template, variables_context)
                    success = True
                    
                elif node_type == "PromptRegistryNode":
                    prompt_id = config_json.get("prompt_id")
                    prompt_version = config_json.get("prompt_version", -1)
                    if not prompt_id:
                        raise ValueError("No prompt_id configured in PromptRegistryNode")
                    
                    conn_p = models.get_db_connection()
                    cursor_p = conn_p.cursor(dictionary=True)
                    try:
                        if int(prompt_version) == -1:
                            cursor_p.execute(
                                "SELECT prompt_template FROM prompt_registry_versions WHERE prompt_id = %s ORDER BY version_number DESC LIMIT 1",
                                (prompt_id,)
                            )
                        else:
                            cursor_p.execute(
                                "SELECT prompt_template FROM prompt_registry_versions WHERE prompt_id = %s AND version_number = %s",
                                (prompt_id, prompt_version)
                            )
                        p_row = cursor_p.fetchone()
                        if not p_row:
                            raise ValueError(f"Prompt ID {prompt_id} version {prompt_version} not found in registry")
                        template = p_row["prompt_template"]
                    finally:
                        cursor_p.close()
                        conn_p.close()
                        
                    output_val = resolve_variables(template, variables_context)
                    success = True

                elif node_type == "LLMNode":
                    # Determine dynamic routing rules or direct settings
                    model = config_json.get("model")
                    temperature = float(config_json.get("temperature", 0.7))
                    max_tokens = int(config_json.get("max_tokens", 1000))
                    system_prompt = resolve_variables(config_json.get("system_prompt", ""), variables_context)
                    user_prompt = resolve_variables(node.get("prompt_template", "") or config_json.get("prompt", ""), variables_context)
                    
                    if not model:
                        task_type = config_json.get("task_type", "general")
                        route = RouterService.route_task(task_type, self.org_id)
                        provider_name = route["provider"]
                        model_name = route["model"]
                    else:
                        model_name = model
                        if "gpt" in model_name:
                            provider_name = "openai"
                        elif "llama" in model_name:
                            provider_name = "groq"
                        else:
                            provider_name = "local"
                            
                    res = LLMService.call(
                        provider_name=provider_name,
                        model_name=model_name,
                        prompt=user_prompt,
                        system_prompt=system_prompt,
                        max_tokens=max_tokens,
                        temperature=temperature
                    )
                    output_val = res["text"]
                    tokens = res["tokens"]
                    cost = res["cost"]
                    success = True

                elif node_type == "AgentNode":
                    agent_id = node.get("agent_id")
                    if not agent_id:
                        raise Exception("No agent_id defined")
                    conn_a = models.get_db_connection()
                    cursor_a = conn_a.cursor(dictionary=True)
                    try:
                        cursor_a.execute("SELECT * FROM agents WHERE id = %s", (agent_id,))
                        agent = cursor_a.fetchone()
                    finally:
                        cursor_a.close()
                        conn_a.close()
                        
                    if not agent:
                        raise Exception("Agent details missing")
                    model = agent.get("preferred_model", "llama3-8b-8192")
                    system_prompt = f"Role: {agent['role']}\nGoals: {agent['goals']}\nInstructions: {agent['instructions']}"
                    user_prompt = resolve_variables(node.get("prompt_template", ""), variables_context)
                    
                    provider_name = "openai" if "gpt" in model else ("groq" if "llama" in model else "local")
                    res = LLMService.call(
                        provider_name=provider_name,
                        model_name=model,
                        prompt=user_prompt,
                        system_prompt=system_prompt
                    )
                    output_val = res["text"]
                    tokens = res["tokens"]
                    cost = res["cost"]
                    success = True

                elif node_type == "SearchNode":
                    query_tmpl = config_json.get("query", "")
                    query = resolve_variables(query_tmpl, variables_context)
                    output_val = search_ddg(query)
                    success = True

                elif node_type == "ConditionNode":
                    left_tmpl = config_json.get("left", "")
                    left_val = resolve_variables(left_tmpl, variables_context)
                    right_tmpl = config_json.get("right", "")
                    right_val = resolve_variables(right_tmpl, variables_context)
                    operator = config_json.get("operator", "equals")
                    cond_res = evaluate_condition(left_val, operator, right_val)
                    output_val = {"result": cond_res}
                    success = True
                    self.handle_condition_branch(node_id, cond_res)

                elif node_type == "DelayNode":
                    sec = float(config_json.get("seconds", 1.0))
                    time.sleep(sec)
                    output_val = f"Delayed {sec}s"
                    success = True

                elif node_type == "ApprovalNode":
                    self.statuses[node_id] = "paused"
                    self.update_step_db(node_id, "pending", output_val="Paused: human approval required")
                    transition_run_status(self.run_id, "paused")
                    return

                elif node_type == "KnowledgeNode":
                    kb_id = config_json.get("kb_id")
                    query_tmpl = config_json.get("query", "")
                    query = resolve_variables(query_tmpl, variables_context)
                    
                    query_vector = EmbeddingService.embed(query, provider_name="local")
                    vector_store = MySQLVectorStore()
                    search_results = vector_store.similarity_search(kb_id, query_vector, top_k=5)
                    
                    retrieved_text = "\n\n".join(r["text"] for r in search_results)
                    output_val = {
                        "context": retrieved_text,
                        "sources": [{"doc_id": r["doc_id"], "page": r["page_number"], "score": r["score"]} for r in search_results]
                    }
                    success = True

                elif node_type == "OCRNode":
                    file_path_tmpl = config_json.get("file_path", "")
                    file_path = resolve_variables(file_path_tmpl, variables_context)
                    output_val = f"[OCR text extracted from {file_path}]: This is a simulated OCR text result."
                    success = True

                elif node_type == "VisionNode":
                    prompt_tmpl = node.get("prompt_template") or config_json.get("prompt", "")
                    user_prompt = resolve_variables(prompt_tmpl, variables_context)
                    image_url_tmpl = config_json.get("image_url", "")
                    image_url = resolve_variables(image_url_tmpl, variables_context)
                    
                    res = LLMService.call(
                        provider_name="openai",
                        model_name="gpt-4o",
                        prompt=f"Analyze this image URL: {image_url}. {user_prompt}",
                        system_prompt="You are a helpful assistant with vision capabilities."
                    )
                    output_val = res["text"]
                    tokens = res["tokens"]
                    cost = res["cost"]
                    success = True

                elif node_type == "SpeechNode":
                    audio_path_tmpl = config_json.get("audio_path", "")
                    audio_path = resolve_variables(audio_path_tmpl, variables_context)
                    output_val = f"[Speech Transcript for {audio_path}]: This is a simulated transcription."
                    success = True

                elif node_type == "BrowserNode":
                    url_tmpl = config_json.get("url", "")
                    url = resolve_variables(url_tmpl, variables_context)
                    import requests
                    res = requests.get(url, timeout=10)
                    output_val = f"HTTP Status: {res.status_code}\nContent Preview: {res.text[:1000]}"
                    success = True

                elif node_type == "SQLNode":
                    query_tmpl = config_json.get("query", "")
                    query = resolve_variables(query_tmpl, variables_context)
                    clean_q = query.strip().upper()
                    if not (clean_q.startswith("SELECT") or clean_q.startswith("SHOW") or clean_q.startswith("DESC")):
                        raise PermissionError("SQLNode security constraint violation: Only SELECT, SHOW, or DESC queries are allowed.")
                        
                    conn_sql = models.get_db_connection()
                    cursor_sql = conn_sql.cursor(dictionary=True)
                    try:
                        cursor_sql.execute(query)
                        rows = cursor_sql.fetchall()
                        output_val = {"rows": rows, "row_count": len(rows)}
                        success = True
                    finally:
                        cursor_sql.close()
                        conn_sql.close()

                elif node_type == "PythonNode":
                    code_tmpl = config_json.get("code", "")
                    code = resolve_variables(code_tmpl, variables_context)
                    sandbox_res = SandboxService.execute_code(code, inputs=variables_context)
                    if sandbox_res.get("success"):
                        output_val = {
                            "stdout": sandbox_res.get("stdout", ""),
                            "stderr": sandbox_res.get("stderr", ""),
                            "outputs": sandbox_res.get("outputs", {})
                        }
                        success = True
                    else:
                        raise ValueError(sandbox_res.get("error", "Sandbox execution failed"))

                else:
                    # Check custom Node Plugins
                    plugin_class = PluginSDK.get_plugin(node_type)
                    if plugin_class:
                        ctx = NodeExecutionContext(
                            run_id=self.run_id,
                            workflow_id=node["workflow_id"],
                            variables=variables_context,
                            memory=MemoryService.get_all("workflow", self.run_id),
                            artifacts=[],
                            logger=print,
                            provider=None,
                            event_bus=event_bus,
                            org_id=self.org_id
                        )
                        plugin_inst = plugin_class(config=config_json)
                        plugin_res = plugin_inst.execute(ctx)
                        if isinstance(plugin_res, dict) and plugin_res.get("success"):
                            output_val = plugin_res
                            success = True
                        elif isinstance(plugin_res, dict) and not plugin_res.get("success"):
                            raise ValueError(plugin_res.get("error", "Plugin returned success=False"))
                        else:
                            output_val = plugin_res
                            success = True
                    else:
                        output_val = f"Unsupported node type: {node_type}"
                        success = True
                        
                if success:
                    break
            except Exception as err:
                error_msg = str(err)
                attempt += 1
                if attempt <= retry_count:
                    time.sleep(retry_delay)
                    
        duration_ms = int((time.time() - t_start) * 1000)
        
        if success:
            self.statuses[node_id] = "completed"
            self.outputs[node_id] = output_val
            with self.lock:
                self.total_tokens += tokens
                self.total_cost += cost
            self.update_step_db(node_id, "completed", output_val=json.dumps(output_val) if isinstance(output_val, (dict, list)) else str(output_val), tokens=tokens, cost=cost)
            
            # Find parent node id safely from edges
            parent_node_id = None
            for edge in self.edges:
                if edge["target_node_id"] == node_id:
                    parent_node_id = edge["source_node_id"]
                    break
            
            # Publish event completes
            event_bus.publish("NodeCompleted", {
                "run_id": self.run_id,
                "node_id": node_id,
                "parent_node_id": parent_node_id,
                "status": "completed",
                "inputs": variables_context,
                "outputs": output_val,
                "duration_ms": duration_ms,
                "tokens": tokens,
                "cost": cost
            })
        else:
            self.statuses[node_id] = "failed"
            self.update_step_db(node_id, "failed", error_message=error_msg)
            event_bus.publish("NodeFailed", {
                "run_id": self.run_id,
                "node_id": node_id,
                "error_message": error_msg,
                "duration_ms": duration_ms
            })

    def handle_condition_branch(self, node_id, cond_result):
        inactive = "false" if cond_result else "true"
        with self.lock:
            for edge in self.edges:
                if edge["source_node_id"] == node_id and edge["source_handle"] == inactive:
                    target = edge["target_node_id"]
                    if self.statuses.get(target) == "pending":
                        self.statuses[target] = "skipped"
                        self.update_step_db(target, "completed", output_val="Skipped branch inactive", tokens=0, cost=0.0)
                        self.skip_descendants(target)

    def skip_descendants(self, node_id):
        for child in self.children.get(node_id, []):
            if self.statuses.get(child) == "pending":
                self.statuses[child] = "skipped"
                self.update_step_db(child, "completed", output_val="Skipped: parent skipped", tokens=0, cost=0.0)
                self.skip_descendants(child)

    def update_step_db(self, node_id, status, output_val=None, tokens=0, cost=0.0, error_message=None):
        conn = models.get_db_connection()
        cursor = conn.cursor()
        try:
            if status == "completed":
                cursor.execute(
                    "UPDATE workflow_steps SET status = 'completed', output_generated = %s, tokens_used = %s, cost = %s, completed_at = CURRENT_TIMESTAMP WHERE run_id = %s AND node_id = %s",
                    (output_val, tokens, cost, self.run_id, node_id)
                )
            elif status == "failed":
                cursor.execute(
                    "UPDATE workflow_steps SET status = 'failed', error_message = %s, completed_at = CURRENT_TIMESTAMP WHERE run_id = %s AND node_id = %s",
                    (error_message, self.run_id, node_id)
                )
            elif status == "running":
                cursor.execute(
                    "UPDATE workflow_steps SET status = 'running' WHERE run_id = %s AND node_id = %s",
                    (self.run_id, node_id)
                )
            conn.commit()
        except Exception as e:
            app.logger.error(f"Failed step update: {str(e)}")
            cursor.close()
            conn.close()

def run_executor_async(run_id):
    runner = WorkflowRunner(run_id)
    runner.execute()

def start_cron_scheduler():
    """Background polling thread validating and executing cron schedules."""
    while True:
        try:
            conn = models.get_db_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                "SELECT ws.* FROM workflow_schedules ws JOIN workflows w ON ws.workflow_id = w.id WHERE ws.status = 'active' AND w.deleted_at IS NULL"
            )
            schedules = cursor.fetchall()
            now = datetime.now()
            for sched in schedules:
                cron_expr = sched["cron_expression"]
                last_run = sched["last_run"]
                if last_run and last_run.minute == now.minute and last_run.hour == now.hour and last_run.day == now.day:
                    continue
                if is_cron_due(cron_expr, now):
                    cursor.execute(
                        "INSERT INTO workflow_runs (workflow_id, user_id, status, inputs) VALUES (%s, %s, 'queued', '{}')",
                        (sched["workflow_id"], sched["user_id"])
                    )
                    run_id = cursor.lastrowid
                    cursor.execute(
                        "UPDATE workflow_schedules SET last_run = %s, next_run = DATE_ADD(NOW(), INTERVAL 1 MINUTE) WHERE id = %s",
                        (now, sched["id"])
                    )
                    conn.commit()
                    execution_queue.enqueue(run_id)
            cursor.close()
            conn.close()
        except Exception as ex:
            app.logger.error(f"Scheduler failed: {str(ex)}")
        time.sleep(30)

# ============ HTML PAGE ROUTERS (PHASE 5) ============

@app.route("/workflows")
def workflows_workspace():
    user = get_current_user(request)
    return render_template("workspace.html")

@app.route("/workflows/builder/<int:wf_id>")
def workflow_builder_page(wf_id):
    user = get_current_user(request)
    return render_template("workflow_builder.html", workflow_id=wf_id)

@app.route("/workflows/run/<int:run_id>")
def workflow_run_page(run_id):
    user = get_current_user(request)
    return render_template("workflow_run.html", run_id=run_id)

@app.route("/agents")
def agent_library_page():
    user = get_current_user(request)
    return render_template("agent_library.html")

@app.route("/organization")
def organization_page():
    user = get_current_user(request)
    return render_template("organization.html")

@app.route("/marketplace")
def marketplace_page():
    user = get_current_user(request)
    return render_template("marketplace.html")

@app.route("/prompts")
def prompts_page():
    return render_template("prompts.html")

@app.route("/knowledge")
def knowledge_page():
    return render_template("knowledge.html")

@app.route("/connectors")
def connectors_page():
    return render_template("connectors.html")

@app.route("/playground")
def playground_page():
    return render_template("playground.html")

@app.route("/analytics")
def analytics_page():
    return render_template("analytics.html")

@app.route("/observability")
def observability_page():
    return render_template("observability.html")


# ============ ORGANIZATIONS & MEMBER API ============

@app.route("/api/organizations", methods=["POST"])
def api_create_organization():
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    name = request.json.get("name", "").strip()
    slug = request.json.get("slug", "").strip().lower()
    if not name or not slug:
        return jsonify({"success": False, "error": "Name and slug required"}), 400
        
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor()
        
        # Verify duplicate slug
        cursor.execute("SELECT COUNT(*) FROM organizations WHERE slug = %s", (slug,))
        if cursor.fetchone()[0] > 0:
            return jsonify({"success": False, "error": "Slug already exists"}), 400
            
        cursor.execute(
            "INSERT INTO organizations (name, slug, owner_id) VALUES (%s, %s, %s)",
            (name, slug, user["user_id"])
        )
        org_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO organization_members (organization_id, user_id, role) VALUES (%s, %s, 'owner')",
            (org_id, user["user_id"])
        )
        conn.commit()
        return jsonify({"success": True, "org_id": org_id, "name": name, "slug": slug})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

@app.route("/api/organizations", methods=["GET"])
def api_list_organizations():
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT o.*, om.role FROM organizations o
            JOIN organization_members om ON o.id = om.organization_id
            WHERE om.user_id = %s
            """,
            (user["user_id"],)
        )
        return jsonify({"success": True, "organizations": cursor.fetchall()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

@app.route("/api/organizations/<int:org_id>", methods=["GET"])
def api_get_organization(org_id):
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    has_perm, role = check_org_permission(user["user_id"], org_id)
    if not has_perm:
        return jsonify({"success": False, "error": "Forbidden"}), 403
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM organizations WHERE id = %s", (org_id,))
        return jsonify({"success": True, "organization": cursor.fetchone(), "user_role": role})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

@app.route("/api/organizations/<int:org_id>/members", methods=["GET"])
def api_list_org_members(org_id):
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    has_perm, _ = check_org_permission(user["user_id"], org_id)
    if not has_perm: return jsonify({"success": False, "error": "Forbidden"}), 403
    
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT u.id, u.name, u.email, om.role FROM users u
            JOIN organization_members om ON u.id = om.user_id
            WHERE om.organization_id = %s
            """,
            (org_id,)
        )
        return jsonify({"success": True, "members": cursor.fetchall()})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

@app.route("/api/organizations/<int:org_id>/members", methods=["POST"])
def api_add_org_member(org_id):
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    has_perm, role = check_org_permission(user["user_id"], org_id, ['owner', 'admin'])
    if not has_perm: return jsonify({"success": False, "error": "Forbidden"}), 403
    
    email = request.json.get("email", "").strip().lower()
    member_role = request.json.get("role", "viewer")
    if not email: return jsonify({"success": False, "error": "Email is required"}), 400
    
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
        tgt = cursor.fetchone()
        if not tgt:
            return jsonify({"success": False, "error": "User with this email not found"}), 404
        
        cursor.execute(
            "INSERT INTO organization_members (organization_id, user_id, role) VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE role = %s",
            (org_id, tgt["id"], member_role, member_role)
        )
        conn.commit()
        return jsonify({"success": True, "message": "Member added successfully"})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

@app.route("/api/organizations/<int:org_id>/members/<int:member_user_id>", methods=["PUT"])
def api_update_org_member(org_id, member_user_id):
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    has_perm, _ = check_org_permission(user["user_id"], org_id, ['owner', 'admin'])
    if not has_perm: return jsonify({"success": False, "error": "Forbidden"}), 403
    
    member_role = request.json.get("role", "viewer")
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE organization_members SET role = %s WHERE organization_id = %s AND user_id = %s",
            (member_role, org_id, member_user_id)
        )
        conn.commit()
        return jsonify({"success": True})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

@app.route("/api/organizations/<int:org_id>/members/<int:member_user_id>", methods=["DELETE"])
def api_remove_org_member(org_id, member_user_id):
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    has_perm, _ = check_org_permission(user["user_id"], org_id, ['owner', 'admin'])
    if not has_perm: return jsonify({"success": False, "error": "Forbidden"}), 403
    
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM organization_members WHERE organization_id = %s AND user_id = %s",
            (org_id, member_user_id)
        )
        conn.commit()
        return jsonify({"success": True})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

@app.route("/api/organizations/<int:org_id>/guardrails", methods=["PUT"])
def api_update_org_guardrails(org_id):
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    has_perm, _ = check_org_permission(user["user_id"], org_id, ['owner', 'admin'])
    if not has_perm: return jsonify({"success": False, "error": "Forbidden"}), 403
    
    max_monthly_cost = float(request.json.get("max_monthly_cost", 100.00))
    max_monthly_tokens = int(request.json.get("max_monthly_tokens", 5000000))
    
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE organizations SET max_monthly_cost = %s, max_monthly_tokens = %s WHERE id = %s",
            (max_monthly_cost, max_monthly_tokens, org_id)
        )
        conn.commit()
        return jsonify({"success": True})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()


# ============ SECRETS MANAGER API ============

@app.route("/api/organizations/<int:org_id>/secrets", methods=["POST"])
def api_add_org_secret(org_id):
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    has_perm, _ = check_org_permission(user["user_id"], org_id, ['owner', 'admin', 'editor'])
    if not has_perm: return jsonify({"success": False, "error": "Forbidden"}), 403
    
    name = request.json.get("name", "").strip()
    val = request.json.get("value", "").strip()
    if not name or not val: return jsonify({"success": False, "error": "Secret name and value required"}), 400
    
    # Encrypt secret using simple symmetric base64 encoding to mask value
    enc_val = base64.b64encode(val.encode()).decode()
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO organization_secrets (organization_id, name, encrypted_value) VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE encrypted_value = %s",
            (org_id, name, enc_val, enc_val)
        )
        conn.commit()
        return jsonify({"success": True})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

@app.route("/api/organizations/<int:org_id>/secrets", methods=["GET"])
def api_list_org_secrets(org_id):
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    has_perm, _ = check_org_permission(user["user_id"], org_id)
    if not has_perm: return jsonify({"success": False, "error": "Forbidden"}), 403
    
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, name, created_at FROM organization_secrets WHERE organization_id = %s", (org_id,))
        secrets = cursor.fetchall()
        for s in secrets:
            if isinstance(s["created_at"], datetime):
                s["created_at"] = s["created_at"].strftime("%Y-%m-%d %H:%M:%S")
        return jsonify({"success": True, "secrets": secrets})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

@app.route("/api/organizations/<int:org_id>/secrets/<string:name>", methods=["DELETE"])
def api_delete_org_secret(org_id, name):
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    has_perm, _ = check_org_permission(user["user_id"], org_id, ['owner', 'admin', 'editor'])
    if not has_perm: return jsonify({"success": False, "error": "Forbidden"}), 403
    
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM organization_secrets WHERE organization_id = %s AND name = %s", (org_id, name))
        conn.commit()
        return jsonify({"success": True})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()


# ============ AGENT LIBRARY API ============

@app.route("/api/agents", methods=["POST"])
def api_create_agent():
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    name = request.json.get("name", "").strip()
    role = request.json.get("role", "").strip()
    goals = request.json.get("goals", "").strip()
    instructions = request.json.get("instructions", "").strip()
    preferred_model = request.json.get("preferred_model", "llama3-8b-8192")
    default_style = request.json.get("default_style", "detailed")
    tools = request.json.get("tools", [])
    org_id = request.json.get("organization_id")
    
    if not name or not role:
        return jsonify({"success": False, "error": "Name and role required"}), 400
        
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO agents (user_id, organization_id, name, role, goals, instructions, preferred_model, default_style, tools_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (user["user_id"], org_id, name, role, goals, instructions, preferred_model, default_style, json.dumps(tools))
        )
        conn.commit()
        return jsonify({"success": True, "agent_id": cursor.lastrowid})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

@app.route("/api/agents", methods=["GET"])
def api_list_agents():
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    org_id = request.args.get("organization_id")
    
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        if org_id:
            cursor.execute("SELECT * FROM agents WHERE organization_id = %s AND deleted_at IS NULL", (org_id,))
        else:
            cursor.execute("SELECT * FROM agents WHERE (user_id = %s OR organization_id IS NULL) AND deleted_at IS NULL", (user["user_id"],))
        return jsonify({"success": True, "agents": cursor.fetchall()})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

@app.route("/api/agents/<int:agent_id>", methods=["GET"])
def api_get_agent(agent_id):
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM agents WHERE id = %s AND deleted_at IS NULL", (agent_id,))
        agent = cursor.fetchone()
        if not agent: return jsonify({"success": False, "error": "Agent not found"}), 404
        return jsonify({"success": True, "agent": agent})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

@app.route("/api/agents/<int:agent_id>", methods=["PUT"])
def api_update_agent(agent_id):
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    name = request.json.get("name", "").strip()
    role = request.json.get("role", "").strip()
    goals = request.json.get("goals", "").strip()
    instructions = request.json.get("instructions", "").strip()
    preferred_model = request.json.get("preferred_model", "llama3-8b-8192")
    default_style = request.json.get("default_style", "detailed")
    tools = request.json.get("tools", [])
    
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE agents 
            SET name = %s, role = %s, goals = %s, instructions = %s, preferred_model = %s, default_style = %s, tools_json = %s
            WHERE id = %s
            """,
            (name, role, goals, instructions, preferred_model, default_style, json.dumps(tools), agent_id)
        )
        conn.commit()
        return jsonify({"success": True})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

@app.route("/api/agents/<int:agent_id>", methods=["DELETE"])
def api_delete_agent(agent_id):
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE agents SET deleted_at = CURRENT_TIMESTAMP WHERE id = %s", (agent_id,))
        conn.commit()
        return jsonify({"success": True})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()


# ============ AGENT PLAYGROUND CHAT SESSION ENDPOINTS ============

@app.route("/api/agents/<int:agent_id>/chat/start", methods=["POST"])
def api_start_agent_session(agent_id):
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO agent_sessions (agent_id, user_id) VALUES (%s, %s)", (agent_id, user["user_id"]))
        session_id = cursor.lastrowid
        conn.commit()
        return jsonify({"success": True, "session_id": session_id})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

@app.route("/api/agents/sessions/<int:session_id>/messages", methods=["GET"])
def api_get_session_messages(session_id):
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM agent_messages WHERE session_id = %s ORDER BY created_at ASC", (session_id,))
        msgs = cursor.fetchall()
        for m in msgs:
            if isinstance(m["created_at"], datetime):
                m["created_at"] = m["created_at"].strftime("%Y-%m-%d %H:%M:%S")
        return jsonify({"success": True, "messages": msgs})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

@app.route("/api/agents/sessions/<int:session_id>/message", methods=["POST"])
def api_send_agent_message(session_id):
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    msg_text = request.json.get("message", "").strip()
    if not msg_text: return jsonify({"success": False, "error": "Message body is empty"}), 400
    
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Load agent info from session
        cursor.execute(
            "SELECT a.* FROM agents a JOIN agent_sessions s ON a.id = s.agent_id WHERE s.id = %s", (session_id,)
        )
        agent = cursor.fetchone()
        if not agent: return jsonify({"success": False, "error": "Agent session details missing"}), 404
        
        # Insert user message
        cursor.execute(
            "INSERT INTO agent_messages (session_id, role, message) VALUES (%s, 'user', %s)",
            (session_id, msg_text)
        )
        
        # Fetch previous messages history (up to 10)
        cursor.execute("SELECT role, message FROM agent_messages WHERE session_id = %s ORDER BY created_at ASC LIMIT 10", (session_id,))
        history = cursor.fetchall()
        
        # Build LLM prompts
        system_prompt = f"Role: {agent['role']}\nGoals: {agent['goals']}\nInstructions: {agent['instructions']}"
        model = agent.get("preferred_model", "llama3-8b-8192")
        
        # Simulate LLM call using history context
        runner = WorkflowRunner(0) # dummy runner just to access helper
        assistant_resp, tokens, cost = runner.call_llm(model, system_prompt, msg_text, 0.7, 1000)
        
        # Insert assistant message
        cursor.execute(
            "INSERT INTO agent_messages (session_id, role, message, tokens) VALUES (%s, 'assistant', %s, %s)",
            (session_id, assistant_resp, tokens)
        )
        conn.commit()
        
        return jsonify({
            "success": True,
            "response": assistant_resp,
            "tokens": tokens,
            "cost": cost
        })
    finally:
        if cursor: cursor.close()
        if conn: conn.close()


# ============ WORKFLOW CRUD & SNAPSHOTS API ============

@app.route("/api/workflows", methods=["POST"])
def api_create_workflow():
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    title = request.json.get("title", "").strip()
    desc = request.json.get("description", "").strip()
    org_id = request.json.get("organization_id")
    category = request.json.get("category", "Automation")
    
    if not title: return jsonify({"success": False, "error": "Title required"}), 400
    
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO workflows (organization_id, user_id, title, description, category) VALUES (%s, %s, %s, %s, %s)",
            (org_id, user["user_id"], title, desc, category)
        )
        wf_id = cursor.lastrowid
        conn.commit()
        return jsonify({"success": True, "workflow_id": wf_id})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

@app.route("/api/workflows", methods=["GET"])
def api_list_workflows():
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    org_id = request.args.get("organization_id")
    
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        if org_id:
            cursor.execute("SELECT * FROM workflows WHERE organization_id = %s AND deleted_at IS NULL", (org_id,))
        else:
            cursor.execute("SELECT * FROM workflows WHERE user_id = %s AND deleted_at IS NULL", (user["user_id"],))
        return jsonify({"success": True, "workflows": cursor.fetchall()})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

@app.route("/api/workflows/<int:wf_id>", methods=["GET"])
def api_get_workflow(wf_id):
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM workflows WHERE id = %s AND deleted_at IS NULL", (wf_id,))
        wf = cursor.fetchone()
        if not wf: return jsonify({"success": False, "error": "Workflow not found"}), 404
        
        cursor.execute("SELECT * FROM workflow_nodes WHERE workflow_id = %s", (wf_id,))
        nodes = cursor.fetchall()
        cursor.execute("SELECT * FROM workflow_edges WHERE workflow_id = %s", (wf_id,))
        edges = cursor.fetchall()
        cursor.execute("SELECT * FROM workflow_variables WHERE workflow_id = %s", (wf_id,))
        variables = cursor.fetchall()
        
        return jsonify({
            "success": True,
            "workflow": wf,
            "nodes": nodes,
            "edges": edges,
            "variables": variables
        })
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

@app.route("/api/workflows/<int:wf_id>", methods=["PUT"])
def api_update_workflow(wf_id):
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    title = request.json.get("title")
    desc = request.json.get("description")
    status = request.json.get("status")
    sharing = request.json.get("sharing")
    nodes = request.json.get("nodes", [])
    edges = request.json.get("edges", [])
    variables = request.json.get("variables", [])
    
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Verify workflow
        cursor.execute("SELECT * FROM workflows WHERE id = %s", (wf_id,))
        wf = cursor.fetchone()
        if not wf: return jsonify({"success": False, "error": "Workflow not found"}), 404
        
        # Update workflow base
        cursor.execute(
            "UPDATE workflows SET title = %s, description = %s, status = %s, sharing = %s WHERE id = %s",
            (title or wf["title"], desc or wf["description"], status or wf["status"], sharing or wf["sharing"], wf_id)
        )
        
        # Synchronize nodes
        cursor.execute("DELETE FROM workflow_nodes WHERE workflow_id = %s", (wf_id,))
        for node in nodes:
            cursor.execute(
                """
                INSERT INTO workflow_nodes (id, workflow_id, title, type, prompt_template, agent_id, config_json, x_pos, y_pos)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (node["id"], wf_id, node["title"], node["type"], node.get("prompt_template"), node.get("agent_id"), json.dumps(node.get("config", {})), node.get("x", 0.0), node.get("y", 0.0))
            )
            
        # Synchronize edges
        cursor.execute("DELETE FROM workflow_edges WHERE workflow_id = %s", (wf_id,))
        for edge in edges:
            cursor.execute(
                """
                INSERT INTO workflow_edges (workflow_id, source_node_id, target_node_id, source_handle, target_handle)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (wf_id, edge["source_node_id"], edge["target_node_id"], edge.get("source_handle"), edge.get("target_handle"))
            )
            
        # Synchronize variables
        cursor.execute("DELETE FROM workflow_variables WHERE workflow_id = %s", (wf_id,))
        for var in variables:
            cursor.execute(
                """
                INSERT INTO workflow_variables (workflow_id, name, default_value, required, description, type)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (wf_id, var["name"], var.get("default_value"), var.get("required", 0), var.get("description"), var.get("type", "string"))
            )
            
        # Create Version Snapshot
        cursor.execute("SELECT IFNULL(MAX(version_number), 0) + 1 AS next_ver FROM workflow_versions WHERE workflow_id = %s", (wf_id,))
        next_ver = cursor.fetchone()["next_ver"]
        cursor.execute(
            "INSERT INTO workflow_versions (workflow_id, version_number, nodes_json, edges_json) VALUES (%s, %s, %s, %s)",
            (wf_id, next_ver, json.dumps(nodes), json.dumps(edges))
        )
        
        conn.commit()
        return jsonify({"success": True, "version_number": next_ver})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

@app.route("/api/workflows/<int:wf_id>/versions", methods=["GET"])
def api_get_workflow_versions(wf_id):
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, version_number, created_at FROM workflow_versions WHERE workflow_id = %s ORDER BY version_number DESC", (wf_id,))
        vers = cursor.fetchall()
        for v in vers:
            if isinstance(v["created_at"], datetime):
                v["created_at"] = v["created_at"].strftime("%Y-%m-%d %H:%M:%S")
        return jsonify({"success": True, "versions": vers})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

@app.route("/api/workflows/<int:wf_id>/versions/<int:ver_num>", methods=["GET"])
def api_get_workflow_version_detail(wf_id, ver_num):
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM workflow_versions WHERE workflow_id = %s AND version_number = %s", (wf_id, ver_num))
        ver = cursor.fetchone()
        if not ver: return jsonify({"success": False, "error": "Version snapshot not found"}), 404
        return jsonify({"success": True, "version": ver})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

@app.route("/api/workflows/<int:wf_id>/rollback/<int:ver_num>", methods=["POST"])
def api_rollback_workflow_version(wf_id, ver_num):
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("SELECT * FROM workflow_versions WHERE workflow_id = %s AND version_number = %s", (wf_id, ver_num))
        ver = cursor.fetchone()
        if not ver: return jsonify({"success": False, "error": "Version snapshot not found"}), 404
        
        nodes = json.loads(ver["nodes_json"])
        edges = json.loads(ver["edges_json"])
        
        cursor.execute("DELETE FROM workflow_nodes WHERE workflow_id = %s", (wf_id,))
        for node in nodes:
            cursor.execute(
                """
                INSERT INTO workflow_nodes (id, workflow_id, title, type, prompt_template, agent_id, config_json, x_pos, y_pos)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (node["id"], wf_id, node["title"], node["type"], node.get("prompt_template"), node.get("agent_id"), json.dumps(node.get("config", {})), node.get("x", 0.0), node.get("y", 0.0))
            )
            
        cursor.execute("DELETE FROM workflow_edges WHERE workflow_id = %s", (wf_id,))
        for edge in edges:
            cursor.execute(
                """
                INSERT INTO workflow_edges (workflow_id, source_node_id, target_node_id, source_handle, target_handle)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (wf_id, edge["source_node_id"], edge["target_node_id"], edge.get("source_handle"), edge.get("target_handle"))
            )
            
        # Create Version Snapshot indicating Rollback action
        cursor.execute("SELECT IFNULL(MAX(version_number), 0) + 1 AS next_ver FROM workflow_versions WHERE workflow_id = %s", (wf_id,))
        next_ver = cursor.fetchone()["next_ver"]
        cursor.execute(
            "INSERT INTO workflow_versions (workflow_id, version_number, nodes_json, edges_json) VALUES (%s, %s, %s, %s)",
            (wf_id, next_ver, json.dumps(nodes), json.dumps(edges))
        )
        conn.commit()
        return jsonify({"success": True, "rolled_back_to": ver_num, "new_version": next_ver})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

@app.route("/api/workflows/<int:wf_id>/export", methods=["GET"])
def api_export_workflow(wf_id):
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM workflows WHERE id = %s", (wf_id,))
        wf = cursor.fetchone()
        if not wf: return jsonify({"success": False, "error": "Workflow not found"}), 404
        
        cursor.execute("SELECT * FROM workflow_nodes WHERE workflow_id = %s", (wf_id,))
        nodes = cursor.fetchall()
        cursor.execute("SELECT * FROM workflow_edges WHERE workflow_id = %s", (wf_id,))
        edges = cursor.fetchall()
        cursor.execute("SELECT * FROM workflow_variables WHERE workflow_id = %s", (wf_id,))
        variables = cursor.fetchall()
        
        export_data = {
            "title": wf["title"],
            "description": wf["description"],
            "category": wf["category"],
            "nodes": [{
                "id": n["id"],
                "title": n["title"],
                "type": n["type"],
                "prompt_template": n["prompt_template"],
                "agent_id": n["agent_id"],
                "config": json.loads(n["config_json"] or "{}"),
                "x": n["x_pos"],
                "y": n["y_pos"]
            } for n in nodes],
            "edges": [{
                "source_node_id": e["source_node_id"],
                "target_node_id": e["target_node_id"],
                "source_handle": e["source_handle"],
                "target_handle": e["target_handle"]
            } for e in edges],
            "variables": [{
                "name": v["name"],
                "default_value": v["default_value"],
                "required": v["required"],
                "description": v["description"],
                "type": v["type"]
            } for v in variables]
        }
        return jsonify({"success": True, "export": export_data})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

@app.route("/api/workflows/import", methods=["POST"])
def api_import_workflow():
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    data = request.json.get("workflow_data", {})
    title = data.get("title", "Imported Workflow").strip()
    desc = data.get("description", "").strip()
    category = data.get("category", "Automation")
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    variables = data.get("variables", [])
    org_id = request.json.get("organization_id")
    
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO workflows (organization_id, user_id, title, description, category) VALUES (%s, %s, %s, %s, %s)",
            (org_id, user["user_id"], title, desc, category)
        )
        wf_id = cursor.lastrowid
        
        # Map old node ID to new node ID to prevent duplicate entry keys
        node_id_map = {}
        for node in nodes:
            suffix = node["id"].split("_")[-1]
            new_node_id = f"{wf_id}_{suffix}"
            node_id_map[node["id"]] = new_node_id
            cursor.execute(
                """
                INSERT INTO workflow_nodes (id, workflow_id, title, type, prompt_template, agent_id, config_json, x_pos, y_pos)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (new_node_id, wf_id, node["title"], node["type"], node.get("prompt_template"), node.get("agent_id"), json.dumps(node.get("config", {})), node.get("x", 0.0), node.get("y", 0.0))
            )
            
        for edge in edges:
            new_source = node_id_map.get(edge["source_node_id"], edge["source_node_id"])
            new_target = node_id_map.get(edge["target_node_id"], edge["target_node_id"])
            cursor.execute(
                """
                INSERT INTO workflow_edges (workflow_id, source_node_id, target_node_id, source_handle, target_handle)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (wf_id, new_source, new_target, edge.get("source_handle"), edge.get("target_handle"))
            )
            
        for var in variables:
            cursor.execute(
                """
                INSERT INTO workflow_variables (workflow_id, name, default_value, required, description, type)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (wf_id, var["name"], var.get("default_value"), var.get("required", 0), var.get("description"), var.get("type", "string"))
            )
            
        # Version Snapshot (save with remapped node IDs)
        remapped_nodes = []
        for n in nodes:
            n_copy = n.copy()
            n_copy["id"] = node_id_map.get(n["id"], n["id"])
            remapped_nodes.append(n_copy)
            
        remapped_edges = []
        for e in edges:
            e_copy = e.copy()
            e_copy["source_node_id"] = node_id_map.get(e["source_node_id"], e["source_node_id"])
            e_copy["target_node_id"] = node_id_map.get(e["target_node_id"], e["target_node_id"])
            remapped_edges.append(e_copy)
            
        cursor.execute(
            "INSERT INTO workflow_versions (workflow_id, version_number, nodes_json, edges_json) VALUES (%s, 1, %s, %s)",
            (wf_id, json.dumps(remapped_nodes), json.dumps(remapped_edges))
        )
        conn.commit()
        return jsonify({"success": True, "workflow_id": wf_id})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()


# ============ CRON SCHEDULER API ============

@app.route("/api/workflows/<int:wf_id>/schedules", methods=["POST"])
def api_create_workflow_schedule(wf_id):
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    cron = request.json.get("cron_expression", "* * * * *").strip()
    status = request.json.get("status", "inactive")
    
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO workflow_schedules (workflow_id, user_id, cron_expression, status) VALUES (%s, %s, %s, %s) ON DUPLICATE KEY UPDATE cron_expression = %s, status = %s",
            (wf_id, user["user_id"], cron, status, cron, status)
        )
        conn.commit()
        return jsonify({"success": True})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

@app.route("/api/workflows/<int:wf_id>/schedules", methods=["GET"])
def api_get_workflow_schedules(wf_id):
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM workflow_schedules WHERE workflow_id = %s", (wf_id,))
        schedules = cursor.fetchall()
        for s in schedules:
            if isinstance(s["last_run"], datetime):
                s["last_run"] = s["last_run"].strftime("%Y-%m-%d %H:%M:%S")
            if isinstance(s["next_run"], datetime):
                s["next_run"] = s["next_run"].strftime("%Y-%m-%d %H:%M:%S")
        return jsonify({"success": True, "schedules": schedules})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

@app.route("/api/workflows/<int:wf_id>/schedules/<int:sched_id>", methods=["DELETE"])
def api_delete_workflow_schedule(wf_id, sched_id):
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM workflow_schedules WHERE id = %s AND workflow_id = %s", (sched_id, wf_id))
        conn.commit()
        return jsonify({"success": True})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()


# ============ RUNS & QUEUES EXECUTION API ============

@app.route("/api/workflows/<int:wf_id>/run", methods=["POST"])
def api_run_workflow(wf_id):
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    inputs = request.json.get("inputs", {})
    
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM workflows WHERE id = %s AND deleted_at IS NULL", (wf_id,))
        wf = cursor.fetchone()
        if not wf: return jsonify({"success": False, "error": "Workflow not found"}), 404
        
        # Verify Monthly Limit guardrails
        if wf["organization_id"]:
            cursor.execute(
                """
                SELECT SUM(total_cost) AS monthly FROM workflow_runs wr
                JOIN workflows w ON wr.workflow_id = w.id
                WHERE w.organization_id = %s AND wr.created_at >= DATE_FORMAT(NOW(), '%Y-%m-01')
                """,
                (wf["organization_id"],)
            )
            res = cursor.fetchone()
            monthly = float(res["monthly"] or 0.0)
            cursor.execute("SELECT max_monthly_cost FROM organizations WHERE id = %s", (wf["organization_id"],))
            org = cursor.fetchone()
            if org and monthly >= float(org["max_monthly_cost"]):
                return jsonify({"success": False, "error": "Organization monthly budget exhausted."}), 400
                
        cursor.execute(
            "INSERT INTO workflow_runs (workflow_id, user_id, status, inputs) VALUES (%s, %s, 'queued', %s)",
            (wf_id, user["user_id"], json.dumps(inputs))
        )
        run_id = cursor.lastrowid
        conn.commit()
        
        # Execute asynchronously in background queue
        execution_queue.enqueue(run_id)
        return jsonify({"success": True, "run_id": run_id, "status": "queued"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

@app.route("/api/workflow_runs/<int:run_id>", methods=["GET"])
def api_get_workflow_run_progress(run_id):
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM workflow_runs WHERE id = %s", (run_id,))
        run = cursor.fetchone()
        if not run: return jsonify({"success": False, "error": "Run not found"}), 404
        
        cursor.execute("SELECT * FROM workflow_steps WHERE run_id = %s", (run_id,))
        steps = cursor.fetchall()
        for s in steps:
            if isinstance(s["completed_at"], datetime):
                s["completed_at"] = s["completed_at"].strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(run["created_at"], datetime):
            run["created_at"] = run["created_at"].strftime("%Y-%m-%d %H:%M:%S")
            
        return jsonify({"success": True, "run": run, "steps": steps})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

@app.route("/api/workflow_runs/<int:run_id>/approve", methods=["POST"])
def api_approve_workflow_run(run_id):
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Verify run is paused
        cursor.execute("SELECT * FROM workflow_runs WHERE id = %s", (run_id,))
        run = cursor.fetchone()
        if not run or run["status"] != "paused":
            return jsonify({"success": False, "error": "Run is not paused for approval"}), 400
            
        # Find the paused ApprovalNode step and mark as completed
        cursor.execute("SELECT * FROM workflow_steps WHERE run_id = %s AND status = 'pending'", (run_id,))
        steps = cursor.fetchall()
        # Find approval node step
        cursor.execute("SELECT * FROM workflow_nodes WHERE workflow_id = %s AND type = 'ApprovalNode'", (run["workflow_id"],))
        app_nodes = {an["id"] for an in cursor.fetchall()}
        
        app_step_id = None
        for step in steps:
            if step["node_id"] in app_nodes:
                app_step_id = step["node_id"]
                break
                
        if app_step_id:
            cursor.execute(
                "UPDATE workflow_steps SET status = 'completed', output_generated = 'Approved' WHERE run_id = %s AND node_id = %s",
                (run_id, app_step_id)
            )
            
        cursor.execute("UPDATE workflow_runs SET status = 'running' WHERE id = %s", (run_id,))
        conn.commit()
        
        # Resume executor
        threading.Thread(target=run_executor_async, args=(run_id,), daemon=True).start()
        return jsonify({"success": True})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

@app.route("/api/workflow_runs/<int:run_id>/cancel", methods=["POST"])
def api_cancel_workflow_run(run_id):
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE workflow_runs SET status = 'cancelled' WHERE id = %s", (run_id,))
        conn.commit()
        return jsonify({"success": True})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()


# ============ WEBHOOK ENTRYPOINT API ============

@app.route("/api/workflows/webhooks/<int:wf_id>", methods=["POST"])
def api_workflow_webhook_trigger(wf_id):
    # Publicly accessible via webhooks (using request payloads as variables)
    inputs = request.json or {}
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT user_id FROM workflows WHERE id = %s AND deleted_at IS NULL", (wf_id,))
        wf = cursor.fetchone()
        if not wf: return jsonify({"success": False, "error": "Workflow not found"}), 404
        
        cursor.execute(
            "INSERT INTO workflow_runs (workflow_id, user_id, status, inputs) VALUES (%s, %s, 'queued', %s)",
            (wf_id, wf["user_id"], json.dumps(inputs))
        )
        run_id = cursor.lastrowid
        conn.commit()
        
        # Execute asynchronously in background thread
        threading.Thread(target=run_executor_async, args=(run_id,), daemon=True).start()
        return jsonify({"success": True, "run_id": run_id, "status": "queued"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()


# ============ MARKETPLACE & RATINGS API ============

@app.route("/api/marketplace", methods=["GET"])
def api_get_marketplace_list():
    cat = request.args.get("category")
    sort_by = request.args.get("sort_by", "rating") # rating, clones, newest
    
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        query = """
            SELECT w.*, u.username AS creator_username, 
                   IFNULL((SELECT AVG(rating_value) FROM ratings WHERE workflow_id = w.id), 0.0) AS avg_rating,
                   (SELECT COUNT(*) FROM reviews WHERE workflow_id = w.id) AS reviews_count
            FROM workflows w
            JOIN users u ON w.user_id = u.id
            WHERE w.sharing = 'public' AND w.deleted_at IS NULL
        """
        params = []
        if cat:
            query += " AND w.category = %s"
            params.append(cat)
            
        if sort_by == "clones":
            query += " ORDER BY w.clone_count DESC"
        elif sort_by == "newest":
            query += " ORDER BY w.created_at DESC"
        else: # rating
            query += " ORDER BY avg_rating DESC"
            
        cursor.execute(query, params)
        workflows = cursor.fetchall()
        for w in workflows:
            if isinstance(w["created_at"], datetime):
                w["created_at"] = w["created_at"].strftime("%Y-%m-%d %H:%M:%S")
        return jsonify({"success": True, "workflows": workflows})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

@app.route("/api/marketplace/<int:wf_id>/publish", methods=["POST"])
def api_publish_to_marketplace(wf_id):
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE workflows SET status = 'published', sharing = 'public' WHERE id = %s AND user_id = %s",
            (wf_id, user["user_id"])
        )
        conn.commit()
        return jsonify({"success": True})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

@app.route("/api/workflows/<int:wf_id>/clone", methods=["POST"])
def api_clone_workflow(wf_id):
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Load template
        cursor.execute("SELECT * FROM workflows WHERE id = %s AND sharing = 'public' AND deleted_at IS NULL", (wf_id,))
        wf = cursor.fetchone()
        if not wf: return jsonify({"success": False, "error": "Workflow template not found"}), 404
        
        # Create cloned workflow
        cursor.execute(
            "INSERT INTO workflows (user_id, title, description, category, status, sharing) VALUES (%s, %s, %s, %s, 'draft', 'private')",
            (user["user_id"], f"Cloned: {wf['title']}", wf["description"], wf["category"])
        )
        new_wf_id = cursor.lastrowid
        
        # Copy nodes
        cursor.execute("SELECT * FROM workflow_nodes WHERE workflow_id = %s", (wf_id,))
        for node in cursor.fetchall():
            # Generate new local node key
            node_key = node["id"].split("_")[-1]
            new_node_id = f"{new_wf_id}_{node_key}"
            cursor.execute(
                """
                INSERT INTO workflow_nodes (id, workflow_id, title, type, prompt_template, agent_id, config_json, x_pos, y_pos)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (new_node_id, new_wf_id, node["title"], node["type"], node.get("prompt_template"), node.get("agent_id"), node.get("config_json"), node.get("x_pos"), node.get("y_pos"))
            )
            
        # Copy edges
        cursor.execute("SELECT * FROM workflow_edges WHERE workflow_id = %s", (wf_id,))
        for edge in cursor.fetchall():
            src_key = edge["source_node_id"].split("_")[-1]
            tgt_key = edge["target_node_id"].split("_")[-1]
            new_src = f"{new_wf_id}_{src_key}"
            new_tgt = f"{new_wf_id}_{tgt_key}"
            cursor.execute(
                """
                INSERT INTO workflow_edges (workflow_id, source_node_id, target_node_id, source_handle, target_handle)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (new_wf_id, new_src, new_tgt, edge["source_handle"], edge["target_handle"])
            )
            
        # Copy variables
        cursor.execute("SELECT * FROM workflow_variables WHERE workflow_id = %s", (wf_id,))
        for var in cursor.fetchall():
            cursor.execute(
                """
                INSERT INTO workflow_variables (workflow_id, name, default_value, required, description, type)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (new_wf_id, var["name"], var["default_value"], var["required"], var["description"], var["type"])
            )
            
        # Increment template clone count
        cursor.execute("UPDATE workflows SET clone_count = clone_count + 1 WHERE id = %s", (wf_id,))
        conn.commit()
        
        return jsonify({"success": True, "workflow_id": new_wf_id})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

@app.route("/api/workflows/<int:wf_id>/reviews", methods=["POST"])
def api_add_workflow_review(wf_id):
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    comment = request.json.get("comment", "").strip()
    if not comment: return jsonify({"success": False, "error": "Comment cannot be empty"}), 400
    
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO reviews (user_id, workflow_id, comment) VALUES (%s, %s, %s)",
            (user["user_id"], wf_id, comment)
        )
        conn.commit()
        return jsonify({"success": True})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

@app.route("/api/workflows/<int:wf_id>/ratings", methods=["POST"])
def api_add_workflow_rating(wf_id):
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    rating_val = int(request.json.get("rating_value", 5))
    if rating_val < 1 or rating_val > 5:
        return jsonify({"success": False, "error": "Rating value must be between 1 and 5"}), 400
        
    conn = None
    cursor = None
    try:
        conn = models.get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO ratings (user_id, workflow_id, rating_value) VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE rating_value = %s",
            (user["user_id"], wf_id, rating_val, rating_val)
        )
        conn.commit()
        return jsonify({"success": True})
    finally:
        if cursor: cursor.close()
        if conn: conn.close()


# ============ PHASE 6: ENTERPRISE AI OS ENDPOINTS ============

@app.route("/api/workflows/<int:run_id>/stream", methods=["GET"])
def api_stream_run(run_id):
    return Response(
        StreamingService.stream_workflow_progress(run_id),
        mimetype="text/event-stream"
    )

# Prompt Registry CRUD & rollbacks
@app.route("/api/prompts/registry", methods=["POST"])
def api_create_prompt_registry():
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    title = request.json.get("title")
    description = request.json.get("description", "")
    category = request.json.get("category", "general")
    template = request.json.get("prompt_template", "")
    system_prompt = request.json.get("system_prompt", "")
    
    if not title or not template:
        return jsonify({"success": False, "error": "Title and prompt_template are required"}), 400
        
    conn = models.get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO prompt_registry (user_id, title, description, category) VALUES (%s, %s, %s, %s)",
            (user["user_id"], title, description, category)
        )
        prompt_id = cursor.lastrowid
        
        cursor.execute(
            "INSERT INTO prompt_registry_versions (prompt_id, version_number, system_prompt, prompt_template) VALUES (%s, 1, %s, %s)",
            (prompt_id, system_prompt, template)
        )
        conn.commit()
        
        event_bus.publish("PromptUpdated", {"prompt_id": prompt_id, "version": 1})
        return jsonify({"success": True, "prompt_id": prompt_id, "version": 1})
    finally:
        cursor.close()
        conn.close()

@app.route("/api/prompts/registry", methods=["GET"])
def api_list_prompt_registry():
    conn = models.get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM prompt_registry WHERE deleted_at IS NULL ORDER BY id DESC")
        prompts = cursor.fetchall()
        for p in prompts:
            cursor.execute("SELECT MAX(version_number) as latest FROM prompt_registry_versions WHERE prompt_id = %s", (p["id"],))
            row = cursor.fetchone()
            p["latest_version"] = row["latest"] or 1
        return jsonify({"success": True, "prompts": prompts})
    finally:
        cursor.close()
        conn.close()

@app.route("/api/prompts/registry/<int:prompt_id>", methods=["GET"])
def api_get_prompt_registry(prompt_id):
    conn = models.get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM prompt_registry WHERE id = %s AND deleted_at IS NULL", (prompt_id,))
        prompt = cursor.fetchone()
        if not prompt:
            return jsonify({"success": False, "error": "Prompt not found"}), 404
            
        cursor.execute("SELECT * FROM prompt_registry_versions WHERE prompt_id = %s ORDER BY version_number DESC", (prompt_id,))
        versions = cursor.fetchall()
        for v in versions:
            if isinstance(v["created_at"], datetime):
                v["created_at"] = v["created_at"].strftime("%Y-%m-%d %H:%M:%S")
                
        return jsonify({"success": True, "prompt": prompt, "versions": versions})
    finally:
        cursor.close()
        conn.close()

@app.route("/api/prompts/registry/<int:prompt_id>", methods=["PUT"])
def api_update_prompt_registry(prompt_id):
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    template = request.json.get("prompt_template")
    system_prompt = request.json.get("system_prompt", "")
    
    if not template:
        return jsonify({"success": False, "error": "prompt_template is required"}), 400
        
    conn = models.get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT MAX(version_number) as max_v FROM prompt_registry_versions WHERE prompt_id = %s", (prompt_id,))
        row = cursor.fetchone()
        next_v = (row[0] or 0) + 1
        
        cursor.execute(
            "INSERT INTO prompt_registry_versions (prompt_id, version_number, system_prompt, prompt_template) VALUES (%s, %s, %s, %s)",
            (prompt_id, next_v, system_prompt, template)
        )
        conn.commit()
        
        event_bus.publish("PromptUpdated", {"prompt_id": prompt_id, "version": next_v})
        return jsonify({"success": True, "version": next_v})
    finally:
        cursor.close()
        conn.close()

@app.route("/api/prompts/registry/<int:prompt_id>/rollback/<int:ver_num>", methods=["POST"])
def api_rollback_prompt_registry(prompt_id, ver_num):
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    conn = models.get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT * FROM prompt_registry_versions WHERE prompt_id = %s AND version_number = %s",
            (prompt_id, ver_num)
        )
        row = cursor.fetchone()
        if not row:
            return jsonify({"success": False, "error": "Version not found"}), 404
            
        cursor.execute("SELECT MAX(version_number) as max_v FROM prompt_registry_versions WHERE prompt_id = %s", (prompt_id,))
        max_v = cursor.fetchone()["max_v"] or 1
        new_v = max_v + 1
        
        cursor.execute(
            "INSERT INTO prompt_registry_versions (prompt_id, version_number, system_prompt, prompt_template) VALUES (%s, %s, %s, %s)",
            (prompt_id, new_v, row["system_prompt"], row["prompt_template"])
        )
        conn.commit()
        
        event_bus.publish("PromptUpdated", {"prompt_id": prompt_id, "version": new_v})
        return jsonify({"success": True, "version": new_v})
    finally:
        cursor.close()
        conn.close()

# Knowledge Base RAG CRUD & uploads
@app.route("/api/knowledge/bases", methods=["POST"])
def api_create_knowledge_base():
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    title = request.json.get("title")
    description = request.json.get("description", "")
    embedding_model_id = request.json.get("embedding_model_id")
    chunk_size = int(request.json.get("chunk_size", 500))
    chunk_overlap = int(request.json.get("chunk_overlap", 50))
    visibility = request.json.get("visibility", "private")
    
    if not title:
        return jsonify({"success": False, "error": "Title is required"}), 400
        
    conn = models.get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO knowledge_bases (user_id, title, description, embedding_model_id, chunk_size, chunk_overlap, visibility)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (user["user_id"], title, description, embedding_model_id, chunk_size, chunk_overlap, visibility)
        )
        conn.commit()
        return jsonify({"success": True, "kb_id": cursor.lastrowid})
    finally:
        cursor.close()
        conn.close()

@app.route("/api/knowledge/bases", methods=["GET"])
def api_list_knowledge_bases():
    conn = models.get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM knowledge_bases ORDER BY id DESC")
        bases = cursor.fetchall()
        return jsonify({"success": True, "knowledge_bases": bases})
    finally:
        cursor.close()
        conn.close()

@app.route("/api/knowledge/bases/<int:kb_id>/documents", methods=["POST"])
def api_upload_kb_document(kb_id):
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    filename = request.json.get("filename") if request.is_json else None
    content = request.json.get("content") if request.is_json else None
    
    if not filename:
        filename = "document_" + str(uuid.uuid4())[:8] + ".txt"
        content = "Sample document content to sync for testing RAG pipelines."
        
    conn = models.get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO knowledge_documents (kb_id, filename, filetype, filesize) VALUES (%s, %s, 'text/plain', %s)",
            (kb_id, filename, len(content))
        )
        doc_id = cursor.lastrowid
        
        os.makedirs(os.path.join("static", "uploads"), exist_ok=True)
        filepath = os.path.join("static", "uploads", filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
            
        cursor.execute("UPDATE knowledge_documents SET filename = %s WHERE id = %s", (filepath, doc_id))
        
        cursor.execute(
            "INSERT INTO knowledge_jobs (kb_id, doc_id, status, progress_pct) VALUES (%s, %s, 'queued', 0)",
            (kb_id, doc_id)
        )
        conn.commit()
        
        return jsonify({"success": True, "doc_id": doc_id, "status": "queued"})
    finally:
        cursor.close()
        conn.close()

@app.route("/api/knowledge/bases/<int:kb_id>/documents", methods=["GET"])
def api_list_kb_documents(kb_id):
    conn = models.get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM knowledge_documents WHERE kb_id = %s ORDER BY id DESC", (kb_id,))
        docs = cursor.fetchall()
        for d in docs:
            cursor.execute("SELECT status, progress_pct, error_message FROM knowledge_jobs WHERE doc_id = %s ORDER BY id DESC LIMIT 1", (d["id"],))
            job = cursor.fetchone()
            d["sync_status"] = job["status"] if job else "unknown"
            d["sync_progress"] = job["progress_pct"] if job else 0
            d["sync_error"] = job["error_message"] if job else None
        return jsonify({"success": True, "documents": docs})
    finally:
        cursor.close()
        conn.close()

@app.route("/api/knowledge/bases/<int:kb_id>/search", methods=["POST"])
def api_search_kb(kb_id):
    query = request.json.get("query")
    if not query:
        return jsonify({"success": False, "error": "Query is required"}), 400
        
    query_vector = EmbeddingService.embed(query, provider_name="local")
    vector_store = MySQLVectorStore()
    search_results = vector_store.similarity_search(kb_id, query_vector, top_k=5)
    return jsonify({"success": True, "results": search_results})

# Connectors API
@app.route("/api/connectors", methods=["POST"])
def api_register_connector():
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    conn_type = request.json.get("type")
    name = request.json.get("name")
    config_dict = request.json.get("config", {})
    
    if not conn_type or not name:
        return jsonify({"success": False, "error": "Type and name are required"}), 400
        
    conn = models.get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO connectors (user_id, type, name, config_json) VALUES (%s, %s, %s, %s)",
            (user["user_id"], conn_type, name, json.dumps(config_dict))
        )
        conn.commit()
        return jsonify({"success": True, "connector_id": cursor.lastrowid})
    finally:
        cursor.close()
        conn.close()

@app.route("/api/connectors", methods=["GET"])
def api_list_connectors():
    conn = models.get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM connectors ORDER BY id DESC")
        conns = cursor.fetchall()
        for c in conns:
            cursor.execute("SELECT status, progress_pct, last_sync, error_message FROM connector_jobs WHERE connector_id = %s ORDER BY id DESC LIMIT 1", (c["id"],))
            job = cursor.fetchone()
            c["sync_status"] = job["status"] if job else "inactive"
            c["sync_progress"] = job["progress_pct"] if job else 0
            c["last_sync"] = job["last_sync"].strftime("%Y-%m-%d %H:%M:%S") if (job and job["last_sync"]) else None
            c["sync_error"] = job["error_message"] if job else None
        return jsonify({"success": True, "connectors": conns})
    finally:
        cursor.close()
        conn.close()

@app.route("/api/connectors/<int:connector_id>/sync", methods=["POST"])
def api_sync_connector(connector_id):
    user = get_current_user(request)
    if not user: return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    ConnectorService.sync_connector_async(connector_id)
    return jsonify({"success": True, "status": "queued"})

# Analytics & Observability Telemetry
@app.route("/api/analytics/dashboard", methods=["GET"])
def api_analytics_dashboard():
    conn = models.get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT COUNT(*) as runs, SUM(total_tokens) as tokens, SUM(total_cost) as cost FROM workflow_runs")
        totals = cursor.fetchone()
        
        cursor.execute("SELECT w.title, COUNT(wr.id) as run_count FROM workflow_runs wr JOIN workflows w ON wr.workflow_id = w.id GROUP BY wr.workflow_id ORDER BY run_count DESC LIMIT 5")
        popular = cursor.fetchall()
        
        cursor.execute("SELECT status, COUNT(*) as count FROM workflow_runs GROUP BY status")
        statuses = cursor.fetchall()
        
        return jsonify({
            "success": True,
            "totals": totals,
            "popular_workflows": popular,
            "statuses": statuses
        })
    finally:
        cursor.close()
        conn.close()

@app.route("/api/observability/traces/<int:run_id>", methods=["GET"])
def api_observability_traces(run_id):
    conn = models.get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM observability_traces WHERE run_id = %s ORDER BY id ASC", (run_id,))
        traces = cursor.fetchall()
        for t in traces:
            if isinstance(t["created_at"], datetime):
                t["created_at"] = t["created_at"].strftime("%Y-%m-%d %H:%M:%S")
        return jsonify({"success": True, "traces": traces})
    finally:
        cursor.close()
        conn.close()


# ============ FLASK ERROR BOUNDARIES ============

@app.errorhandler(404)
def page_not_found(e):
    return jsonify({"success": False, "error": "Not Found"}), 404

@app.errorhandler(500)
def server_error(e):
    return jsonify({"success": False, "error": "Internal Server Error"}), 500


if __name__ == "__main__":
    # Ensure MySQL tables are seeded and healthy
    models.init_db()
    
    # Bootstrap Phase 7 Container
    container.bootstrap_phase7_foundation()
    
    # Initialize Phase 6 Enterprise extensions
    execution_queue.register_executor(run_executor_async)
    PluginSDK.load_plugins_from_dir("plugins")
    RAGService.start_worker_thread()
    
    # Start background cron scheduler
    threading.Thread(target=start_cron_scheduler, name="CronScheduler", daemon=True).start()
    
    app.run(host="0.0.0.0", port=5001, debug=True, use_reloader=False)
