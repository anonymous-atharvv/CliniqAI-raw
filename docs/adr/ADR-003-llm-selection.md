# ADR-003: LLM Selection for Clinical Reasoning Engine

**Date**: 2025-Q3 | **Status**: Accepted

## Decision: Vendor-Agnostic (Claude Sonnet + GPT-4o, configurable)

## Context
The clinical reasoning engine needs: 128k context window, structured JSON output, clinical accuracy, HIPAA BAA coverage, < 10 second response time.

## Evaluation

| Model | Context | JSON | Clinical | HIPAA BAA | Latency |
|-------|---------|------|----------|-----------|---------|
| Claude Sonnet 4 (Anthropic) | 200k | ✅ | ✅ High | ✅ via AWS | ~3-5s |
| GPT-4o (Azure OpenAI) | 128k | ✅ | ✅ High | ✅ Azure | ~4-6s |
| Gemini Pro (GCP) | 1M | ✅ | ✅ | ✅ GCP | ~3-5s |
| Open-source (Llama-3) | 128k | ⚠ | ⚠ Lower | ✅ self-hosted | Variable |

## Decision

**Primary:** Claude Sonnet via Anthropic API or AWS Bedrock (HIPAA BAA available).  
**Fallback:** GPT-4o via Azure OpenAI Service (HIPAA BAA via Azure agreement).  
**Configuration:** `LLM_PROVIDER` environment variable. No code change to switch.

**Why not open-source:** At 128k context, no open-source model matches Claude/GPT-4o accuracy for clinical reasoning. The risk of hallucination in a clinical context is too high without production-grade RLHF training.

**Why vendor-agnostic:** HIPAA BAA availability varies. Having two providers means we can switch if one loses BAA coverage or degrades in quality.

**HIPAA BAA coverage:**
- Anthropic: Direct BAA available, also available via AWS Bedrock
- Azure OpenAI: Microsoft Azure HIPAA BAA covers Azure OpenAI Service
- PHI NEVER reaches the LLM — all data is de-identified before inference

**Guardrails (non-negotiable):**
1. Output schema validation before physician display
2. Max 3 retries on invalid output
3. HIGH/CRITICAL risk always triggers human_review_required=True
4. All clinical claims must be grounded in injected patient data
