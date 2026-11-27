"""The fixed topic list shared by podcasts, newsletters and notebooks.

A fixed list keeps tags from drifting ("pricing" vs "monetization" vs "SaaS
pricing"), which is what lets "find more on this topic" match across sources.
Until the Opus pre-generation pass runs, passages are tagged by meaning
similarity to these descriptions (cited/index.py); the pre-generation pass
replaces those provisional tags.
"""
from __future__ import annotations

TOPICS: list[dict] = [
    {"id": "ai-product", "name": "Building AI products",
     "description": "Building AI and LLM powered products, AI features, agents, copilots, model choice, prompts, AI product strategy."},
    {"id": "ai-evals", "name": "AI evals & quality",
     "description": "Evals: testing and measuring LLM output quality, LLM-as-judge, test sets, error analysis, hallucination rates."},
    {"id": "ai-pm-skills", "name": "AI skills for PMs",
     "description": "How product managers use AI tools day to day, vibe coding, prototyping with AI, Claude Code, Cursor, AI workflows for PMs."},
    {"id": "pricing", "name": "Pricing & monetization",
     "description": "Pricing strategy, packaging, monetization, willingness to pay, usage-based pricing, price increases, SaaS pricing."},
    {"id": "growth", "name": "Growth & acquisition",
     "description": "User acquisition, growth loops, channels, SEO, paid marketing, virality, referral, activation and funnel growth."},
    {"id": "retention", "name": "Retention & engagement",
     "description": "Retention, churn, engagement, habit formation, onboarding, activation, cohort analysis."},
    {"id": "plg", "name": "Product-led growth",
     "description": "Product-led growth, self-serve, freemium, free trials, PLG versus sales-led motion, conversion to paid."},
    {"id": "strategy", "name": "Product strategy & vision",
     "description": "Product strategy, vision, roadmaps, prioritization, bets, competitive strategy, product operating model."},
    {"id": "discovery", "name": "Discovery & user research",
     "description": "Product discovery, customer interviews, user research, validating ideas, jobs to be done, experiments before building."},
    {"id": "metrics", "name": "Metrics & experimentation",
     "description": "North star metrics, KPIs, A/B testing, experimentation, analytics, data-driven decisions, OKRs."},
    {"id": "positioning", "name": "Positioning & GTM",
     "description": "Positioning, messaging, go-to-market, launches, product marketing, category design, competitive differentiation."},
    {"id": "careers", "name": "PM careers & job search",
     "description": "Getting a PM job, PM interviews, resumes, career growth, promotions, becoming a senior PM or product leader, salaries."},
    {"id": "leadership", "name": "Leadership & teams",
     "description": "Managing product teams, leadership, hiring, org design, working with engineering and design, stakeholder management, culture."},
    {"id": "execution", "name": "Execution & shipping",
     "description": "Shipping fast, execution, specs and PRDs, working with engineers, delivery, agile, writing and communication."},
    {"id": "b2b-enterprise", "name": "B2B & enterprise",
     "description": "B2B SaaS, enterprise sales, selling to businesses, customer success, platform and API products."},
    {"id": "consumer", "name": "Consumer products",
     "description": "Consumer apps, social products, marketplaces, consumer subscription, mobile apps, network effects."},
    {"id": "founders", "name": "Founders & startups",
     "description": "Starting a company, founders, fundraising, product market fit, early-stage startups, pivots."},
    {"id": "design-ux", "name": "Design & UX",
     "description": "Product design, UX, user experience, design systems, craft, usability."},
]

# Hand-written starter questions, shown until pre-generated related questions exist.
STARTERS: dict[str, list[str]] = {
    "ai-product": [
        "How do you decide where AI belongs in your product?",
        "What separates AI features users love from ones they ignore?"
    ],
    "ai-evals": [
        "How do I set up evals for an AI feature?",
        "When is an LLM judge good enough?"
    ],
    "ai-pm-skills": [
        "How should PMs use Claude Code day to day?",
        "How do I prototype a feature with AI tools?"
    ],
    "pricing": [
        "How should I price a new AI feature?",
        "When and how should you raise prices?"
    ],
    "growth": [
        "Which growth channels work best early on?",
        "What does a great head of growth do?"
    ],
    "retention": [
        "How do I diagnose why users churn?",
        "What makes onboarding actually stick?"
    ],
    "plg": [
        "When does product-led growth work, and when doesn't it?",
        "How do you convert free users to paid?"
    ],
    "strategy": [
        "How do you write a product strategy?",
        "How should I prioritize a roadmap?"
    ],
    "discovery": [
        "How do I run good customer interviews?",
        "How do you validate an idea before building it?"
    ],
    "metrics": [
        "What makes a good North Star metric?",
        "How should a PM run A/B tests?"
    ],
    "positioning": [
        "How do I position a B2B product?",
        "How do you plan a product launch?"
    ],
    "careers": [
        "How do I land my first PM job?",
        "How do I prepare for PM interviews?"
    ],
    "leadership": [
        "How do you manage a team of PMs?",
        "How do PMs build trust with engineering?"
    ],
    "execution": [
        "How do you write a good PRD?",
        "How do the best teams ship faster?"
    ],
    "b2b-enterprise": [
        "How is B2B product management different?",
        "How do you sell into enterprises?"
    ],
    "consumer": [
        "What makes consumer products grow?",
        "How do marketplaces solve the chicken-and-egg problem?"
    ],
    "founders": [
        "How do you know you have product-market fit?",
        "What should first-time founders get right?"
    ],
    "design-ux": [
        "How should PMs work with designers?",
        "What makes a product experience feel great?"
    ]
}

TOPIC_BY_ID = {t["id"]: t for t in TOPICS}
