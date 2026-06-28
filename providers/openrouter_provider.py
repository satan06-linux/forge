import logging
import requests
from typing import Dict, Any, Optional
from services.service_result import ServiceResult
from services.errors import ForgeError

logger = logging.getLogger(__name__)

class OpenRouterProvider:
    """Provider for OpenRouter API integration."""
    
    def __init__(self, container):
        self.container = container
        self.api_key = container.get_config("openrouter_api_key")
        self.base_url = container.get_config("openrouter_base_url", "https://openrouter.ai/api/v1")
        self.app_url = container.get_config("app_url", "https://github.com/nexabuild/forge")
        self.app_name = container.get_config("app_name", "ForgePrompt")
        
    def generate_text(self, prompt: str, model: str = "meta-llama/llama-3-8b-instruct", max_tokens: int = 1024, temperature: float = 0.7) -> ServiceResult:
        if not self.api_key:
            return ServiceResult.fail(ForgeError("OpenRouter API key not configured."))
            
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": self.app_url,
            "X-Title": self.app_name,
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature
        }
        
        try:
            response = requests.post(f"{self.base_url}/chat/completions", headers=headers, json=payload, timeout=60)
            if response.status_code != 200:
                logger.error(f"OpenRouter API error: {response.text}")
                return ServiceResult.fail(ForgeError(f"OpenRouter API returned status {response.status_code}: {response.text}"))
                
            data = response.json()
            message = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            usage = data.get("usage", {})
            return ServiceResult.success({
                "text": message,
                "usage": usage,
                "model": model
            })
        except Exception as e:
            logger.error(f"OpenRouter provider request failed: {e}")
            return ServiceResult.fail(ForgeError(f"OpenRouter provider exception: {e}"))
