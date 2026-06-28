import os
import sys
import json
import torch
import builtins
import pathlib

# Global Windows UTF-8 Monkeypatch to prevent TRL / Pathlib read_text crashes
real_open = builtins.open
def utf8_open(*args, **kwargs):
    # Detect if opened in binary mode
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

# Inject FP8BackendType into builtins to prevent Unsloth dynamically compiled scoping NameErrors
try:
    from accelerate.utils.dataclasses import FP8BackendType
    builtins.FP8BackendType = FP8BackendType
except ImportError:
    try:
        from accelerate.utils import FP8BackendType
        builtins.FP8BackendType = FP8BackendType
    except ImportError:
        pass

# Create a mock xformers module to bypass Unsloth's hardcoded xformers.attn_bias references
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

from unsloth import FastLanguageModel

# Inject our MockMask into unsloth module scopes directly
import unsloth.models.llama as uml
import unsloth.models.mistral as umm

for module in [uml, umm]:
    module.xformers = mock_xformers
    module.BlockDiagonalCausalMask = MockMask
    module.HAS_XFORMERS = True
from datasets import Dataset
from trl import SFTTrainer
from transformers import TrainingArguments

if __name__ == '__main__':
    # Load base model
    print("🔥 Loading Phi-3-mini-4k-instruct base model in 4-bit...")
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name="microsoft/Phi-3-mini-4k-instruct",
        max_seq_length=2048,
        dtype=torch.float16,
        load_in_4bit=True,
    )

    # LoRA config
    model = FastLanguageModel.get_peft_model(
        model,
        r=16,
        target_modules=["q_proj", "k_proj", "v_proj", 
                        "o_proj", "gate_proj", "up_proj", "down_proj"],
        lora_alpha=16,
        lora_dropout=0,
        bias="none",
        use_gradient_checkpointing=True,
        random_state=42,
    )

    # Load dataset
    dataset_path = os.path.join(os.path.dirname(__file__), "prometheus_dataset_v1.json")
    if not os.path.exists(dataset_path):
        # Fallback to general dataset.json if prometheus_dataset_v1.json hasn't been generated yet
        dataset_path = os.path.join(os.path.dirname(__file__), "dataset.json")

    with open(dataset_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    def format_example(example):
        # If the dataset is in the prompt evaluation style, format it appropriately
        if "instruction" in example:
            return {
                "text": f"""<|system|>
You are Prometheus AI by Nexafian acting as a fair evaluator.
<|end|>
<|user|>
Evaluate: {example['instruction']}
Rubric: {example['rubric']}
Response: {example['response']}
Reference: {example['reference_answer']}
<|end|>
<|assistant|>
[Feedback] {example['feedback']}
[Score] {example['score']}
<|end|>"""
            }
        
        # Otherwise, use the MCQ prompt builder style
        mcq_text = "\n".join([
            f"Q: {q['question']}\nA: {q['answer']}"
            for q in example.get("mcqs", [])
        ])
        return {
            "text": f"""<|system|>
You are Prometheus AI by Nexafian. You are a specialized 
prompt engineering model. Given a user concept and their 
MCQ answers, generate a precise, professional, ready-to-use 
AI prompt. Output only the final prompt, nothing else.
<|end|>
<|user|>
Concept: {example.get('input', '')}
Category: {example.get('category', '')}
User answers:
{mcq_text}
<|end|>
<|assistant|>
{example.get('output', '')}
<|end|>"""
        }

    formatted = [format_example(e) for e in data]
    dataset = Dataset.from_list(formatted)

    output_dir = os.path.join(os.path.dirname(__file__), "model_weights")

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        dataset_text_field="text",
        max_seq_length=2048,
        dataset_num_proc=2,
        args=TrainingArguments(
            per_device_train_batch_size=2,
            gradient_accumulation_steps=4,
            warmup_steps=20,
            num_train_epochs=8,
            learning_rate=2e-4,
            fp16=True,
            logging_steps=10,
            optim="adamw_8bit",
            weight_decay=0.01,
            lr_scheduler_type="cosine",
            output_dir=output_dir,
            save_strategy="epoch",
            report_to="none",
        ),
    )

    print("Training Prometheus-1 (Phi-3-mini)... this will take 2-4 hours on RTX 4050")
    trainer.train()

    # CRITICAL FIX FOR WINDOWS BSOD / CRASHING
    # The laptop crashes here because saving the model spikes the System RAM when the Optimizer is still loaded.
    # We must explicitly delete the trainer and clear the GPU/RAM cache before saving!
    print("🧹 Clearing Optimizer Memory from System RAM to prevent Windows Crash...")
    import gc
    del trainer
    torch.cuda.empty_cache()
    gc.collect()

    model.save_pretrained(output_dir)
    tokenizer.save_pretrained(output_dir)
    print(f"🎉 Prometheus-1 saved to: {output_dir}")
