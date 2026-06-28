import json
import os
import sys
import time

# Append parent dir to path so we can import config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

from groq import Groq
import httpx

# Wipe proxy variables from environment to bypass httpx client wrapping error
for key in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
    if key in os.environ:
        del os.environ[key]

client = Groq(api_key=config.GROQ_API_KEY, http_client=httpx.Client())

# 50 High-Quality Topics (approx 5-6 per category to prevent Groq Rate limits)
TOPICS = [
    # 1. Web Development (6)
    {"topic": "e-commerce store for handmade jewelry", "category": "webdev"},
    {"topic": "portfolio website for a photographer", "category": "webdev"},
    {"topic": "SaaS dashboard for project management", "category": "webdev"},
    {"topic": "landing page for a fitness app", "category": "webdev"},
    {"topic": "blog website for a travel writer", "category": "webdev"},
    {"topic": "website to sell online courses", "category": "webdev"},
    
    # 2. Chatbots (6)
    {"topic": "customer support bot for an airline", "category": "chatbot"},
    {"topic": "personal finance advisor bot", "category": "chatbot"},
    {"topic": "AI tutor for high school physics students", "category": "chatbot"},
    {"topic": "mental health support chatbot for anxiety relief", "category": "chatbot"},
    {"topic": "sales assistant bot for real estate deals", "category": "chatbot"},
    {"topic": "hotel room service order bot", "category": "chatbot"},
    
    # 3. AI Agents (6)
    {"topic": "autonomous research agent that scrapes papers and writes summaries", "category": "agents"},
    {"topic": "agentic software developer that writes, tests and debugs python", "category": "agents"},
    {"topic": "AI agent for social media content creation and scheduling", "category": "agents"},
    {"topic": "market analysis agent tracking crypto price feeds", "category": "agents"},
    {"topic": "personal calendar scheduling autonomous assistant", "category": "agents"},
    {"topic": "RAG-driven knowledge base search assistant", "category": "agents"},
    
    # 4. Mobile Applications (6)
    {"topic": "cross-platform language learning mobile app", "category": "mobile"},
    {"topic": "native iOS camera filter and editor app", "category": "mobile"},
    {"topic": "budget tracker mobile app with receipt scanner", "category": "mobile"},
    {"topic": "meditation timer mobile app with audio streaming", "category": "mobile"},
    {"topic": "delivery driver route tracking app", "category": "mobile"},
    {"topic": "smart home device controller mobile application", "category": "mobile"},
    
    # 5. Databases (5)
    {"topic": "PostgreSQL relational schema design for HR systems", "category": "databases"},
    {"topic": "MongoDB NoSQL database design for blog platforms", "category": "databases"},
    {"topic": "Redis cache setup for high-traffic session store", "category": "databases"},
    {"topic": "Elasticsearch cluster indexing for ecommerce product catalog", "category": "databases"},
    {"topic": "Neo4j graph database modeling for social connections", "category": "databases"},
    
    # 6. DevOps & Deployment (5)
    {"topic": "Docker containerization setup for Python flask app", "category": "devops"},
    {"topic": "Kubernetes cluster configuration deployment for web apps", "category": "devops"},
    {"topic": "CI/CD pipeline yaml config for GitHub Actions", "category": "devops"},
    {"topic": "AWS EC2 instance provisioning terraform config script", "category": "devops"},
    {"topic": "Nginx reverse proxy and SSL configuration template", "category": "devops"},
    
    # 7. APIs & Integrations (6)
    {"topic": "Stripe payment gateway webhook integrations script", "category": "apis"},
    {"topic": "OAuth2.0 user authentication flow integration", "category": "apis"},
    {"topic": "SendGrid automated transactional email sender", "category": "apis"},
    {"topic": "Twilio SMS notification automated alerts integration", "category": "apis"},
    {"topic": "Salesforce CRM API lead syncing integration program", "category": "apis"},
    {"topic": "Google Sheets API spreadsheet reading program", "category": "apis"},
    
    # 8. Desktop Applications (5)
    {"topic": "cross-platform markdown editor desktop application", "category": "desktop"},
    {"topic": "native Windows system status tray utility dashboard", "category": "desktop"},
    {"topic": "macOS files backup configuration manager app", "category": "desktop"},
    {"topic": "cross-platform media player desktop software", "category": "desktop"},
    {"topic": "sqlite database manager GUI desktop tool", "category": "desktop"},
    
    # 9. Automation Scripts (5)
    {"topic": "Python automated file backup script to AWS S3 bucket", "category": "automation"},
    {"topic": "automated script to clean up temp folders on server hosting", "category": "automation"},
    {"topic": "automated CSV log parser and summary email script", "category": "automation"},
    {"topic": "automated web browser scraper script to download PDFs", "category": "automation"},
    {"topic": "automated system health check and alert sender script", "category": "automation"}
]

