from services.plugin_sdk import BaseNodePlugin, NodeExecutionContext

class JiraPlugin(BaseNodePlugin):
    api_version = "v1"
    plugin_version = "1.0.0"

    def execute(self, ctx: NodeExecutionContext):
        action = self.config.get("action", "create_ticket")
        summary = ctx.variables.get("summary", "New Ticket")
        ctx.logger(f"[JiraPlugin] Executing action '{action}' for summary: {summary}")
        
        return {
            "success": True,
            "ticket_key": "PROJ-101",
            "status": "Created"
        }
