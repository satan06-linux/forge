import os
import sys
import torch
import builtins
import pathlib
import json
import random

# Global Windows UTF-8 Monkeypatch to prevent tokenizer / Pathlib crashes
real_open = builtins.open
def utf8_open(*args, **kwargs):
    mode = kwargs.get('mode', '')
    if len(args) > 1:
        mode = args[1]
    is_binary = 'b' in mode if isinstance(mode, str) else False
    if not is_binary and 'encoding' not in kwargs:
        kwargs['encoding'] = 'utf-8'
    return real_open(*args, **kwargs)
builtins.open = utf8_open

real_read_text = pathlib.Path.read_text
def utf8_read_text(self, encoding=None, errors=None):
    return real_read_text(self, encoding=encoding or 'utf-8', errors=errors)
pathlib.Path.read_text = utf8_read_text

real_path_open = pathlib.Path.open
def utf8_path_open(self, mode='r', buffering=-1, encoding=None, errors=None, newline=None):
    if 'r' in mode and 'b' not in mode and encoding is None:
        encoding = 'utf-8'
    return real_path_open(self, mode=mode, buffering=buffering, encoding=encoding, errors=errors, newline=newline)
pathlib.Path.open = utf8_path_open

# Force UTF-8 encoding for Windows terminal prints
sys.stdout.reconfigure(encoding='utf-8') if hasattr(sys.stdout, 'reconfigure') else None

# Mock xformers monkeypatch
from types import ModuleType
import importlib.machinery
mock_xformers = ModuleType("xformers")
mock_spec = importlib.machinery.ModuleSpec("xformers", None, is_package=True)
mock_spec.submodule_search_locations = ["C:\\fake\\path"]
mock_xformers.__spec__ = mock_spec
mock_ops = ModuleType("xformers.ops")
mock_fmha = ModuleType("xformers.ops.fmha")
mock_attn_bias = ModuleType("xformers.attn_bias")

class MockMask:
    def __init__(self, *args, **kwargs):
        pass

mock_attn_bias.LowerTriangularMask = MockMask
mock_attn_bias.BlockDiagonalCausalMask = MockMask
mock_fmha.attn_bias = mock_attn_bias
mock_ops.fmha = mock_fmha
mock_xformers.ops = mock_ops
mock_xformers.attn_bias = mock_attn_bias

sys.modules["xformers"] = mock_xformers
sys.modules["xformers.ops"] = mock_ops
sys.modules["xformers.ops.fmha"] = mock_fmha
sys.modules["xformers.attn_bias"] = mock_attn_bias

import importlib.metadata
import importlib.util

real_version = importlib.metadata.version
def fake_version(pkg_name):
    if pkg_name == "xformers": return "0.0.22"
    return real_version(pkg_name)
importlib.metadata.version = fake_version

real_find_spec = importlib.util.find_spec
def fake_find_spec(name, package=None):
    if name == "xformers":
        return mock_spec
    return real_find_spec(name, package)
importlib.util.find_spec = fake_find_spec

# Inject FP8BackendType into builtins to prevent Unsloth NameErrors
try:
    from accelerate.utils.dataclasses import FP8BackendType
    builtins.FP8BackendType = FP8BackendType
except ImportError:
    try:
        from accelerate.utils import FP8BackendType
        builtins.FP8BackendType = FP8BackendType
    except ImportError:
        pass

def print_header(title):
    print("=" * 65)
    print(f" 🧠 {title.upper()}")
    print("=" * 65)

print_header("PROMETHEUS EVALUATION INFERENCE")

try:
    from unsloth import FastLanguageModel
except ImportError:
    print("❌ ERROR: Missing unsloth! Please install it.")
    sys.exit(1)

adapter_path = os.path.join(os.path.dirname(__file__), "model_weights")

if not os.path.exists(adapter_path) or not os.path.exists(os.path.join(adapter_path, "adapter_config.json")):
    print("💡 No local weights folder found at model_weights. Using base model...")
    model_name = "microsoft/Phi-3-mini-4k-instruct"
else:
    print("💡 Found fine-tuned weights. Loading model with local adapters...")
    model_name = adapter_path

try:
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=model_name,
        max_seq_length=2048,
        dtype=torch.float16,
        load_in_4bit=True,
    )
    FastLanguageModel.for_inference(model)
    print("✅ SUCCESS: Prometheus model is fully loaded in VRAM and active!\n")
except Exception as e:
    print(f"❌ ERROR loading model: {str(e)}")
    sys.exit(1)

# Prometheus evaluation structure template
prometheus_template = """<|system|>
You are Prometheus AI by Nexafian. You are a specialized 
prompt engineering model. Given a user concept and their 
MCQ answers, generate a precise, professional, ready-to-use 
AI prompt. Output only the final prompt, nothing else.
<|end|>
<|user|>
Concept: {instruction}
Category: {category}
User answers:
{rubric}
<|end|>
<|assistant|>
"""

def evaluate_prompt(instruction, category, rubric):
    formatted_input = prometheus_template.format(
        instruction=instruction,
        category=category,
        rubric=rubric
    )
    
    inputs = tokenizer(formatted_input, return_tensors="pt").to("cuda")
    
    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=500,
            temperature=0.3,
            use_cache=True,
            do_sample=True,
            eos_token_id=tokenizer.eos_token_id
        )
        
    generated_text = tokenizer.decode(outputs[0][inputs.input_ids.shape[1]:], skip_special_tokens=True)
    return generated_text.strip()

