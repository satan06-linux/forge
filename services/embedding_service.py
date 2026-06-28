from abc import ABC, abstractmethod
import os
import requests
import math
from services.config_service import ConfigService

class EmbeddingProvider(ABC):
    @abstractmethod
    def get_embedding(self, text):
        pass

class OpenAIEmbedding(EmbeddingProvider):
    def __init__(self, api_key=None):
        self.api_key = api_key or ConfigService.get_openai_api_key()

    def get_embedding(self, text):
        if not self.api_key:
            raise ValueError("OpenAI API key missing")
        url = "https://api.openai.com/v1/embeddings"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        data = {
            "model": "text-embedding-3-small",
            "input": text
        }
        res = requests.post(url, headers=headers, json=data, timeout=30)
        if res.status_code != 200:
            raise Exception(f"OpenAI Embedding API error: {res.status_code} - {res.text}")
        return res.json()["data"][0]["embedding"]

class LocalHashEmbedding(EmbeddingProvider):
    """
    Pure-Python character-hash embedding generator returning 384-dimensional normalized vectors.
    Provides a fast, zero-dependency, local fallback.
    """
    def get_embedding(self, text):
        dimensions = 384
        vector = [0.0] * dimensions
        
        if not text:
            vector[0] = 1.0
            return vector
            
        words = text.lower().split()
        for i, word in enumerate(words):
            word_hash = hash(word)
            for offset in range(3):
                dim_idx = abs((word_hash + offset) * (i + 1)) % dimensions
                vector[dim_idx] += 1.0
                
        magnitude = math.sqrt(sum(v * v for v in vector))
        if magnitude > 0:
            vector = [v / magnitude for v in vector]
        else:
            vector[0] = 1.0
        return vector

class EmbeddingService:
    @staticmethod
    def get_provider(provider_name="local", api_key=None):
        if provider_name == "openai":
            return OpenAIEmbedding(api_key=api_key)
        else:
            return LocalHashEmbedding()

    @staticmethod
    def embed(text, provider_name="local", api_key=None):
        return EmbeddingService.get_provider(provider_name, api_key).get_embedding(text)
