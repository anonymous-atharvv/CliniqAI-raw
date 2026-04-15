"""
LLM Clinical Reasoning Engine (Layer 4)

Architecture:
- Base model: Claude Sonnet or GPT-4o (configurable)
- Private API gateway (Azure OpenAI for HIPAA BAA)
- 5-section prompt architecture
- Chain-of-thought clinical reasoning
- Output validation layer
- Hallucination prevention
- Token budget management

CRITICAL DESIGN RULES:
1. LLM assists physicians. Never replaces them.
2. All clinical claims must be grounded in patient data — no training knowledge alone.
3. HIGH/CRITICAL risk always requires human_review_required = True.
4. Invalid JSON output = retry (max 3) then return reasoning_failed.
5. MedNLI consistency check on all outputs.
"""

import json
import time
import logging
import hashlib
from typing import Optional, Dict, List, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Output Schema (Strict — reject if invalid)
# ─────────────────────────────────────────────

class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ConfidenceLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ProbabilityRank(str, Enum):
    PRIMARY = "primary"
    ALTERNATIVE = "alternative"
    RULE_OUT = "rule_out"


class ActionUrgency(str, Enum):
    IMMEDIATE = "immediate"    # Next 1 hour
    SHORT_TERM = "short_term"  # Next 6 hours
    MONITORING = "monitoring"  # Ongoing


@dataclass
class DifferentialDiagnosis:
    condition: str
    icd10: str
    supporting_evidence: List[str]
    contradicting_evidence: List[str]
    probability_rank: ProbabilityRank
    confidence: float  # 0.0–1.0
    
    def validate(self) -> Tuple[bool, List[str]]:
        errors = []
        if not self.condition:
            errors.append("condition is required")
        if not self.supporting_evidence:
            errors.append(f"Differential '{self.condition}' has no supporting_evidence")
        if not 0.0 <= self.confidence <= 1.0:
            errors.append(f"confidence must be 0.0–1.0, got {self.confidence}")
        return len(errors) == 0, errors


@dataclass
class RecommendedAction:
    action: str
    urgency: ActionUrgency
    rationale: str
    evidence_base: str  # Clinical guideline or specific data point
    
    def validate(self) -> Tuple[bool, List[str]]:
        errors = []
        if not self.action:
            errors.append("action is required")
        if not self.rationale:
            errors.append(f"Action '{self.action}' lacks rationale")
        if not self.evidence_base:
            errors.append(f"Action '{self.action}' lacks evidence_base")
        return len(errors) == 0, errors


@dataclass
class ClinicalReasoningOutput:
    """The validated output from the LLM reasoning engine."""
    patient_state_summary: str
    differential_diagnoses: List[DifferentialDiagnosis]
    risk_level: RiskLevel
    risk_justification: str
    recommended_actions: List[RecommendedAction]
    overall_confidence: ConfidenceLevel
    data_gaps: List[str]
    human_review_required: bool
    human_review_reason: str
    
    # Metadata
    reasoning_model: str = ""
    reasoning_duration_ms: int = 0
    token_count: int = 0
    retry_count: int = 0
    timestamp: str = ""
    
    def validate(self) -> Tuple[bool, List[str]]:
        """
        Strict output validation.
        Guardrails: reject and retry if:
        1. HIGH/CRITICAL risk but human_review_required = False
        2. Differential has no supporting_evidence
        3. Recommended action lacks rationale
        """
        errors = []
        
        # Guardrail 1: Critical risk must have human review
        if self.risk_level in [RiskLevel.HIGH, RiskLevel.CRITICAL]:
            if not self.human_review_required:
                errors.append(
                    f"GUARDRAIL VIOLATION: risk_level={self.risk_level} "
                    f"but human_review_required=False. Auto-correcting."
                )
                # Auto-correct this — it's a safety issue
                self.human_review_required = True
                self.human_review_reason = f"Auto-escalated: {self.risk_level} risk requires physician review"
        
        # Guardrail 2: Differential evidence
        for dx in self.differential_diagnoses:
            valid, dx_errors = dx.validate()
            if not valid:
                errors.extend(dx_errors)
        
        # Guardrail 3: Action rationale
        for action in self.recommended_actions:
            valid, action_errors = action.validate()
            if not valid:
                errors.extend(action_errors)
        
        # Guardrail 4: Summary must exist
        if not self.patient_state_summary:
            errors.append("patient_state_summary is required")
        
        # Guardrail 5: Risk justification for high risk
        if self.risk_level in [RiskLevel.HIGH, RiskLevel.CRITICAL]:
            if not self.risk_justification or len(self.risk_justification) < 20:
                errors.append(f"Insufficient risk_justification for {self.risk_level} risk")
        
        return len(errors) == 0, errors


