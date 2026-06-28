from models import get_db_connection
import json

class MCPService:
    @staticmethod
    def list_tools(server_id):
        """
        Mock lists tools for a registered MCP server. In production, this connects via stdio/SSE,
        sends a `tools/list` JSON-RPC message, and caches the schemas.
        """
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("SELECT name, command FROM mcp_servers WHERE id = %s", (server_id,))
            server = cursor.fetchone()
            if not server:
                return []
            
            # Simple simulation/caching of schemas based on common servers
            if "postgres" in server["name"].lower() or "sql" in server["name"].lower():
                return [
                    {
                        "name": "query_db",
                        "description": "Execute a safe SELECT query on the target PostgreSQL database.",
                        "input_schema": json.dumps({
                            "type": "object",
                            "properties": {
                                "query": {"type": "string", "description": "SQL query to execute."}
                            },
                            "required": ["query"]
                        })
                    }
                ]
            else:
                return [
                    {
                        "name": "fetch_web_page",
                        "description": "Fetches and extracts Markdown content from a target URL.",
                        "input_schema": json.dumps({
                            "type": "object",
                            "properties": {
                                "url": {"type": "string", "description": "URL to scrape."}
                            },
                            "required": ["url"]
                        })
                    }
                ]
        except Exception as e:
            print(f"[MCPService Error] {e}")
            return []
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def execute_tool(server_id, tool_name, arguments):
        """
        Simulates executing an MCP tool. In production, spawns subprocess (for stdio) or sends HTTP POST (for SSE).
        """
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("SELECT name FROM mcp_servers WHERE id = %s", (server_id,))
            server = cursor.fetchone()
            if not server:
                return {"success": False, "error": "Server not found"}

            print(f"[MCPService] Executing tool {tool_name} on server {server['name']} with args: {arguments}")
            
            # Simulation of tool behaviors
            if tool_name == "query_db":
                return {
                    "success": True,
                    "rows": [
                        {"id": 1, "username": "alpha_tester", "email": "alpha@example.com"},
                        {"id": 2, "username": "beta_tester", "email": "beta@example.com"}
                    ]
                }
            elif tool_name == "fetch_web_page":
                return {
                    "success": True,
                    "markdown": f"# Content scraped from {arguments.get('url', 'URL')}\n\nThis is a mocked scraping response from the MCP server."
                }
            else:
                return {
                    "success": False,
                    "error": f"Tool '{tool_name}' not implemented in mock MCP runner."
                }
        except Exception as e:
            print(f"[MCPService Execution Error] {e}")
            return {"success": False, "error": str(e)}
        finally:
            cursor.close()
            conn.close()
