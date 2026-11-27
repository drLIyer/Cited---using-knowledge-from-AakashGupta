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
     "description": "Evaluating AI products, evals, LLM judges, test sets, hallucinations, measuring model output quality."},
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

TOPIC_BY_ID = {t["id"]: t for t in TOPICS}