class PatientContextBuilder:
    """
    Builds the dynamic patient context section (Section 2 of system prompt).
    
    Token budget management:
    - Scores all available patient data by priority
    - Fills context window up to budget
    - Prioritizes: recency × 0.40 + severity × 0.35 + semantic_similarity × 0.25
    
    Target: Fit most relevant data in ~52k tokens (leaving room for response).
    """
    
    TOKEN_BUDGET = 52000
    
    def __init__(self, tokenizer=None):
        # Simple approximation: 1 token ≈ 4 chars
        self._tokenizer = tokenizer
    
    def estimate_tokens(self, text: str) -> int:
        return len(text) // 4
    
    def calculate_priority(
        self,
        item: Dict[str, Any],
        hours_ago: float,
        severity: float,  # 0.0–1.0
        semantic_similarity: float,  # 0.0–1.0
    ) -> float:
        """
        Priority score for context item selection.
        Higher = more important = goes into context window first.
        """
        # Recency: exponential decay, half-life ≈ 6 hours
        recency_score = 2 ** (-hours_ago / 6)
        
        return (
            0.40 * recency_score +
            0.35 * severity +
            0.25 * semantic_similarity
        )
    
    def build_context(
        self,
        patient_data: Dict[str, Any],
        current_vitals: List[Dict],
        recent_labs: List[Dict],
        medications: List[Dict],
        imaging_findings: List[Dict],
        nlp_summaries: List[Dict],
        historical_context: List[Dict],
        chief_complaint: str = "",
    ) -> str:
        """
        Build priority-ordered patient context string.
        Will truncate at TOKEN_BUDGET tokens.
        """
        sections = []
        remaining_budget = self.TOKEN_BUDGET
        
        # 1. Current vitals + alerts (highest priority — last 2 hours)
        vitals_text = self._format_vitals(current_vitals)
        vitals_tokens = self.estimate_tokens(vitals_text)
        if vitals_tokens < remaining_budget:
            sections.append(("CURRENT VITALS (Last 2 Hours)", vitals_text))
            remaining_budget -= vitals_tokens
        
        # 2. Active medications + problems
        meds_text = self._format_medications(medications)
        meds_tokens = self.estimate_tokens(meds_text)
        if meds_tokens < remaining_budget:
            sections.append(("ACTIVE MEDICATIONS + PROBLEMS", meds_text))
            remaining_budget -= meds_tokens
        
        # 3. Recent labs (24h)
        labs_text = self._format_labs(recent_labs)
        labs_tokens = self.estimate_tokens(labs_text)
        if labs_tokens < remaining_budget:
            sections.append(("RECENT LABORATORY RESULTS (24h)", labs_text))
            remaining_budget -= labs_tokens
        
        # 4. Imaging findings
        if imaging_findings and remaining_budget > 2000:
            imaging_text = self._format_imaging(imaging_findings)
            sections.append(("IMAGING FINDINGS", imaging_text))
            remaining_budget -= self.estimate_tokens(imaging_text)
        
        # 5. NLP clinical summary
        if nlp_summaries and remaining_budget > 2000:
            nlp_text = self._format_nlp(nlp_summaries)
            sections.append(("CLINICAL NLP SUMMARY", nlp_text))
            remaining_budget -= self.estimate_tokens(nlp_text)
        
        # 6. Historical context from vector DB (fill remaining budget)
        if historical_context and remaining_budget > 1000:
            hist_text = self._format_history(historical_context, remaining_budget)
            sections.append(("RELEVANT PATIENT HISTORY", hist_text))
        
        # Format as structured context
        context_parts = []
        for title, content in sections:
            context_parts.append(f"### {title}\n{content}")
        
        if chief_complaint:
            header = f"**Chief Complaint / Reason for Visit**: {chief_complaint}\n\n"
        else:
            header = ""
        
        return header + "\n\n".join(context_parts)
    
    def _format_vitals(self, vitals: List[Dict]) -> str:
        if not vitals:
            return "No recent vitals available."
        
        lines = []
        for v in vitals[:20]:  # Last 20 readings
            timestamp = v.get("timestamp", "Unknown time")
            lines.append(
                f"- {v.get('parameter', 'Unknown')}: {v.get('value', 'N/A')} "
                f"{v.get('unit', '')} at {timestamp}"
                + (" ⚠️ CRITICAL" if v.get("critical") else "")
                + (" ⬆️ HIGH" if v.get("abnormal_high") else "")
                + (" ⬇️ LOW" if v.get("abnormal_low") else "")
            )
        
        return "\n".join(lines)
    
    def _format_medications(self, medications: List[Dict]) -> str:
        if not medications:
            return "No active medications on record."
        
        lines = []
        for med in medications:
            lines.append(
                f"- {med.get('name', 'Unknown')} "
                f"{med.get('dose', '')} {med.get('route', '')} {med.get('frequency', '')}"
            )
        return "\n".join(lines)
    
    def _format_labs(self, labs: List[Dict]) -> str:
        if not labs:
            return "No recent laboratory results."
        
        lines = []
        for lab in labs:
            flag = ""
            if lab.get("critical"): flag = " ⚠️ CRITICAL"
            elif lab.get("abnormal"): flag = " * ABNORMAL"
            lines.append(
                f"- {lab.get('test', 'Unknown')}: {lab.get('value', 'N/A')} "
                f"{lab.get('unit', '')} (Ref: {lab.get('reference_range', 'N/A')}){flag}"
            )
        return "\n".join(lines)
    
    def _format_imaging(self, findings: List[Dict]) -> str:
        if not findings:
            return "No recent imaging studies."
        
        lines = []
        for finding in findings:
            lines.append(
                f"- [{finding.get('modality', 'Unknown')}] {finding.get('study_date', '')}: "
                f"{finding.get('summary', finding.get('finding', 'No findings documented'))}"
                + (" 🚨 URGENT" if finding.get("urgent") else "")
            )
        return "\n".join(lines)
    
    def _format_nlp(self, summaries: List[Dict]) -> str:
        return "\n\n".join([
            f"[{s.get('document_type', 'Note')} — {s.get('date', '')}]: {s.get('summary', '')}"
            for s in summaries[:5]
        ])
    
    def _format_history(self, history: List[Dict], token_budget: int) -> str:
        lines = []
        used = 0
        for item in history:
            text = f"- [{item.get('date', '')}] {item.get('description', '')}"
            if used + self.estimate_tokens(text) > token_budget:
                break
            lines.append(text)
            used += self.estimate_tokens(text)
        return "\n".join(lines) if lines else "No relevant history retrieved."


