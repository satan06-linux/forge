import mysql.connector
import sys
import os

# Add local path to import models cleanly
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import config
import models

def run_diagnostics():
    print("=" * 60)
    print("        🛠️ FORGE.AI DATABASE DIAGNOSTIC UTILITY")
    print("=" * 60)
    print(f"Target Host: {config.MYSQL_HOST}")
    print(f"Target User: {config.MYSQL_USER}")
    print(f"Target Database: {config.MYSQL_DATABASE}")
    print("-" * 60)

    print("[1/3] Testing raw connection to MySQL server...")
    try:
        conn = mysql.connector.connect(
            host=config.MYSQL_HOST,
            user=config.MYSQL_USER,
            password=config.MYSQL_PASSWORD
        )
        print("✅ SUCCESS: Successfully connected to local MySQL server!")
        conn.close()
    except mysql.connector.Error as err:
        print("❌ FAILED: Unable to reach the local MySQL server.")
        print("\n" + "!" * 50)
        print("👨‍💻 DIAGNOSTIC ACTION PLAN FOR WINDOWS DEVELOPERS:")
        print("1. Start your local database engine:")
        print("   - If using XAMPP: Open 'XAMPP Control Panel' and click 'Start' next to MySQL.")
        print("   - If using WampServer: Ensure WampServer is running (green status tray icon).")
        print("   - If using MySQL Installer: Open Services (services.msc) and start the 'MySQL' service.")
        print("2. Check database credentials in your '.env' file:")
        print("   - Verify MYSQL_HOST (typically 'localhost')")
        print("   - Verify MYSQL_USER (typically 'root')")
        print("   - Verify MYSQL_PASSWORD (blank by default in XAMPP)")
        print("!" * 50 + "\n")
        return False

    print("[2/3] Initializing 'forge_db' database and tables schema...")
    success = models.init_db()
    if success:
        print("✅ SUCCESS: Database and all history ledger tables are fully operational!")
    else:
        print("❌ FAILED: Successfully connected to server, but database schema creation failed.")
        return False

    print("[3/3] Running dry-run database insert & delete transaction check...")
    try:
        conn = models.get_db_connection(include_db=True)
        cursor = conn.cursor()
        
        # Test insert
        cursor.execute(
            "INSERT INTO prompts (user_id, input_text, mcq_questions, mcq_answers, generated_prompt, category) VALUES (%s, %s, %s, %s, %s, %s)",
            (None, "anti-gravity test", "{}", "{}", "Dry run diagnostic compiled prompt.", "Image gen")
        )
        test_id = cursor.lastrowid
        
        # Test select
        cursor.execute("SELECT generated_prompt FROM prompts WHERE id = %s", (test_id,))
        res = cursor.fetchone()
        
        # Test delete to keep DB clean
        cursor.execute("DELETE FROM prompts WHERE id = %s", (test_id,))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        if res and res[0] == "Dry run diagnostic compiled prompt.":
            print("✅ SUCCESS: Read/Write transactions validated successfully!")
        else:
            print("❌ FAILED: Database accepted queries, but returned corrupted test states.")
            return False
    except Exception as e:
        print(f"❌ FAILED: SQL transaction failed: {str(e)}")
        return False

    print("=" * 60)
    print("🎉 ALL SYSTEMS GO: Your local MySQL database is 100% ready for Saturday launch!")
    print("=" * 60)
    return True

if __name__ == "__main__":
    run_diagnostics()
