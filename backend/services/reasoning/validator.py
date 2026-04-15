"""
Output Validator — Strict schema enforcement for LLM clinical outputs.
Every output is validated before being shown to a physician.
Invalid outputs trigger retry (max 3), then graceful failure.
"""
import json
from typing import Dict, List, Tuple, Any, Optional


class OutputValidator:
    """
    Validates LLM clinical reasoning output against the required schema.
    Catches: missing fields, wrong types, safety-critical violations.
    """

    REQUIRED_FIELDS = {
        "patient_state_summary", "differential_diagnoses", "risk_level",
        "risk_justification", "recommended_actions", "overall_confidence",
        "data_gaps", "human_review_required", "human_review_reason",
    }
    VALID_RISK_LEVELS    = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    VALID_CONFIDENCE     = {"LOW", "MEDIUM", "HIGH"}
    VALID_URGENCY        = {"immediate", "short_term", "monitoring"}
    VALID_RANK           = {"primary", "alternative", "rule_out"}
    HIGH_RISK_LEVELS     = {"HIGH", "CRITICAL"}

    def validate(self, output: Dict) -> Tuple[bool, List[str]]:
        errors: List[str] = []

        # 1. Required top-level fields
        for field in self.REQUIRED_FIELDS:
            if field not in output:
                errors.append(f"Missing required field: {field}")

        if errors:
            return False, errors

        # 2. Enumerations
        if output.get("risk_level") not in self.VALID_RISK_LEVELS:
            errors.append(f"Invalid risk_level: {output.get('risk_level')}")
        if output.get("overall_confidence") not in self.VALID_CONFIDENCE:
            errors.append(f"Invalid overall_confidence: {output.get('overall_confidence')}")

        # 3. Guardrail: HIGH/CRITICAL must have human_review_required=True
        if output.get("risk_level") in self.HIGH_RISK_LEVELS:
            if not output.get("human_review_required"):
                errors.append("GUARDRAIL: HIGH/CRITICAL risk but human_review_required=False")
                output["human_review_required"] = True   # Auto-correct safety violation
                output["human_review_reason"] = output.get("human_review_reason") or f"Auto-escalated: {output.get('risk_level')} risk"

        # 4. Differentials
        for i, dx in enumerate(output.get("differential_diagnoses", [])):
            if not dx.get("condition"):
                errors.append(f"differential[{i}] missing condition")
            if not dx.get("supporting_evidence"):
                errors.append(f"GUARDRAIL: differential[{i}] '{dx.get('condition')}' has no supporting_evidence")
            if dx.get("probability_rank") not in self.VALID_RANK:
                errors.append(f"differential[{i}] invalid probability_rank")
            conf = dx.get("confidence")
            if conf is not None and not (0.0 <= float(conf) <= 1.0):
                errors.append(f"differential[{i}] confidence out of range: {conf}")

        # 5. Recommended actions
        for i, action in enumerate(output.get("recommended_actions", [])):
            if not action.get("action"):
                errors.append(f"action[{i}] missing action field")
            if not action.get("rationale"):
                errors.append(f"GUARDRAIL: action[{i}] '{action.get('action')}' missing rationale")
            if action.get("urgency") not in self.VALID_URGENCY:
                errors.append(f"action[{i}] invalid urgency: {action.get('urgency')}")

        # 6. Summary length
        summary = output.get("patient_state_summary", "")
        if len(summary.split()) < 15:
            errors.append(f"patient_state_summary too short ({len(summary.split())} words, min 15)")

        return len(errors) == 0, errors

    def parse_json(self, raw: str) -> Optional[Dict]:
        """Parse JSON from LLM response, handling common formatting issues."""
        cleaned = raw.strip()
        # Strip markdown code fences
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            # Try to find JSON object in response
            start = cleaned.find("{")
            end = cleaned.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(cleaned[start:end])
                except Exception:
                    pass
        return None

    def auto_correct(self, output: Dict, errors: List[str]) -> Dict:
        """Apply safe auto-corrections for recoverable violations."""
        # Always enforce human review for high risk
        if output.get("risk_level") in self.HIGH_RISK_LEVELS:
            output["human_review_required"] = True
            if not output.get("human_review_reason"):
                output["human_review_reason"] = f"{output['risk_level']} risk — physician review mandatory"

        # Ensure summary exists
        if not output.get("patient_state_summary"):
            output["patient_state_summary"] = "Clinical summary unavailable — data insufficient or model error. Physician review required."

        # Ensure data_gaps is a list
        if not isinstance(output.get("data_gaps"), list):
            output["data_gaps"] = ["Model output incomplete — see physician review reason"]

        # Flag auto-correction in data gaps
        if errors:
            output.setdefault("data_gaps", []).append(
                f"Note: AI output auto-corrected due to validation errors: {errors[:2]}"
            )

        return output