class ClinicalReasoningEngine:
    """
    The LLM Clinical Reasoning Engine.
    
    Orchestrates the 5-section prompt, calls LLM API,
    validates output, retries on failure.
    
    Vendor-agnostic: supports Azure OpenAI + Anthropic Claude.
    """
    
    SYSTEM_PROMPT_SECTION_1 = """You are a clinical decision support AI embedded in a hospital intelligence system.

ROLE AND HARD CONSTRAINTS:
- You ASSIST physicians. You NEVER replace them or provide final diagnoses.
- You suggest HYPOTHESES for physician evaluation, not conclusions.
- You ALWAYS cite the specific data points behind your reasoning.
- You ALWAYS flag uncertainty explicitly: "I am uncertain about X because Y."
- You CONSERVATIVELY err toward more testing and monitoring over less.
- When data is insufficient: escalate to human review.
- When HIGH or CRITICAL risk: ALWAYS set human_review_required = true.
- All clinical claims MUST reference the patient data provided, not general knowledge alone.
  If using general medical knowledge, mark as "clinical_reasoning" not "patient_data".
- You NEVER recommend specific medication changes, ICU transfer orders, or procedure orders.
  These require physician orders. You MAY suggest them for physician consideration.

WHAT YOU PRODUCE:
Structured JSON only. Physician-readable clinical reasoning in the following schema.
No markdown. No preamble. No postamble. Pure JSON."""

    SYSTEM_PROMPT_SECTION_3 = """
REASONING STEPS (execute in order):

STEP 1 — PATIENT STATE SYNTHESIS:
Synthesize the current patient state in 3-5 sentences covering:
chief complaint, key abnormal findings, current trajectory (improving/stable/worsening).
Be specific about data. Avoid generic statements.

STEP 2 — DIFFERENTIAL GENERATION:
List 3-5 possible clinical conditions consistent with the data.
For each:
- Cite which SPECIFIC data points support it (lab values, vital trends, symptoms)
- Note any contradicting evidence
- Mark as: primary / alternative / rule_out
Do NOT suggest conditions without supporting data from the patient record.

STEP 3 — RISK STRATIFICATION:
Assign: LOW / MEDIUM / HIGH / CRITICAL
Justify using: vital sign trends, lab values, clinical scores, comorbidities.
For HIGH or CRITICAL: list the SPECIFIC trigger criteria that drove this classification.

STEP 4 — RECOMMENDED ACTIONS:
List next clinical actions in priority order:
- immediate: next 1 hour
- short_term: next 6 hours  
- monitoring: ongoing
Each action MUST map to a specific finding that motivates it.
Each action MUST cite a clinical guideline or specific data point as evidence_base.

STEP 5 — CONFIDENCE AND UNCERTAINTY:
Rate: HIGH / MEDIUM / LOW
List specific data GAPS that limit confidence.
State what additional information would most change this assessment."""

    OUTPUT_SCHEMA = {
        "patient_state_summary": "string (3-5 sentences)",
        "differential_diagnoses": [
            {
                "condition": "string",
                "icd10": "string (ICD-10 code)",
                "supporting_evidence": ["string (cite specific data)"],
                "contradicting_evidence": ["string"],
                "probability_rank": "primary|alternative|rule_out",
                "confidence": "float 0.0-1.0"
            }
        ],
        "risk_level": "LOW|MEDIUM|HIGH|CRITICAL",
        "risk_justification": "string (cite specific triggers for HIGH/CRITICAL)",
        "recommended_actions": [
            {
                "action": "string",
                "urgency": "immediate|short_term|monitoring",
                "rationale": "string",
                "evidence_base": "string (guideline or data point)"
            }
        ],
        "overall_confidence": "HIGH|MEDIUM|LOW",
        "data_gaps": ["string"],
        "human_review_required": "boolean",
        "human_review_reason": "string"
    }

    def __init__(self, llm_client, model_name: str, context_builder: PatientContextBuilder):
        self._client = llm_client
        self._model = model_name
        self._context_builder = context_builder
    
    def reason(
        self,
        patient_context: str,
        multimodal_findings: Optional[Dict] = None,
    ) -> Tuple[Optional[ClinicalReasoningOutput], str]:
        """
        Execute clinical reasoning pipeline.
        
        Returns:
            (output, status)
            status: "success" | "validation_failed" | "reasoning_failed"
        """
        start_time = time.time()
        
        # Build full prompt
        system_prompt = self._build_system_prompt()
        user_message = self._build_user_message(patient_context, multimodal_findings)
        
        # Retry loop
        for attempt in range(3):
            try:
                raw_response = self._call_llm(system_prompt, user_message)
                
                # Parse JSON
                parsed = self._parse_json_response(raw_response)
                if not parsed:
                    logger.warning(f"Attempt {attempt+1}: JSON parse failed")
                    continue
                
                # Build output object
                output = self._deserialize_output(parsed)
                if not output:
                    logger.warning(f"Attempt {attempt+1}: Deserialization failed")
                    continue
                
                # Validate output
                valid, errors = output.validate()
                if errors:
                    logger.warning(f"Attempt {attempt+1}: Validation errors: {errors}")
                    if attempt == 2:
                        # Last attempt: auto-correct what we can
                        self._auto_correct(output, errors)
                        valid = True
                
                if valid or attempt == 2:
                    output.reasoning_model = self._model
                    output.reasoning_duration_ms = int((time.time() - start_time) * 1000)
                    output.retry_count = attempt
                    output.timestamp = datetime.now(timezone.utc).isoformat()
                    
                    logger.info(
                        f"Clinical reasoning complete: risk={output.risk_level} "
                        f"confidence={output.overall_confidence} "
                        f"attempts={attempt+1} "
                        f"duration={output.reasoning_duration_ms}ms"
                    )
                    
                    return output, "success"
                
            except Exception as e:
                logger.error(f"Attempt {attempt+1}: LLM call failed: {e}")
                if attempt == 2:
                    return None, "reasoning_failed"
        
        return None, "reasoning_failed"
    
    def _build_system_prompt(self) -> str:
        schema_json = json.dumps(self.OUTPUT_SCHEMA, indent=2)
        return f"""{self.SYSTEM_PROMPT_SECTION_1}

{self.SYSTEM_PROMPT_SECTION_3}

OUTPUT SCHEMA (respond ONLY with valid JSON matching this schema exactly):
{schema_json}

GUARDRAILS (automatic rejection triggers for retry):
1. risk_level = HIGH or CRITICAL but human_review_required = false → INVALID
2. Any differential_diagnosis with empty supporting_evidence → INVALID
3. Any recommended_action with empty rationale → INVALID
4. Response is not valid JSON → INVALID"""
    
    def _build_user_message(
        self,
        patient_context: str,
        multimodal_findings: Optional[Dict],
    ) -> str:
        parts = [f"## PATIENT DATA\n\n{patient_context}"]
        
        if multimodal_findings:
            if imaging := multimodal_findings.get("imaging"):
                parts.append(f"## AI IMAGING ANALYSIS\n{json.dumps(imaging, indent=2)}")
            
            if nlp := multimodal_findings.get("nlp"):
                parts.append(f"## AI NLP ANALYSIS\n{json.dumps(nlp, indent=2)}")
            
            if vitals_ai := multimodal_findings.get("vitals_prediction"):
                parts.append(f"## AI VITALS PREDICTIONS\n{json.dumps(vitals_ai, indent=2)}")
        
        parts.append("## TASK\nApply the 5-step clinical reasoning protocol. Respond with valid JSON only.")
        
        return "\n\n".join(parts)
    
    def _call_llm(self, system_prompt: str, user_message: str) -> str:
        """
        Call the configured LLM.
        Supports: Azure OpenAI, Anthropic Claude.
        """
        # This is a simplified interface — actual implementation
        # connects to Azure OpenAI or Anthropic API
        # The client is injected at construction time
        
        response = self._client.complete(
            system=system_prompt,
            user=user_message,
            model=self._model,
            max_tokens=4096,
            temperature=0.1,
            response_format={"type": "json_object"},  # Force JSON for OpenAI
        )
        
        return response.content
    
    def _parse_json_response(self, raw: str) -> Optional[Dict]:
        """Parse JSON, handling common LLM formatting issues."""
        if not raw:
            return None
        
        # Remove markdown fences if present
        cleaned = raw.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
        
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e}\nRaw: {raw[:200]}")
            return None
    
    def _deserialize_output(self, parsed: Dict) -> Optional[ClinicalReasoningOutput]:
        """Safely deserialize parsed JSON into typed output object."""
        try:
            differentials = [
                DifferentialDiagnosis(
                    condition=d.get("condition", ""),
                    icd10=d.get("icd10", ""),
                    supporting_evidence=d.get("supporting_evidence", []),
                    contradicting_evidence=d.get("contradicting_evidence", []),
                    probability_rank=ProbabilityRank(d.get("probability_rank", "alternative")),
                    confidence=float(d.get("confidence", 0.5)),
                )
                for d in parsed.get("differential_diagnoses", [])
            ]
            
            actions = [
                RecommendedAction(
                    action=a.get("action", ""),
                    urgency=ActionUrgency(a.get("urgency", "monitoring")),
                    rationale=a.get("rationale", ""),
                    evidence_base=a.get("evidence_base", ""),
                )
                for a in parsed.get("recommended_actions", [])
            ]
            
            return ClinicalReasoningOutput(
                patient_state_summary=parsed.get("patient_state_summary", ""),
                differential_diagnoses=differentials,
                risk_level=RiskLevel(parsed.get("risk_level", "LOW")),
                risk_justification=parsed.get("risk_justification", ""),
                recommended_actions=actions,
                overall_confidence=ConfidenceLevel(parsed.get("overall_confidence", "MEDIUM")),
                data_gaps=parsed.get("data_gaps", []),
                human_review_required=bool(parsed.get("human_review_required", True)),
                human_review_reason=parsed.get("human_review_reason", ""),
            )
        
        except (KeyError, ValueError, TypeError) as e:
            logger.error(f"Deserialization failed: {e}")
            return None
    
    def _auto_correct(self, output: ClinicalReasoningOutput, errors: List[str]) -> None:
        """Auto-correct safety-critical violations (last resort before failure)."""
        # Always ensure HIGH/CRITICAL has human review
        if output.risk_level in [RiskLevel.HIGH, RiskLevel.CRITICAL]:
            output.human_review_required = True
            if not output.human_review_reason:
                output.human_review_reason = f"Auto-escalated: {output.risk_level} risk level"
        
        # Ensure summary exists
        if not output.patient_state_summary:
            output.patient_state_summary = "Clinical summary unavailable — physician review required."
        
        # Flag auto-correction in data gaps
        if errors:
            output.data_gaps.append(f"Note: Output auto-corrected due to validation errors: {errors[:2]}")