SYSTEM_PROMPT = """You are a training data generator for Prometheus AI.
Generate a ForgePrompt training example as a valid JSON object.
Return ONLY the JSON, no explanation, no markdown, no backticks.

JSON format:
{
  "input": "user's raw concept description",
  "category": "webdev|chatbot|agents|mobile|databases|devops|apis|desktop|automation",
  "mcqs": [
    {
      "question": "specific contextual question",
      "options": ["option1", "option2", "option3", "option4"],
      "answer": "chosen option"
    }
  ],
  "output": "the full professional final prompt"
}

Rules for the MCQs:
- Must have exactly 3-4 questions, specific to the topic.
- Include linguistic variety in the question phrasing.

Rules for the 'output' prompt generation (CRITICAL FOR STRUCTURE AND FIDELITY):
1. Must use the following clean markdown structure:
# Project Goal
...
# Project Type
...
# Functional Requirements
...
# Core Features
...
# Tech Stack
...
# Architecture & Deployment (Optional, only show if relevant)
...
# Constraints (Optional, only show if relevant)
...
# Optional Enhancements (Optional, only show if relevant)

2. Strict Anti-Hallucination rule: Do NOT introduce any major framework, library, deployment platform, cloud provider, or database in the prompt unless it was explicitly selected in the MCQ answers, explicitly requested in the input, or placed under the "# Optional Enhancements" section.
3. User-selected MCQ answers always take precedence over inferred improvements.
4. Vary the writing style inside each section (do not use a rigid template) to keep output natural.
5. Generate the prompt with a length style matching one of: Short (~120 words), Medium (~250 words), or Detailed (~450 words). Make this choice randomly.
"""

CRITIQUE_PROMPT = """You are a quality-assurance critic for Prometheus AI.
Analyze the candidate JSON training example against the checklist below.

Checklist:
1. Does the 'output' prompt reflect the concept and MCQ answers accurately?
2. Are there any unselected framework/libraries/databases listed as core requirements? (They MUST only appear in 'Optional Enhancements' if not selected).
3. Does it follow the correct section order?
4. Are optional sections like '# Optional Enhancements' or '# Constraints' missing if empty, rather than printed empty?
5. Is the grammar correct and are there no duplicate sentences?

Lenience Rules:
- Allow semantic matches. For example, if the MCQ answer is 'Both' (for platform) and the output says 'optimized for both mobile and desktop devices', that is a PASS. Do not fail exact wording mismatches if the meaning is fully represented.

If the candidate example passes all checks, output ONLY: PASS
If any check fails, output ONLY: FAIL followed by a brief reason.
"""

dataset = []
failed = []

print(f"🚀 Starting generation of {len(TOPICS)} topics...")

for i, item in enumerate(TOPICS):
    topic = item["topic"]
    category = item["category"]
    print(f"\n[{i+1}/{len(TOPICS)}] Generating: {topic} ({category})")
    
    success = False
    for attempt in range(3):
        try:
            # Rate limit buffer - sleep 4 seconds before call
            time.sleep(4)
            
            # 1. Generate candidate
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"Generate training example for topic: '{topic}' in category '{category}'"}
                ],
                max_tokens=1000,
                temperature=0.8,
            )
            raw = response.choices[0].message.content.strip()
            
            # Clean markdown JSON wraps
            if raw.startswith("```json"):
                raw = raw[7:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()
            
            example = json.loads(raw, strict=False)
            
            # Rate limit buffer - sleep 4 seconds before call
            time.sleep(4)
            
            # 2. Self-Review Step
            critique_response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": CRITIQUE_PROMPT},
                    {"role": "user", "content": f"Analyze this candidate JSON:\n{raw}"}
                ],
                max_tokens=150,
                temperature=0.1,
            )
            critique = critique_response.choices[0].message.content.strip()
            
            if "PASS" in critique.upper():
                dataset.append(example)
                print(f"  ✅ PASS (Attempt {attempt+1}) - category: {example['category']}")
                success = True
                break
            else:
                print(f"  ❌ FAIL (Attempt {attempt+1}) - Reason: {critique}")
                
        except Exception as e:
            print(f"  ⚠️ Error during attempt {attempt+1}: {e}")
            time.sleep(5)
            
    if not success:
        failed.append(topic)

output_file = os.path.join(os.path.dirname(__file__), "prometheus_dataset_v1.json")
with open(output_file, "w", encoding="utf-8") as f:
    json.dump(dataset, f, indent=2, ensure_ascii=False)

print(f"\n=========================================")
print(f"🎉 Dataset Generation Complete!")
print(f"Successfully generated & validated: {len(dataset)} examples")
print(f"Failed to generate: {len(failed)} examples")
if failed:
    print(f"Failed topics: {failed}")
print(f"Saved frozen dataset version 1 to: {output_file}")
print(f"=========================================")
