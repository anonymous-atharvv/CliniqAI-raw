"""
Prompt Builder — 5-Section Clinical Reasoning Prompt Architecture

Section 1: Role + Constraints (static ~200 tokens)
Section 2: Patient Context (dynamic, priority-filled to budget)
Section 3: Task Specification (CoT clinical reasoning steps)
Section 4: Output Schema (strict JSON enforcement)
Section 5: Guardrails (rejection criteria)

Design principles:
- Section 1 is NEVER summarized or compressed — its constraints are non-negotiable
- Section 2 fills remaining context window after other sections reserved
- Section 3 forces step-by-step reasoning before conclusion (reduces hallucination)
- Section 4 enforces structured JSON — no free-form text responses
- Section 5 lists explicit retry triggers — engine auto-retries on violation
"""

from typing import Dict, List, Optional, Any
import json


SECTION_1_ROLE = """You are a clinical decision support AI embedded in a hospital intelligence system.

CORE CONSTRAINTS (never violate):
• You ASSIST licensed physicians. You do NOT replace them or provide final diagnoses.
• You suggest HYPOTHESES for physician evaluation, not clinical conclusions.
• You ALWAYS cite the specific patient data points behind your reasoning.
• You EXPLICITLY flag uncertainty: state "I am uncertain about X because Y."
• You CONSERVATIVELY err toward more testing and monitoring over less.
• When data is insufficient: escalate to human review — never guess.
• For HIGH or CRITICAL risk: human_review_required MUST be true.
• All clinical claims MUST reference the injected patient data, not training knowledge alone.
  Mark general medical knowledge as "clinical_reasoning" not "patient_data".
• NEVER recommend medication changes, ICU transfers, or procedures autonomously.
  These require physician orders. You may SUGGEST them for physician consideration.
• You do NOT have access to the internet. All knowledge comes from injected context."""

SECTION_3_TASK = """
REASONING PROTOCOL (execute ALL 5 steps in order before producing output):

STEP 1 — PATIENT STATE SYNTHESIS:
Write 3–5 sentences covering: chief complaint, key abnormal findings (cite values),
current trajectory (improving/stable/worsening based on trend data).
Be specific. "HR 112 and rising over 4h" not "tachycardia".

STEP 2 — DIFFERENTIAL GENERATION:
List 3–5 conditions consistent with the data.
For EACH differential:
  - Cite ≥2 specific data points that support it (lab values, vital trends, symptoms)
  - Note contradicting evidence if any
  - Assign probability_rank: primary / alternative / rule_out
  - Do NOT list differentials without supporting data from the patient record

STEP 3 — RISK STRATIFICATION:
Assign: LOW / MEDIUM / HIGH / CRITICAL
For HIGH or CRITICAL: list the SPECIFIC trigger criteria (e.g., "NEWS2=6, >threshold of 5").
Justify using: vital trends, lab values, clinical scores, comorbidities.

STEP 4 — RECOMMENDED ACTIONS:
List actions in urgency order: immediate (1h) → short_term (6h) → monitoring (ongoing).
Each action MUST: map to a specific finding, cite a guideline or data point as evidence_base.
Phrase as suggestions for physician consideration, not physician orders.

STEP 5 — CONFIDENCE AND UNCERTAINTY:
Rate: HIGH / MEDIUM / LOW
List specific DATA GAPS that limit confidence.
State: what information would most change this assessment."""

SECTION_5_GUARDRAILS = """
GUARDRAIL RULES — OUTPUT IS INVALID AND MUST RETRY IF:
1. risk_level = HIGH or CRITICAL but human_review_required = false
2. Any differential_diagnosis has an empty supporting_evidence list
3. Any recommended_action lacks a rationale field
4. Response is not valid JSON matching the output schema exactly
5. patient_state_summary is missing or fewer than 20 words
6. risk_justification is missing for HIGH or CRITICAL risk_level

Retry limit: 3. If still failing after 3 attempts → return error, do not guess."""

OUTPUT_SCHEMA = {
    "patient_state_summary": "string (3–5 sentences citing specific values)",
    "differential_diagnoses": [
        {
            "condition": "string",
            "icd10": "string (ICD-10 code e.g. A41.9)",
            "supporting_evidence": ["string — cite specific data: 'HR 128 bpm (tachycardia)'"],
            "contradicting_evidence": ["string — cite specific data or absence of data"],
            "probability_rank": "primary|alternative|rule_out",
            "confidence": "float 0.0–1.0",
        }
    ],
    "risk_level": "LOW|MEDIUM|HIGH|CRITICAL",
    "risk_justification": "string — cite specific triggers, required for HIGH/CRITICAL",
    "recommended_actions": [
        {
            "action": "string — specific, actionable suggestion for physician",
            "urgency": "immediate|short_term|monitoring",
            "rationale": "string — why this is needed",
            "evidence_base": "string — clinical guideline or specific data point",
        }
    ],
    "overall_confidence": "HIGH|MEDIUM|LOW",
    "data_gaps": ["string — missing information that would change assessment"],
    "human_review_required": "boolean — true for HIGH/CRITICAL always",
    "human_review_reason": "string — why physician review is needed",
}


class PromptBuilder:
    """Builds the 5-section clinical reasoning prompt for the LLM."""

    # Token budgets per section
    SECTION_TOKENS = {
        "section_1": 400,
        "section_3": 600,
        "section_4": 400,
        "section_5": 200,
        "response_reserved": 4096,
    }
    MAX_CONTEXT = 128000

    @property
    def patient_context_budget(self) -> int:
        reserved = sum(self.SECTION_TOKENS.values())
        return self.MAX_CONTEXT - reserved

    def build_system_prompt(self) -> str:
        schema_json = json.dumps(OUTPUT_SCHEMA, indent=2)
        return (
            f"{SECTION_1_ROLE}\n\n"
            f"{SECTION_3_TASK}\n\n"
            f"OUTPUT SCHEMA (respond ONLY with valid JSON — no markdown, no preamble):\n{schema_json}\n\n"
            f"{SECTION_5_GUARDRAILS}"
        )

    def build_user_message(
        self,
        patient_context: str,
        multimodal_findings: Optional[Dict] = None,
    ) -> str:
        parts = [f"## PATIENT DATA\n\n{patient_context}"]
        if multimodal_findings:
            if imaging := multimodal_findings.get("imaging"):
                parts.append(f"## AI IMAGING ANALYSIS\n{json.dumps(imaging, indent=2, default=str)}")
            if nlp := multimodal_findings.get("nlp"):
                parts.append(f"## AI NLP ANALYSIS\n{json.dumps(nlp, indent=2, default=str)}")
            if vitals := multimodal_findings.get("vitals"):
                parts.append(f"## AI VITALS PREDICTIONS\n{json.dumps(vitals, indent=2, default=str)}")
            if discordances := multimodal_findings.get("discordances"):
                parts.append(f"## ⚠️ MODALITY DISCORDANCES (require physician review)\n{json.dumps(discordances, indent=2, default=str)}")
        parts.append("## TASK\nApply the 5-step reasoning protocol. Return valid JSON only.")
        return "\n\n".join(parts)
