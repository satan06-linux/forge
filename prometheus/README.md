# Prometheus AI Fine-Tuning & Routing Pipeline 🧠🚀

Welcome to **Prometheus**, the custom prompt-engineering intelligence engine of the **ForgePrompt** platform. This directory contains the complete pipeline for dataset generation, LoRA fine-tuning, rubric-based evaluation, and multi-tier cascade routing.

---

## 📖 Table of Contents
1. [Project Overview & Objectives](#-project-overview--objectives)
2. [Dataset Engineering & QA Critique](#-dataset-engineering--qa-critique)
3. [Fine-Tuning Pipeline (Phi-3-Mini QLoRA)](#-fine-tuning-pipeline-phi-3-mini-qlora)
4. [Dual-Key Cascade & Refinement Architecture](#-dual-key-cascade--refinement-architecture)
5. [How to Run (Step-by-Step)](#-how-to-run-step-by-step)
6. [Future Production Optimization (GGUF & Ollama)](#-future-production-optimization-gguf--ollama)

---

## 🎯 Project Overview & Objectives

Prometheus is designed to be a specialized **planning and prompt-generation layer** rather than a raw code writer. Given a user's raw concept (e.g., "Build a finance app") and their answers to multiple-choice questions (MCQs), Prometheus generates a structured, production-grade **engineering specification prompt**.

### Standard Prompt Format
Prometheus enforces a highly structured Markdown schema:
```markdown
# Project Goal
# Project Type
# Functional Requirements
# Core Features
# Tech Stack
# Constraints
# Optional Enhancements
```

### Key Engineering Goals
- **Strict Anti-Hallucination**: The model must never invent libraries, databases, or cloud hosts unless they were selected in the MCQs or explicitly requested.
- **Varying Layout styles**: Supports Short (~120w), Medium (~250w), and Detailed (~450w) specifications dynamically.
- **Fault-Tolerant Execution**: Employs a zero-downtime cascade when cloud APIs are rate-limited or offline.

---

## 🛠️ Dataset Engineering & QA Critique

A primary bottleneck of fine-tuning small LLMs is **dataset scale and quality**. To solve this, we built a self-validating dataset pipeline.

### Dynamic Generation with OpenRouter
Because Groq has strict daily token limits (100k TPD) on its free tier, the dataset generator runs on **`google/gemini-2.5-flash`** via OpenRouter. This bypasses rate limits and costs fractions of a penny.
- **Topics List**: Pre-screened 50 topics across 9 domains (WebDev, Chatbots, Agents, Mobile, Databases, DevOps, APIs, Desktop, and Automation).
- **Variations**: The generator generates **3 distinct variations** of prompts per topic, yielding a diverse training set of **150 validated examples** in `prometheus_dataset_v1.json`.

### Self-Review Quality Gate
Every generated training example is evaluated by a critic LLM using a rubric:
1. **Semantic Match**: Verifies MCQ options match the output prompt (wording mismatches are allowed semantically).
2. **Framework Isolation**: Blocks unselected core frameworks (forces them into Optional Enhancements).
3. **Template Formatting**: Enforces correct markdown ordering.

---

## 🧬 Fine-Tuning Pipeline (Phi-3-Mini QLoRA)

Prometheus is trained on **Microsoft's Phi-3-Mini-4K-Instruct** base model using **Unsloth** for 2x faster execution.

### Training Strategy
- **QLoRA (4-bit)**: Leverages Parameter-Efficient Fine-Tuning (PEFT) targeting the QKV projections and MLP layers.
- **Safeguarding VRAM / Avoiding Windows BSODs**: Standard HuggingFace `SFTTrainer` holds references to the optimizer states during adapter saving, causing memory spikes and Windows crashes on low-spec configurations. Our pipeline explicitly deletes the trainer, flushes CUDA caches, and calls the Python garbage collector (`gc.collect()`) before writing LoRA adapters to `prometheus/model_weights`.
- **Convergence Telemetry**: Runs for **8 epochs**, successfully converging from an initial loss of **2.84** down to **1.07** (final average loss **1.75**).

---

## 🛡️ Dual-Key Cascade & Refinement Architecture

We refactored the platform's core cascade router [llm_service.py](file:///D:/Nexabuild/forge/services/llm_service.py) to support high-availability operations:

### 1. Generation Cascade
When generating a prompt:
- **Primary Groq Key**: Attempt Llama-3.3-70b-versatile first.
- **OpenRouter Key**: If primary Groq key is rate-limited or exhausted, fall back to OpenRouter.
- **Local Prometheus (GPU)**: If OpenRouter fails, load and run local Prometheus-1 weights natively.

### 2. Refinement Cascade (Dynamic Critique)
Because local Phi-3 (3.8B) prompts might miss fine-grained requirements, any local prompt is immediately passed to a refinement loop:
- **Secondary Groq Key** (`GROQ_API_KEY_2`): Runs Llama-3.3-70b to expand, audit, and clean the local prompt. This key must be on a **separate Groq account** to ensure independent rate limits.
- **OpenRouter**: Fallback refiner.
- **Fail-Safe Bypass**: If all cloud endpoints are dead or offline, the system returns the raw local Prometheus prompt directly, guaranteeing zero runtime crashes.

---

## 🚀 How to Run (Step-by-Step)

### 1. Setup Environment Variables
Create or modify your `.env` file in the project root:
```env
# Primary Groq Key
GROQ_API_KEY=gsk_primary_key_here

# Secondary Groq Key (Separate Account)
GROQ_API_KEY_2=gsk_secondary_key_here

# OpenRouter Key
OPEN_ROUTER_API_KEY=sk-or-v1-key_here
```

### 2. Run Dataset Generator
Generate 150 validated training variations:
```powershell
python prometheus/generate_dataset_openrouter.py
```

### 3. Run Fine-Tuning
Execute the LoRA training loop:
```powershell
python prometheus/train.py
```

### 4. Run Rubric Evaluation
Evaluate the model against test cases and print a multi-metric scorecard (Intent capture, constraint inclusion, completeness):
```powershell
python prometheus/evaluate.py
```

### 5. Run Cascade Refinement Test
Test local Prometheus generation combined with secondary key refinement:
```powershell
python prometheus/evaluate_groq.py
```

---

## ⚡ Future Production Optimization (GGUF & Ollama)

Before final production launch, python/PyTorch inference should be replaced by native C++ execution to eliminate CUDA latency overhead:

1. **Export model to GGUF**:
   ```python
   from unsloth import FastLanguageModel
   model, tokenizer = FastLanguageModel.from_pretrained("prometheus/model_weights", load_in_4bit=True)
   model.save_pretrained_gguf("prometheus/prometheus_gguf", tokenizer, quantization_method="q4_k_m")
   ```
2. **Register model in Ollama**:
   Create a `Modelfile`:
   ```dockerfile
   FROM ./prometheus/prometheus_gguf/unsloth.Q4_K_M.gguf
   TEMPLATE "<|system|>\n{{ .System }}\n<|end|>\n<|user|>\n{{ .Prompt }}\n<|end|>\n<|assistant|>\n"
   ```
   Run:
   ```powershell
   ollama create prometheus -f Modelfile
   ```
3. **Execute**:
   Ollama will serve the model natively at **50+ tokens per second**, bringing generation latency down to **under 1.5 seconds**.
