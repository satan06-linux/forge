import os
import requests
import json
from providers.base_provider import BaseLLMProvider
from services.config_service import ConfigService

class OpenAIProvider(BaseLLMProvider):
    def call_llm(self, model_name, prompt, system_prompt=None, max_tokens=1000, temperature=0.7):
        api_key = self.api_key or ConfigService.get_openai_api_key()
        if not api_key:
            raise ValueError("OpenAI API key not configured")
            
        endpoint = self.endpoint or "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        data = {
            "model": model_name,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature
        }

        res = requests.post(endpoint, headers=headers, json=data, timeout=30)
        if res.status_code != 200:
            raise Exception(f"OpenAI API error: {res.status_code} - {res.text}")

        res_json = res.json()
        text = res_json["choices"][0]["message"]["content"]
        
        usage = res_json.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", len(prompt) // 4)
        completion_tokens = usage.get("completion_tokens", len(text) // 4)
        total_tokens = prompt_tokens + completion_tokens

        # Cost estimation: default to gpt-4o rates ($5/M input, $15/M output) or gpt-4o-mini ($0.150/M input, $0.600/M output)
        if "mini" in model_name:
            cost = (prompt_tokens * 0.150 + completion_tokens * 0.600) / 1000000.0
        else:
            cost = (prompt_tokens * 5.0 + completion_tokens * 15.0) / 1000000.0

        return {
            "text": text,
            "tokens": total_tokens,
            "cost": cost
        }

    def call_llm_stream(self, model_name, prompt, system_prompt=None, max_tokens=1000, temperature=0.7):
        api_key = self.api_key or ConfigService.get_openai_api_key()
        if not api_key:
            raise ValueError("OpenAI API key not configured")
            
        endpoint = self.endpoint or "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        data = {
            "model": model_name,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True
        }

        res = requests.post(endpoint, headers=headers, json=data, stream=True, timeout=30)
        if res.status_code != 200:
            raise Exception(f"OpenAI Stream API error: {res.status_code} - {res.text}")

        for line in res.iter_lines():
            if not line:
                continue
            decoded_line = line.decode('utf-8').strip()
            if decoded_line.startswith("data: "):
                data_str = decoded_line[6:]
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                    delta = chunk["choices"][0]["delta"].get("content", "")
                    if delta:
                        yield delta
                except Exception:
                    pass
