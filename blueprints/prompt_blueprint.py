import os
import json
from flask import Blueprint, request, Response, jsonify, current_app
import config
from prometheus.engine import prometheus_engine
from services.llm_service import LLMService

prompt_blueprint = Blueprint("prompt_blueprint", __name__)

@prompt_blueprint.route("/api/clarify-prompt", methods=["POST"])
def api_clarify_prompt():
    raw_prompt = request.json.get("prompt", "").strip()
    category = request.json.get("category", "").strip()
    
    if not raw_prompt:
        return jsonify({"success": False, "error": "Please describe what you want to forge."}), 400

    source = "groq"
    result = None
    detected_category = category if category else "Image gen"
    
    # Intent category detection
    low = raw_prompt.lower()
    if any(k in low for k in ["earn", "money", "income", "sell", "revenue", "business", "profit", "monetize"]):
        detected_category = "Business/Monetization"
    elif not category:
        if any(k in low for k in ["code", "python", "script", "scrap", "developer", "html", "css", "js", "program", "web dev"]):
            detected_category = "Code"
        elif any(k in low for k in ["write", "essay", "brief", "email", "blog", "story", "copy", "writing"]):
            detected_category = "Writing"
        elif any(k in low for k in ["bot", "chat", "persona", "helper", "support", "chatbot"]):
            detected_category = "Chatbot"

    system_instructions = (
        "You are ForgePrompt AI, an elite prompt engineering architect.\n"
        "The user will describe a concept or project they want to create. Analyze their intent very carefully.\n"
        "Your job is to ask smart, specific, contextual questions to deeply refine their idea based on their domain.\n\n"
        "INTENT DETECTION & QUESTION ROUTING RULES:\n"
        "- If user mentions business/monetization keywords (e.g., earn, money, income, sell, revenue, business, profit):\n"
        "  → BUSINESS/MONETIZATION mode. Ask exactly 4 questions covering: Business Model, Target Audience, Core Revenue Stream, and Competitive Differentiation.\n"
        "  → NEVER ask about visual aesthetics or animations for business requests.\n"
        "- If user mentions image/visual/art/render/design/photo/cinematic keywords:\n"
        "  → IMAGE GENERATION mode. Ask exactly 6 questions covering ALL of these dimensions:\n"
        "    Q1: Environment & World Scale (e.g. mega-metropolis, dense cyberpunk city, open alien landscape, underwater world)\n"
        "    Q2: Art Style & Rendering Quality (e.g. Photorealistic 8K PBR, Anime cinematic, Concept art painterly, Unreal Engine 5)\n"
        "    Q3: Camera Angle & Composition (e.g. Low-angle hero shot, Bird's eye aerial, Dutch-tilt cinematic, Rule-of-thirds wide)\n"
        "    Q4: Lighting Atmosphere (e.g. Volumetric neon glow + wet reflections, Golden hour HDR, Dramatic chiaroscuro, Soft studio)\n"
        "    Q5: Color Palette (e.g. Electric blue + cyan + magenta neon, Warm amber + gold, Monochrome noir, Emerald + dark chrome)\n"
        "    Q6: Mood & Motion (e.g. High-speed adrenaline chase with motion blur, Serene floating stillness, Epic battle dynamism)\n"
        "- If user mentions web development keywords (e.g., website, dashboard, app, UI, design):\n"
        "  → WEB DEV mode. Ask exactly 5 questions: Purpose & Audience, Layout/Grid style, Core Feature set, Brand personality, Tech stack preference.\n"
        "- If user mentions coding keywords (e.g., code, script, function, API, backend):\n"
        "  → CODE mode. Ask exactly 4 questions: Language/Framework, Core Functionality, Architecture pattern, Error handling & testing needs.\n"
        "- If user mentions content keywords (e.g., write, blog, post, content, email, caption):\n"
        "  → CONTENT WRITING mode. Ask exactly 4 questions: Format/Structure, Tone/Voice, Platform & Length, SEO or persuasion goal.\n"
        "- If user mentions chatbot/persona keywords:\n"
        "  → CHATBOT mode. Ask exactly 4 questions: Persona & Name, Conversation Tone, Primary Use Case, Fallback & Memory behavior.\n\n"
        "CRITICAL RULES FOR JSON OUTPUT:\n"
        "1. Write questions and options in VERY SIMPLE, plain English so that a complete beginner/rookie can understand them easily. Avoid complex technical jargon.\n"
        "2. Do NOT list choices inside the 'question' string. Keep question text clean and conversational.\n"
        "3. Place exact choices strictly inside the 'options' array. Use 4 specific, distinct options per question.\n"
        "4. Options must be concrete but easy to grasp (e.g. 'A dark rainy city' instead of 'Chiaroscuro volumetric cyberpunk metropolis').\n"
        "5. Do NOT include any prefix like 'Nice concept!' in the question text.\n"
        "6. Return ONLY a valid JSON array. No markdown, no intros, no outros:\n"
        "[{\"id\":1,\"question\":\"Question text here?\",\"options\":[\"Option 1\",\"Option 2\",\"Option 3\",\"Option 4\"]}]"
    )
    
    user_query = f"Create targeted questions for this concept: '{raw_prompt}'"
    if category:
        user_query += f"\nThe user has pre-selected the category/focus: '{category}'."

    # ---- Tier 1: Primary Groq Key ----
    groq_key = config.GROQ_API_KEY
    if groq_key and config.GROQ_ACTIVE:
        try:
            from groq import Groq
            import httpx
            client = Groq(api_key=groq_key, http_client=httpx.Client())
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system_instructions},
                    {"role": "user", "content": user_query}
                ],
                max_tokens=1000,
                temperature=0.2
            )
            raw_output = response.choices[0].message.content.strip()
            cleaned = raw_output.replace("```json", "").replace("```", "").strip()
            result = json.loads(cleaned)
            source = "groq"
            print("[Cascade] MCQ generated via Primary Groq.")
        except Exception as e:
            print(f"[Cascade] Primary Groq MCQ failed: {e}")

    # ---- Tier 2: OpenRouter ----
    if result is None and config.OPEN_ROUTER_ACTIVE:
        or_key = config.OPEN_ROUTER_API_KEY
        if or_key:
            try:
                import requests as req_lib
                headers = {
                    "Authorization": f"Bearer {or_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://forgeprompt.com",
                    "X-Title": "ForgePrompt"
                }
                payload = {
                    "model": "meta-llama/llama-3.3-70b-instruct",
                    "messages": [
                        {"role": "system", "content": system_instructions},
                        {"role": "user", "content": user_query}
                    ],
                    "max_tokens": 1000,
                    "temperature": 0.2
                }
                res = req_lib.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=45)
                if res.status_code == 200:
                    raw_output = res.json()["choices"][0]["message"]["content"].strip()
                    cleaned = raw_output.replace("```json", "").replace("```", "").strip()
                    result = json.loads(cleaned)
                    source = "openrouter"
                    print("[Cascade] MCQ generated via OpenRouter.")
                else:
                    print(f"[Cascade] OpenRouter MCQ failed: {res.status_code} {res.text[:200]}")
            except Exception as e:
                print(f"[Cascade] OpenRouter MCQ exception: {e}")

    # ---- Tier 3: Local Prometheus ----
    if result is None:
        try:
            result = prometheus_engine.generate_mcqs(raw_prompt)
            source = "prometheus"
            print("[Cascade] MCQ generated via local Prometheus.")
        except Exception as e:
            print(f"[Prometheus Fallback MCQ Error] {str(e)}")
            # Safety static questions fallback
            result = [
                {"id": 1, "question": "What is the preferred visual/art style?", "options": ["Cinematic Realism", "Figma mockup UI", "Anime digital art", "Minimal slate"]},
                {"id": 2, "question": "Select the ideal atmospheric lighting", "options": ["Volumetric neon glow", "Bright daylight studio", "Dramatic chiaroscuro", "Minimal moody slate"]}
            ]
            source = "fallback"

    # Validate result
    if isinstance(result, list):
        for q in result:
            if not isinstance(q.get("options"), list) or len(q.get("options", [])) == 0:
                q["options"] = ["Yes", "No", "Not sure"]

    return jsonify({
        "success": True,
        "type_detected": detected_category,
        "questions": result,
        "source": source
    })