def grade_prompt(mcq_text, generated, target):
    system_prompt = """You are an expert prompt engineer and quality auditor.
Evaluate the candidate generated prompt against the target expected prompt and user answers.
Return ONLY a valid JSON object with the following keys, no markdown wraps, no extra text:
{
  "intent_capture": 0-5,
  "constraint_inclusion": 0-5,
  "project_type_correctness": 0-5,
  "missing_features": 0-5,
  "completeness": 0-100,
  "semantic_similarity": 0-100,
  "rationale": "brief sentence explaining the scores"
}
"""
    user_prompt = f"""User MCQ Answers:
{mcq_text}

Candidate Generated Prompt:
{generated}

Target Expected Prompt:
{target}
"""
    # Append parent dir to path so we can import config
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import config
    from groq import Groq
    import httpx

    # Wipe proxy variables from environment to bypass httpx client wrapping error
    for key in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
        if key in os.environ:
            del os.environ[key]

    try:
        if not config.GROQ_API_KEY:
            raise ValueError("No Groq key configured")
            
        client = Groq(api_key=config.GROQ_API_KEY, http_client=httpx.Client())
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            max_tokens=300,
            temperature=0.1
        )
        raw_res = response.choices[0].message.content.strip()
        if raw_res.startswith("```json"):
            raw_res = raw_res[7:]
        if raw_res.endswith("```"):
            raw_res = raw_res[:-3]
        return json.loads(raw_res.strip())
    except Exception as e:
        # Fallback heuristic grading
        mcq_lines = mcq_text.strip().split("\n")
        answers = [line.split("A:")[1].strip().lower() for line in mcq_lines if "A:" in line]
        matched = sum(1 for ans in answers if ans in generated.lower())
        match_ratio = (matched / len(answers)) if answers else 1.0
        
        expected_headers = ["# Project Goal", "# Project Type", "# Functional Requirements", "# Core Features", "# Tech Stack"]
        header_matches = sum(1 for h in expected_headers if h in generated)
        header_ratio = header_matches / len(expected_headers)
        
        intent_score = round(match_ratio * 5)
        constraint_score = round(match_ratio * 5)
        project_type_score = 5 if "# Project Type" in generated else 3
        missing_features = 5 - round(header_ratio * 5)
        completeness = round(header_ratio * 100)
        
        len_gen = len(generated.split())
        len_tar = len(target.split())
        similarity = round(min(len_gen, len_tar) / max(len_gen, len_tar, 1) * 100)
        
        return {
            "intent_capture": intent_score,
            "constraint_inclusion": constraint_score,
            "project_type_correctness": project_type_score,
            "missing_features": missing_features,
            "completeness": completeness,
            "semantic_similarity": similarity,
            "rationale": f"Heuristic fallback applied (Groq bypass/fail: {str(e)[:45]})"
        }

if __name__ == "__main__":
    dataset_path = os.path.join(os.path.dirname(__file__), "prometheus_dataset.json")
    if not os.path.exists(dataset_path):
        dataset_path = os.path.join(os.path.dirname(__file__), "dataset.json")
        
    if os.path.exists(dataset_path):
        print(f"📚 Loading evaluation dataset from: {dataset_path}")
        with open(dataset_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        print(f"🔎 Randomly selecting 3 examples to evaluate prompt generation accuracy...")
        sampled = random.sample(data, min(3, len(data)))
        
        results = []
        for i, example in enumerate(sampled):
            print("\n" + "="*50)
            print(f"📊 TEST CASE {i+1}")
            print("="*50)
            print(f"User Concept: {example.get('input', '')}")
            print(f"Category: {example.get('category', '')}")
            
            mcqs = example.get("mcqs", [])
            mcq_text = "\n".join([f"Q: {q['question']}\nA: {q['answer']}" for q in mcqs])
            print(f"User Answers:\n{mcq_text}")
            
            print("\n🤖 Processing prompt generation...")
            generated = evaluate_prompt(example.get('input', ''), example.get('category', ''), mcq_text)
            
            print("\n🤖 GENERATED PROMPT:")
            print(generated)
            print("\n🎯 TARGET EXPECTED PROMPT:")
            print(example.get('output', ''))
            
            print("\n📝 SCORING RUBRIC RESULT:")
            score = grade_prompt(mcq_text, generated, example.get('output', ''))
            results.append(score)
            print(f"  - Intent Capture: {score['intent_capture']}/5")
            print(f"  - Constraint Inclusion: {score['constraint_inclusion']}/5")
            print(f"  - Project Type Correctness: {score['project_type_correctness']}/5")
            print(f"  - Missing Features (lower is better): {score['missing_features']}/5")
            print(f"  - Prompt Completeness: {score['completeness']}%")
            print(f"  - Semantic Similarity: {score['semantic_similarity']}%")
            print(f"  - Rationale: {score['rationale']}")
            print("="*50)
            
        # Calculate summary averages
        if results:
            print("\n==================================================")
            print("📈 EVALUATION SUMMARY CARD")
            print("==================================================")
            avg_intent = sum(r["intent_capture"] for r in results) / len(results)
            avg_constraint = sum(r["constraint_inclusion"] for r in results) / len(results)
            avg_type = sum(r["project_type_correctness"] for r in results) / len(results)
            avg_missing = sum(r["missing_features"] for r in results) / len(results)
            avg_complete = sum(r["completeness"] for r in results) / len(results)
            avg_similarity = sum(r["semantic_similarity"] for r in results) / len(results)
            
            print(f"Average Intent Capture: {avg_intent:.2f}/5")
            print(f"Average Constraint Inclusion: {avg_constraint:.2f}/5")
            print(f"Average Project Type Correctness: {avg_type:.2f}/5")
            print(f"Average Missing Features (lower is better): {avg_missing:.2f}/5")
            print(f"Average Prompt Completeness: {avg_complete:.2f}%")
            print(f"Average Semantic Similarity: {avg_similarity:.2f}%")
            print("==================================================")
    else:
        print("❌ ERROR: Evaluation dataset not found.")