class ContextManager:
    """
    Manages patient context injection for the LLM reasoning engine.
    Priority scoring: score = 0.40 * recency + 0.35 * severity + 0.25 * semantic_similarity
    """

    MAX_CONTEXT_TOKENS = 128_000
    STATIC_SECTION_TOKENS = 1_600
    RESPONSE_RESERVED = 4_096
    PATIENT_CONTEXT_BUDGET = MAX_CONTEXT_TOKENS - STATIC_SECTION_TOKENS - RESPONSE_RESERVED

    RECENCY_WEIGHT = 0.40
    SEVERITY_WEIGHT = 0.35
    SIMILARITY_WEIGHT = 0.25

    def estimate_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)

    def priority_score(self, hours_ago: float, severity: float, similarity: float) -> float:
        import math
        recency_score = math.exp(-hours_ago / 6)
        return (
            self.RECENCY_WEIGHT * recency_score
            + self.SEVERITY_WEIGHT * severity
            + self.SIMILARITY_WEIGHT * similarity
        )

    def build_patient_context(
        self,
        current_vitals,
        medications,
        recent_labs,
        imaging_findings,
        nlp_summaries,
        historical_context,
        chief_complaint: str = "",
        admission_hours_ago: float = 0,
    ):
        import json
        sections = []
        budget = self.PATIENT_CONTEXT_BUDGET

        vitals_text = self._format_vitals(current_vitals)
        if self.estimate_tokens(vitals_text) <= budget:
            sections.append(("CURRENT VITALS (Last 2 Hours)", vitals_text))
            budget -= self.estimate_tokens(vitals_text)

        meds_text = self._format_medications(medications)
        if self.estimate_tokens(meds_text) <= budget:
            sections.append(("ACTIVE MEDICATIONS", meds_text))
            budget -= self.estimate_tokens(meds_text)

        labs_text = self._format_labs(recent_labs)
        if self.estimate_tokens(labs_text) <= budget:
            sections.append(("RECENT LABORATORY RESULTS (24h)", labs_text))
            budget -= self.estimate_tokens(labs_text)

        if imaging_findings and budget > 1000:
            imaging_text = self._format_imaging(imaging_findings)
            sections.append(("IMAGING FINDINGS", imaging_text))
            budget -= self.estimate_tokens(imaging_text)

        if nlp_summaries and budget > 1000:
            nlp_text = self._format_nlp(nlp_summaries)
            sections.append(("CLINICAL NLP SUMMARY", nlp_text))
            budget -= self.estimate_tokens(nlp_text)

        if historical_context and budget > 500:
            hist_text = self._format_history(historical_context, budget)
            sections.append(("RELEVANT HISTORY", hist_text))

        header = f"**Chief Complaint**: {chief_complaint}\n\n" if chief_complaint else ""
        body = "\n\n".join(f"### {title}\n{content}" for title, content in sections)
        token_usage = {title: self.estimate_tokens(content) for title, content in sections}
        return header + body, token_usage

    def _format_vitals(self, vitals):
        if not vitals:
            return "No recent vitals recorded."
        lines = []
        for v in vitals[-20:]:
            param = v.get("parameter", "Unknown")
            value = v.get("value", "N/A")
            unit = v.get("unit", "")
            ts = str(v.get("timestamp", v.get("time", "")))[:16]
            flags = " ⚠️ CRITICAL" if v.get("is_critical") else ""
            lines.append(f"- {param}: {value} {unit} at {ts}{flags}")
        return "\n".join(lines)

    def _format_medications(self, meds):
        if not meds:
            return "No active medications."
        return "\n".join(
            f"- {m.get('name','Unknown')} {m.get('dose','')} {m.get('route','')} {m.get('frequency','')}"
            for m in meds
        )

    def _format_labs(self, labs):
        if not labs:
            return "No recent laboratory results."
        lines = []
        for lab in labs:
            flag = " ⚠️ CRITICAL" if lab.get("is_critical") else ""
            ref = lab.get("reference_range", "N/A")
            lines.append(f"- {lab.get('test','Unknown')}: {lab.get('value','N/A')} {lab.get('unit','')} (Ref: {ref}){flag}")
        return "\n".join(lines)

    def _format_imaging(self, findings):
        if not findings:
            return "No recent imaging."
        return "\n".join(
            f"- [{f.get('modality','Imaging')} {f.get('date','')}]: {f.get('summary', f.get('finding',''))}"
            + (" 🚨 URGENT" if f.get("urgent") else "")
            for f in findings
        )

    def _format_nlp(self, summaries):
        return "\n\n".join(
            f"[{s.get('document_type','Note')} — {s.get('date','')}]: {s.get('summary','')}"
            for s in summaries[:5]
        )

    def _format_history(self, history, budget):
        lines = []
        used = 0
        for item in history:
            line = f"- [{item.get('date','')}] {item.get('description','')}"
            line_tokens = self.estimate_tokens(line)
            if used + line_tokens > budget:
                break
            lines.append(line)
            used += line_tokens
        return "\n".join(lines) if lines else "No relevant history retrieved."