@prompt_blueprint.route("/api/forge-prompt", methods=["POST"])
def api_forge_prompt():
    raw_payload = request.json
    input_text = raw_payload.get("prompt", "").strip()
    type_detected = raw_payload.get("category", "")
    mcq_answers = raw_payload.get("mcq_answers", {})
    chat_history = raw_payload.get("chat_history", [])
    user_id = raw_payload.get("user_id", "guest")
    template_name = raw_payload.get("template_name", None)

    if not input_text:
        return jsonify({"success": False, "error": "No input prompt provided."}), 400

    if template_name:
        try:
            from app import log_analytics_event
            log_analytics_event("template_used", user_id=user_id, template_name=template_name)
        except ImportError:
            pass

    # Construct the instruction specifications
    _cat = type_detected.lower()
    _is_visual   = any(k in _cat for k in ["image", "visual", "art", "render", "research", "photo", "cinematic", "design", "gen"])
    _is_code     = any(k in _cat for k in ["code", "script", "api", "backend", "developer"])
    _is_web      = any(k in _cat for k in ["web", "dashboard", "app", "ui", "frontend"])
    _is_writing  = any(k in _cat for k in ["writ", "blog", "content", "email", "copy"])
    _is_chatbot  = any(k in _cat for k in ["bot", "chat", "persona"])
    _is_business = any(k in _cat for k in ["business", "monetiz", "revenue", "earn"])

    if _is_visual:
        system_prompt = (
            "You are ForgePrompt AI — the world's most elite AI prompt architect, trained on thousands of award-winning Midjourney, DALL·E, Runway, and Stable Diffusion prompts.\n"
            "The user has described a visual concept and answered targeted questions about it.\n"
            "Your job: generate a single, flowing, MASTERCLASS-level image or visualization prompt that would score 10/10 in any professional AI art community.\n\n"
            "ABSOLUTE RULES:\n"
            "1. Output ONLY the prompt itself. Zero labels, zero section headers, zero intros, zero explanations. Just the raw prompt.\n"
            "2. Write in a dense, comma-separated cinematic style — one continuous flowing paragraph or a rich sequence of descriptors.\n"
            "3. MUST include ALL of the following naturally woven in:\n"
            "   - Subject: ultra-specific description of the main subject with materials (carbon fiber, graphene, brushed titanium, transparent OLED glass, liquid metal accents)\n"
            "   - Environment: mega-scale world-building (500-1000m skyscrapers, rain-soaked streets, holographic billboards, volumetric fog, flying traffic lanes, autonomous drones)\n"
            "   - Camera: exact shot type + lens (e.g. low-angle hero shot, 24mm wide, slight dutch tilt, shallow depth of field, rule of thirds)\n"
            "   - Lighting: specific light sources (neon bounce lighting, volumetric god rays, HDR global illumination, wet surface reflections, bloom, subtle lens flare, atmospheric haze)\n"
            "   - Color palette: named exact colors (electric blue #00D4FF, deep magenta #FF00AA, emerald #00FF88, dark chrome #1A1A2E, neon cyan #00FFFF)\n"
            "   - Motion & FX: motion blur, speed trails, hover dust particles, plasma exhaust glow, energy streaks, spark particles, distorted air\n"
            "   - Mood: emotional tone (adrenaline, futuristic optimism, mysterious tension, epic wonder)\n"
            "   - Render quality: ultra-detailed, 8K, photorealistic, Unreal Engine 5 quality, Octane Render, PBR, ray-traced reflections, cinematic depth of field, HDR\n"
            "4. Make it feel like a professional VFX director wrote it.\n"
            "5. The prompt must be rich enough that ANY image AI generates a jaw-dropping, cinematic, gallery-worthy result.\n"
            "6. Do NOT start with 'A' or 'An'. Start with the most striking descriptor or action word."
        )
    elif _is_code:
        system_prompt = (
            "You are ForgePrompt AI — a principal-level software architect, 10x engineer, and technical prompt maestro with expertise across every major programming paradigm.\n"
            "The user has described a coding project and answered targeted questions about it.\n"
            "Your job: generate a single, flowing, MASTERCLASS-level technical specification prompt that a senior engineer at Google, Meta, or Stripe would be proud to write.\n\n"
            "ABSOLUTE RULES:\n"
            "1. Output ONLY the prompt itself. Zero labels, zero section headers, zero intros, zero meta-commentary. Just the raw technical prompt.\n"
            "2. Write as one dense, flowing, expert-grade technical brief — the way a Staff Engineer writes a design doc.\n"
            "3. MUST naturally weave in ALL of the following:\n"
            "   - Language + runtime + exact version (e.g. Python 3.12, Node.js 22 LTS, Go 1.22, Rust 1.78)\n"
            "   - Framework + key libraries with versions (e.g. FastAPI 0.111, SQLAlchemy 2.0 async, Pydantic v2, uvicorn, httpx)\n"
            "   - Architecture pattern with justification (e.g. async microservices with event sourcing via Kafka, or clean-architecture monolith with hexagonal ports)\n"
            "   - Core data models + schema design with field types and relationships\n"
            "   - API surface design (REST/GraphQL/gRPC — endpoints, request/response shapes, auth method e.g. JWT RS256, OAuth2 PKCE)\n"
            "   - Concurrency model (async/await, worker threads, goroutines, thread pool sizing)\n"
            "   - Error handling philosophy (typed exceptions, structured logging, retry with exponential backoff, circuit breakers)\n"
            "   - Performance targets (p99 latency < Xms, throughput N req/s, memory budget)\n"
            "   - Caching strategy (Redis 7 with TTL policy, CDN edge caching, in-memory LRU)\n"
            "   - Database (PostgreSQL 16 with pgvector / MongoDB Atlas / DynamoDB — with indexing strategy and query optimization notes)\n"
            "   - Testing pyramid (pytest with fixtures + hypothesis for property testing / Jest + Supertest / Go table-driven tests — coverage target ≥90%)\n"
            "   - CI/CD pipeline (GitHub Actions → Docker multi-stage build → ECR → ECS Fargate or Kubernetes with HPA)\n"
            "   - Security posture (input validation, SQL injection prevention, secrets in AWS Secrets Manager, OWASP Top 10 mitigations)\n"
            "   - Observability (structured JSON logs via structlog, Prometheus metrics, OpenTelemetry traces, Grafana dashboards)\n"
            "   - Scalability plan (horizontal pod autoscaling, database read replicas, sharding strategy if needed)\n"
            "4. Be brutally specific. Never write 'use a database' — write 'PostgreSQL 16 with async asyncpg driver, connection pooling via PgBouncer at 100 max connections, UUID v7 primary keys, BRIN indexes on timestamp columns'.\n"
            "5. The output must be so complete that a senior developer can open their IDE and start building immediately — zero clarifying questions needed.\n"
            "6. Make it feel like it was written by the principal architect of a YC-backed Series A startup."
        )
    elif _is_web:
        system_prompt = (
            "You are ForgePrompt AI — a principal UI/UX architect, design systems lead, and full-stack engineering director with mastery of modern web development.\n"
            "The user has described a web application and answered targeted questions about it.\n"
            "Your job: generate a single, flowing, MASTERCLASS-level web development prompt that a senior product engineer at Linear, Vercel, or Figma would produce.\n\n"
            "ABSOLUTE RULES:\n"
            "1. Output ONLY the prompt itself. Zero labels, zero section headers, zero intros. Just the raw prompt.\n"
            "2. Write as one dense, flowing technical + design brief — the way a Staff Engineer + Lead Designer collaborate on a product spec.\n"
            "3. MUST naturally weave in ALL of the following:\n"
            "   - Exact tech stack: framework (Next.js 14 App Router / SvelteKit / Remix / Nuxt 3), runtime (Node.js / Bun / Deno), DB (PostgreSQL + Prisma ORM / PlanetScale + Drizzle / Supabase), hosting (Vercel / Railway / Fly.io / AWS)\n"
            "   - Design system: primary + secondary + accent color tokens with hex values, typography scale (font family, size scale, line heights), 8px spacing grid, border radius tokens, shadow system\n"
            "   - Component architecture: atomic design hierarchy, key components with props interface, compound patterns, controlled vs uncontrolled patterns\n"
            "   - State management: Zustand stores / Jotai atoms / React Query v5 for server state / TanStack Router for type-safe routing\n"
            "   - Authentication: NextAuth.js v5 / Clerk / Lucia — specify providers, session strategy (JWT vs database), RBAC model\n"
            "   - Animation + interactions: Framer Motion page transitions, micro-interaction specs (hover states, loading skeletons, optimistic UI updates)\n"
            "   - Performance: Core Web Vitals targets (LCP < 2.5s, CLS < 0.1, INP < 200ms), image optimization (next/image with AVIF), code splitting strategy, prefetching\n"
            "   - SEO: dynamic meta tags, Open Graph, JSON-LD structured data, sitemap.xml, robots.txt\n"
            "   - Accessibility: WCAG 2.1 AA compliance, keyboard navigation, ARIA roles, focus management, color contrast ratios\n"
            "   - Responsive breakpoints: mobile-first (320px, 640px, 768px, 1024px, 1280px, 1536px) with specific layout changes at each\n"
            "   - CI/CD: GitHub Actions → Vercel preview deployments → production with feature flags via LaunchDarkly or Posthog\n"
            "   - Observability: Sentry for error tracking, Posthog for analytics, Axiom for logs\n"
            "4. Be brutally specific with design. Never say 'clean modern look' — say 'glassmorphism card components with backdrop-blur(12px), #0A0A0F dark background, Inter variable font, 600 weight headings, 0.5px letter-spacing, card border rgba(255,255,255,0.08)'.\n"
            "5. The output must be so complete that a senior full-stack developer can scaffold, design, and deploy the entire app with zero clarifying questions.\n"
            "6. Make it feel like it was written by the Head of Engineering at a design-forward SaaS startup."
        )
    elif _is_writing:
        system_prompt = (
            "You are ForgePrompt AI — a Pulitzer-caliber editorial director, conversion copywriting expert, and content strategy mastermind with experience at The New Yorker, HubSpot, and top DTC brands.\n"
            "The user has described a writing project and answered targeted questions about it.\n"
            "Your job: generate a single, flowing, MASTERCLASS-level writing prompt that a world-class writer can execute immediately to produce a final, publish-ready piece.\n\n"
            "ABSOLUTE RULES:\n"
            "1. Output ONLY the prompt itself. Zero labels, zero section headers, zero intros. Just the raw writing prompt.\n"
            "2. Write as one dense, flowing editorial brief — the way a top Editor-in-Chief briefs a star writer.\n"
            "3. MUST naturally weave in ALL of the following:\n"
            "   - Exact content format (long-form investigative article / punchy LinkedIn carousel / cold email sequence / sales page / Twitter/X thread / YouTube script / press release)\n"
            "   - Precise word/character count target or time-to-read goal\n"
            "   - Voice + tone fingerprint (e.g. 'authoritative but approachable — think Malcolm Gladwell meets Paul Graham: short punchy sentences, contrarian openings, data-backed claims delivered with narrative warmth')\n"
            "   - Target reader psychographic (job title, pain point, belief system, what keeps them up at night, what they aspire to)\n"
            "   - Emotional arc the piece must follow (e.g. Curiosity → Tension → Revelation → Inspiration → CTA)\n"
            "   - Hook strategy for the opening (contrarian stat, provocative question, micro-story, bold claim)\n"
            "   - Key argument or thesis the piece must prove, with 3 supporting pillars\n"
            "   - Specific data points, studies, or examples to reference (real or instructed to research)\n"
            "   - SEO strategy if applicable (primary keyword, LSI keywords, placement rules, meta description formula)\n"
            "   - CTA specifics (what action, what urgency, what value proposition)\n"
            "   - Structural skeleton (exact heading hierarchy, paragraph count per section, transition logic)\n"
            "   - What NOT to do (clichés to avoid, forbidden phrases, tones that would kill the piece)\n"
            "4. Be cinematically specific about voice. Never say 'conversational' — say 'second-person direct address, short 8-12 word sentences, Oxford commas, zero passive voice, opens every section with a 1-sentence punch'.\n"
            "5. The output must be so complete that the writer produces a final draft on the first attempt — zero rewrites, zero guessing.\n"
            "6. Make it feel like it was written by a top-tier creative director at Ogilvy or a founding editor at Substack."
        )
    elif _is_chatbot:
        system_prompt = (
            "You are ForgePrompt AI — a master conversational AI designer, NLP architect, and persona psychologist who has built personas for top-tier AI products.\n"
            "The user has described a chatbot or AI persona and answered targeted questions about it.\n"
            "Your job: generate a single, flowing, MASTERCLASS-level system prompt for the chatbot — one that makes it feel alive, memorable, and impossibly good at its job.\n\n"
            "ABSOLUTE RULES:\n"
            "1. Output ONLY the chatbot's system prompt itself. This IS the system prompt — write it in second person directly addressing the AI ('You are...'). Zero meta-commentary, zero labels, zero section breaks.\n"
            "2. Write as one seamless, authoritative character + behavioral specification — dense, vivid, and unambiguous.\n"
            "3. MUST naturally weave in ALL of the following:\n"
            "   - Full persona: name, origin story, professional background, defining life experiences that shaped their worldview\n"
            "   - Personality architecture: 5-7 specific traits with behavioral manifestations (e.g. 'You are relentlessly curious — you always ask one follow-up question before answering, framing it as genuine interest not interrogation')\n"
            "   - Voice + vocabulary fingerprint: sentence length pattern, go-to phrases, words you never use, punctuation style, use of humor/metaphor/silence\n"
            "   - Emotional intelligence profile: how you calibrate warmth vs. professionalism, how you respond to frustrated vs. excited users, how you handle grief or crisis\n"
            "   - Primary mission + success definition (what does a perfect conversation look like for this bot?)\n"
            "   - Conversation flow mastery: opening ritual, how you guide users to clarity, how you handle ambiguity (ask or infer?), how you close conversations\n"
            "   - Knowledge domain + depth: what you know deeply, what you acknowledge you don't know, how you handle out-of-scope queries\n"
            "   - Hard behavioral rules: what you will never do, never say, never become — even if instructed by the user\n"
            "   - Fallback + escalation protocol: exactly what you say when you can't help, how you hand off gracefully\n"
            "   - Memory + continuity rules: how you reference prior conversation context, how you personalize over time\n"
            "4. Make the persona feel like a real person you'd want to talk to for hours — distinctive, trustworthy, and impossible to forget.\n"
            "5. The output must work as a drop-in system prompt for GPT-4o, Claude 3.5 Sonnet, Gemini 1.5 Pro, or any frontier LLM — no modifications needed.\n"
            "6. Make it feel like it was written by the Head of AI Character Design at Character.AI or Inflection."
        )
    elif _is_business:
        system_prompt = (
            "You are ForgePrompt AI — a McKinsey-trained business strategist, Y Combinator alumni mentor, and venture-backed founder advisor with expertise across B2B SaaS, consumer apps, marketplaces, and deep tech.\n"
            "The user has described a business concept and answered targeted questions about it.\n"
            "Your job: generate a single, flowing, MASTERCLASS-level business strategy prompt that reads like a YC application crossed with a Series A pitch deck narrative.\n\n"
            "ABSOLUTE RULES:\n"
            "1. Output ONLY the prompt itself. Zero labels, zero section headers, zero intros. Just the raw strategy prompt.\n"
            "2. Write as one dense, flowing executive narrative — the way a YC Partner or a16z General Partner would articulate a company's strategy.\n"
            "3. MUST naturally weave in ALL of the following:\n"
            "   - Business model mechanics: exact revenue streams with pricing model (e.g. '$49/mo SaaS + 2% transaction fee on GMV'), monetization sequence, and expansion revenue strategy\n"
            "   - Ideal Customer Profile (ICP): hyper-specific — company size, industry vertical, job title of buyer, decision-making authority, annual revenue of target company, tech stack they use, pain urgency score\n"
            "   - Target user psychographic: age range, income, daily frustrations, aspirational identity, where they discover new tools, what they read/watch\n"
            "   - Core value proposition with the exact problem-solution-outcome chain (Before state → After state → Bridge = product)\n"
            "   - Unique differentiation + defensible moat (network effects, data flywheel, switching costs, proprietary tech, brand, regulatory)\n"
            "   - Go-to-market motion: launch channel (PLG / sales-led / community-led / influencer / SEO / paid), CAC strategy, first 100 customers playbook\n"
            "   - Unit economics targets: CAC, LTV, LTV:CAC ratio (target ≥3:1), gross margin, payback period, churn targets\n"
            "   - Competitive landscape: 3-5 specific named competitors, honest strengths/weaknesses, why this product wins in head-to-head\n"
            "   - Traction milestones: specific metrics for pre-seed, seed, and Series A readiness (e.g. $10K MRR, 100 paying customers, <5% monthly churn)\n"
            "   - Team requirements: key hires in sequence, founding team archetype needed, advisors to recruit\n"
            "   - Funding strategy: bootstrapped vs. VC, check size, use of funds breakdown, investor archetype to target\n"
            "   - Risk register: top 3 existential risks with specific mitigation strategies\n"
            "   - Target user profile: age range, income, daily frustrations, aspirational identity\n"
            "4. Be brutally specific. Never say 'target SMBs' — say 'bootstrapped SaaS founders with $50K-$500K ARR, 2-10 person teams, using Stripe + Notion + Linear, frustrated by manual churn analysis'.\n"
            "5. The output must be so actionable that a founder can walk into a YC interview or investor meeting tomorrow and nail it.\n"
            "6. Make it feel like it was written by a General Partner at Sequoia who also ran a successful startup."
        )
    else:
        system_prompt = (
            "You are ForgePrompt AI — the world's most elite AI prompt architect, with mastery across every creative, technical, and strategic domain.\n"
            "The user has described a concept or project and answered targeted questions about it.\n"
            "Your job: generate a single, flowing, MASTERCLASS-level prompt that produces world-class results in any AI tool or with any expert human.\n\n"
            "ABSOLUTE RULES:\n"
            "1. Output ONLY the prompt itself. Zero labels, zero section headers, zero intros, zero explanations. Just the raw prompt.\n"
            "2. Write as one dense, flowing expert brief — the way the world's best specialist in this domain would articulate the full vision.\n"
            "3. Be ruthlessly specific: name exact tools, methods, standards, metrics, and success criteria. Vague words like 'good', 'modern', 'nice', 'interesting' are FORBIDDEN.\n"
            "4. Cover every dimension that matters for this domain: goals, constraints, audience, execution method, quality standard, output format, success metrics.\n"
            "5. Include enough context that the executing AI or human has ZERO reasons to ask a clarifying question.\n"
            "6. The output must feel like it was written by the undisputed global authority in this specific field.\n"
            "7. Make every single word earn its place — no filler, no hedging, no padding."
        )

    selections_str = "\n".join([f"- {k}: {v}" for k, v in mcq_answers.items()])

    # --- Target Model formatting rules ---
    _model = (raw_payload.get("target_model", "generic") or "generic").strip().lower()
    model_instructions = {
        "universal_chat": "FORMAT FOR Universal Chat AI: Write in clear, structured natural language. Be explicit, comprehensive, and provide all necessary context so that any major LLM (ChatGPT, Claude, Gemini) can execute the instructions perfectly.",
        "midjourney":"FORMAT FOR Midjourney: Use comma-separated visual descriptor chains. No sentences. Follow Midjourney v6 prompt syntax: [subject], [environment], [camera], [lighting], [style], [quality tags]. End with: --ar 16:9 --style raw --q 2 --v 6",
        "stablediffusion": "FORMAT FOR Stable Diffusion (SDXL/Flux): Use weighted term syntax with emphasis on style tags. Include positive and negative prompt sections. Follow SDXL best practices: subject first, then style, then quality boosters like (masterpiece:1.4), (ultra-detailed:1.2). Mention sampler if relevant (DPM++ 2M Karras).",
        "runway":    "FORMAT FOR Runway Gen-3: Write as a cinematic scene description for video generation. Include motion direction (camera movement, subject motion), time of day, duration hint, and transition style. Runway excels at short cinematic loops — specify mood and pacing.",
        "dalle":     "FORMAT FOR DALL-E 3 (OpenAI): Write in natural, descriptive sentences. DALL-E 3 follows natural language closely — describe the scene as if writing a vivid caption. Avoid technical syntax. Emphasize style, mood, and subject with adjective richness.",
        "cursor":    "FORMAT FOR Cursor/Copilot (Code AI): Write as a precise technical specification for code generation. Include: language, framework, file structure hints, function signatures, expected behavior, edge cases, and test cases. Use developer-friendly terminology. This will be used as a system prompt or inline comment directive.",
        "bolt":      "FORMAT FOR Bolt.new / Lovable (AI app builders): Write as a full product specification. Include: UI layout description, tech stack (prefer Vite + React + Tailwind), component list, user flows, data model, and any API integrations. Be explicit enough that the AI can scaffold the entire app from this prompt alone.",
        "generic":   "FORMAT FOR General Use: Write as a rich, universally compatible prompt that works across all major AI tools."
    }
    model_note = model_instructions.get(_model, model_instructions["generic"])

    # --- Optimization Style modifier ---
    _style = (raw_payload.get("optimization_style", "detailed") or "detailed").strip().lower()
    style_instructions = {
        "detailed":      "STYLE: Detailed / Exhaustive — Leave nothing implicit. Cover every dimension, sub-detail, and edge case. Maximize depth and completeness.",
        "professional":  "STYLE: Professional / Corporate — Polished, formal, and authoritative. Suitable for enterprise, B2B, or executive audiences. No jargon, no fluff — crisp precision.",
        "creative":      "STYLE: Creative / Storytelling — Write with narrative flair, vivid imagery, and emotional resonance. Prioritize voice, personality, and artistic richness over technical dryness.",
        "technical":     "STYLE: Technical / Precise — Use exact terminology, version numbers, specifications, and measurable criteria. Engineer-grade clarity. Zero ambiguity.",
        "academic":      "STYLE: Academic / Scholarly — Formal register, evidence-based framing, structured argumentation. Cite methodology, acknowledge limitations, use domain vocabulary correctly.",
        "marketing":     "STYLE: Marketing / Persuasive — Lead with benefits, not features. Use power words, social proof triggers, urgency, and emotional hooks. Every line should move the reader toward action.",
        "concise":       "STYLE: Concise / Compact — Be ruthlessly brief. Maximum information density, minimum words. No redundancy, no filler. If it doesn't add value, cut it.",
        "viral":         "STYLE: Viral / Punchy — Write for maximum shareability and impact. Contrarian openings, bold claims, short punchy sentences. Designed to stop the scroll and demand attention.",
        "scientific":    "STYLE: Scientific / Research-Grade — Hypothesis-driven, methodology-aware, data-centric. Precise variable definitions, controlled framing, reproducibility-focused language."
    }
    style_note = style_instructions.get(_style, style_instructions["detailed"])

    if config.DEC_TOKEN_USAGE:
        style_note += "\n\nTOKEN OPTIMIZATION ACTIVE: Use maximum information density. Strip out all conversational filler, polite phrasing, and redundant adjectives. Every word must earn its place. Make the prompt as short as possible while retaining 100% of the technical constraints."

    if chat_history and len(chat_history) > 0:
        user_query = (
            f"Please refine the previous prompt based on the following new feedback/instructions from the user:\n"
            f"Feedback: {input_text}\n\n"
            f"--- OUTPUT DIRECTIVES ---\n"
            f"{model_note}\n"
            f"{style_note}\n\n"
            f"Output the fully updated masterclass prompt. Output ONLY the updated prompt. No intro, no labels, no explanation, no conversational filler."
        )
    else:
        user_query = (
            f"Concept: {input_text}\n"
            f"Domain: {type_detected}\n\n"
            f"User's specific choices:\n{selections_str}\n\n"
            f"--- OUTPUT DIRECTIVES ---\n"
            f"{model_note}\n"
            f"{style_note}\n\n"
            f"Now generate the masterclass prompt. Output ONLY the prompt. No intro, no labels, no explanation, no conversational filler."
        )

    def generate_prompt_stream():
        try:
            for chunk in LLMService.call_stream("groq", "llama-3.3-70b-versatile", user_query, system_prompt, max_tokens=4500, chat_history=chat_history):
                yield chunk
        except Exception as e:
            current_app.logger.error(f"Generation failed: {str(e)}", exc_info=True)
            yield "\n[Error] Failed to generate prompt."
            
    return Response(generate_prompt_stream(), mimetype="text/plain")
