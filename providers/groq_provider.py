import logging
import requests
from typing import Dict, Any, Optional
from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class GroqProvider:
    """Provider for Groq API integration."""
    
    def __init__(self, container):
        self.container = container
        self.api_key = container.get_config("groq_api_key")
        self.base_url = container.get_config("groq_base_url", "https://api.groq.com/openai/v1")
        
    def generate_text(self, prompt: str, model: str = "llama3-8b-8192", max_tokens: int = 1024, temperature: float = 0.7) -> ServiceResult:
        if not self.api_key:
            return ServiceResult.fail(ForgeError("Groq API key not configured."))
            
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature
        }
        
        try:
            response = requests.post(f"{self.base_url}/chat/completions", headers=headers, json=payload, timeout=30)
            if response.status_code != 200:
                logger.error(f"Groq API error: {response.text}")
                return ServiceResult.fail(ForgeError(f"Groq API returned status {response.status_code}: {response.text}"))
                
            data = response.json()
            message = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            usage = data.get("usage", {})
            return ServiceResult.success({
                "text": message,
                "usage": usage,
                "model": model
            })
        except Exception as e:
            logger.error(f"Groq provider request failed: {e}")
            return ServiceResult.fail(ForgeError(f"Groq provider exception: {e}"))
