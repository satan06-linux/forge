import os
import json
import bcrypt
from flask import Blueprint, request, render_template, jsonify, current_app
import models
import config

auth_blueprint = Blueprint("auth_blueprint", __name__)

@auth_blueprint.route("/auth")
def auth_gate():
    """Renders the JWT register/login gateway card."""
    from app import get_current_user, log_analytics_event
    user = get_current_user(request)
    log_analytics_event("page_view", user_id=user["user_id"] if user else None)
    return render_template("auth.html")

@auth_blueprint.route("/api/auth/register", methods=["POST"])
def api_register():
    """Registers a new creator in the MySQL users table."""
    from app import get_client_ip, register_limiter, log_analytics_event, generate_jwt_token
    
    client_ip = get_client_ip()
    if not register_limiter.is_allowed(client_ip):
        # We can fetch loggers via logging module or current_app
        import logging
        security_logger = logging.getLogger("forge.security")
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
        # Import ConfigService inside function
        from services.config_service import ConfigService
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
        current_app.logger.info(f"User {email} registered successfully with role {role} [user_id={user_id}]")
        return jsonify({
            "success": True,
            "token": token,
            "user": {"id": user_id, "name": name, "email": email}
        })
    except Exception as e:
        current_app.logger.error(f"Registration failed: {str(e)}", exc_info=True)
        return jsonify({"success": False, "error": "Registration failed due to an internal error."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@auth_blueprint.route("/api/auth/login", methods=["POST"])
def api_login():
    """Authenticates credentials against MySQL users table and issues a JWT."""
    from app import get_client_ip, login_limiter, log_analytics_event, generate_jwt_token
    
    client_ip = get_client_ip()
    if not login_limiter.is_allowed(client_ip):
        import logging
        security_logger = logging.getLogger("forge.security")
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
        current_app.logger.error(f"Database search error during login: {str(e)}", exc_info=True)
        return jsonify({"success": False, "error": "Database retrieval failed."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    if user and bcrypt.checkpw(password.encode("utf-8"), user["password_hash"].encode("utf-8")):
        if user.get("is_banned"):
            import logging
            security_logger = logging.getLogger("forge.security")
            security_logger.warning(f"Banned login attempt for email {email} from IP {client_ip}")
            return jsonify({"success": False, "error": "This account has been suspended by administrators."}), 403
            
        token = generate_jwt_token(user["id"], user["name"], user["email"], user["role"])
        log_analytics_event("login", user_id=user["id"])
        current_app.logger.info(f"User {email} logged in successfully [user_id={user['id']}]")
        return jsonify({
            "success": True,
            "token": token,
            "user": {"id": user["id"], "name": user["name"], "email": user["email"]}
        })
    else:
        return jsonify({"success": False, "error": "Invalid email or password."}), 401
