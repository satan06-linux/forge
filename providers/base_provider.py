from abc import ABC, abstractmethod

class BaseLLMProvider(ABC):
    def __init__(self, api_key=None, endpoint=None):
        self.api_key = api_key
        self.endpoint = endpoint

    @abstractmethod
    def call_llm(self, model_name, prompt, system_prompt=None, max_tokens=1000, temperature=0.7):
        """
        Executes a blocking call to the model and returns a dict with:
        {
           "text": str,
           "tokens": int,
           "cost": float
        }
        """
        pass

    @abstractmethod
    def call_llm_stream(self, model_name, prompt, system_prompt=None, max_tokens=1000, temperature=0.7):
        """
        Executes a streaming call to the model, yielding token strings.
        """
        pass
