import logging
from typing import Any, Dict, List
from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class ToolRouterService:
    """
    Dynamically selects tools (File Ops, Python, Git, OCR) that best match the prompt domain.
    """
    def __init__(self, container: Any):
        self.storage = container.get('StorageProvider')
        
        self.available_tools = [
            {
                "tool_id": "file_ops_toolkit",
                "name": "File Operations",
                "description": "Tools for reading, writing, and managing local files.",
                "keywords": ["file", "read", "write", "directory", "folder", "save", "load", "system", "path"],
                "domain": "filesystem"
            },
            {
                "tool_id": "python_executor",
                "name": "Python Sandbox Exec",
                "description": "Execute Python code dynamically in a sandbox.",
                "keywords": ["python", "script", "execute", "run code", "code execution", "compute", "calculate"],
                "domain": "execution"
            },
            {
                "tool_id": "git_toolkit",
                "name": "Git Operations",
                "description": "Perform git commits, diffs, branches, and version control tasks.",
                "keywords": ["git", "commit", "branch", "repo", "repository", "diff", "merge", "push", "pull", "version"],
                "domain": "vcs"
            },
            {
                "tool_id": "ocr_toolkit",
                "name": "OCR Engine",
                "description": "Extract text from images or scanned documents.",
                "keywords": ["image", "pdf", "scan", "ocr", "extract text", "vision", "picture"],
                "domain": "vision"
            },
            {
                "tool_id": "web_search",
                "name": "Web Search API",
                "description": "Search the internet for up-to-date information.",
                "keywords": ["search", "web", "internet", "lookup", "online", "find"],
                "domain": "web"
            }
        ]

    def _calculate_relevance(self, tool: Dict[str, Any], prompt_text: str, domain_keywords: List[str]) -> float:
        score = 0.0
        prompt_lower = prompt_text.lower()
        
        for kw in domain_keywords:
            if kw.lower() in tool["domain"] or kw.lower() in tool["keywords"]:
                score += 2.0
                
        for kw in tool["keywords"]:
            if kw in prompt_lower:
                score += 1.0
                
        return score

    def route_tools(self, prompt_text: str, domain_keywords: List[str], threshold: float = 1.0) -> ServiceResult[List[Dict[str, Any]]]:
        """
        Analyzes the prompt text and domain keywords to dynamically select relevant tools.
        """
        try:
            selected_tools = []
            
            for tool in self.available_tools:
                score = self._calculate_relevance(tool, prompt_text, domain_keywords)
                if score >= threshold:
                    tool_data = dict(tool)
                    tool_data["relevance_score"] = score
                    selected_tools.append(tool_data)
                    
            selected_tools.sort(key=lambda x: x["relevance_score"], reverse=True)
            
            if not selected_tools:
                logger.info("No specific tools crossed the relevance threshold.")
                
            return ServiceResult.success(selected_tools)
        except Exception as e:
            logger.error(f"Tool routing failed: {e}", exc_info=True)
            return ServiceResult.fail(ForgeError(code="TOOL_ROUTING_ERROR", message=f"Failed to route tools: {str(e)}"))
