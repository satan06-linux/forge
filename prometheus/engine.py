import os
import torch
import threading
import json
import re
import builtins

# Inject FP8BackendType into builtins to prevent Unsloth NameErrors during generation
try:
    from accelerate.utils.dataclasses import FP8BackendType
    builtins.FP8BackendType = FP8BackendType
except ImportError:
    try:
        from accelerate.utils import FP8BackendType
        builtins.FP8BackendType = FP8BackendType
    except ImportError:
        pass

class PrometheusEngine:
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def initialize(self, model_path=None):
        if self._initialized:
            return
            
        try:
            from unsloth import FastLanguageModel
        except ImportError:
            print("⚠️ WARNING: 'unsloth' package is not installed in the current environment.")
            print("👉 Local Prometheus-1 fallback model will not be active on startup.")
            print("👉 Execute 'pip install -r requirements.txt' inside your target environment to enable local fallback.")
            return
        
        if model_path is None:
            # Default to the local trained folder
            local_weights = os.path.join(os.path.dirname(__file__), "model_weights")
            if os.path.exists(local_weights) and os.path.exists(os.path.join(local_weights, "adapter_config.json")):
                model_path = local_weights
            else:
                model_path = "microsoft/Phi-3-mini-4k-instruct"
                
        print(f"⚡ Loading Prometheus-1 local model from {model_path}...")
        self.model, self.tokenizer = FastLanguageModel.from_pretrained(
            model_name=model_path,
            max_seq_length=1024,
            dtype=torch.float16,
            load_in_4bit=True,
        )
        FastLanguageModel.for_inference(self.model)
        self._initialized = True
        print("✅ Prometheus-1 ready — RTX 4050 active")
    
    def generate_mcqs(self, user_input: str) -> list:
        if not self._initialized:
            return self._fallback_mcqs(user_input)
            
        prompt = f"""<|system|>
You are Prometheus AI by Nexafian. You are a specialized prompt engineering model.
Given a user concept, generate exactly 3 smart, specific, contextual multiple-choice questions to refine their idea.
Return ONLY a valid JSON array matching this exact format, with no markdown code blocks, no intros/outros:
[
  {{"id": 1, "question": "Question text here?", "options": ["Option 1", "Option 2", "Option 3", "Option 4"]}}
]
<|end|>
<|user|>
Concept: {user_input}
<|end|>
<|assistant|>"""
        
        inputs = self.tokenizer(
            prompt, 
            return_tensors="pt",
            truncation=True,
            max_length=512
        ).to("cuda")
        
        with torch.no_grad():
            outputs = self.model.generate(
                **inputs,
                max_new_tokens=300,
                temperature=0.3,
                do_sample=False,
                pad_token_id=self.tokenizer.eos_token_id,
            )
        
        response = self.tokenizer.decode(
            outputs[0][inputs['input_ids'].shape[1]:], 
            skip_special_tokens=True
        )
        
        try:
            json_match = re.search(r'\[.*\]', response, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group())
                if isinstance(parsed, list) and len(parsed) > 0 and isinstance(parsed[0], dict) and 'question' in parsed[0] and 'options' in parsed[0]:
                    return parsed
        except:
            pass
        return self._fallback_mcqs(user_input)
    
    def generate_prompt(self, user_input: str, 
                        category: str, mcq_answers: dict) -> str:
        if not self._initialized:
            # Fallback static prompt generation
            fallback_text = f"A high-quality custom engineered specification prompt based on concept: '{user_input}'.\n"
            fallback_text += f"- Category Focus: {category}\n"
            if isinstance(mcq_answers, dict):
                for q, ans in mcq_answers.items():
                    fallback_text += f"- {q}: {ans}\n"
            return fallback_text
            
        # Support both list and dict formats for answers
        if isinstance(mcq_answers, dict):
            mcq_text = "\n".join([
                f"Q: {q}\nA: {ans}" 
                for q, ans in mcq_answers.items()
            ])
        else:
            mcq_text = "\n".join([
                f"Q: {a.get('question', '')}\nA: {a.get('answer', '')}" 
                for a in mcq_answers
            ])
        
        prompt = f"""<|system|>
You are Prometheus AI by Nexafian. You are a specialized 
prompt engineering model. Given a user concept and their 
MCQ answers, generate a precise, professional, ready-to-use 
AI prompt. Output only the final prompt, nothing else.
<|end|>
<|user|>
Concept: {user_input}
Category: {category}
User answers:
{mcq_text}
<|end|>
<|assistant|>"""
        
        inputs = self.tokenizer(
            prompt,
            return_tensors="pt",
            truncation=True,
            max_length=768
        ).to("cuda")
        
        with torch.no_grad():
            outputs = self.model.generate(
                **inputs,
                max_new_tokens=500,
                temperature=0.4,
                do_sample=True,
                top_p=0.9,
                no_repeat_ngram_size=4,
                pad_token_id=self.tokenizer.eos_token_id,
            )
        
        return self.tokenizer.decode(
            outputs[0][inputs['input_ids'].shape[1]:],
            skip_special_tokens=True
        ).strip()
    
    def _fallback_mcqs(self, user_input: str) -> list:
        # Emergency hardcoded MCQs if JSON parsing fails
        return [
            {
                "id": 1,
                "question": "What is the primary goal?",
                "options": ["Visual/Creative", "Functional/Technical", 
                           "Business/Commercial", "Educational/Informational"]
            },
            {
                "id": 2,
                "question": "What level of detail do you need?",
                "options": ["Quick overview", "Standard detail", 
                           "Highly detailed", "Production-ready spec"]
            },
            {
                "id": 3,
                "question": "Who is the target audience?",
                "options": ["General public", "Technical experts",
                           "Business professionals", "Creative community"]
            }
        ]

# Singleton instance
prometheus_engine = PrometheusEngine()
