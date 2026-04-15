"""
Cross-Modal Fusion Engine (Layer 3)
=====================================
Combines outputs from:
  - Imaging AI (MONAI/BioViL-T)
  - Clinical NLP (BioMedBERT)
  - Vitals AI (Temporal Fusion Transformer)

CRITICAL DESIGN RULE:
When findings from two modalities CONTRADICT each other:
  → Flag as "clinical-imaging discordance"
  → Escalate to physician review
  → Do NOT let LLM reasoning resolve contradictions autonomously
  → LLM reasoning engine CONSUMES unified fusion output (not raw modalities)

Examples of contradictions that must be caught:
  - Imaging: "No pneumonia" + NLP: "physician suspects pneumonia" → DISCORDANCE
  - Imaging: "Clear lungs" + Vitals: SpO2=86%, RR=32 → DISCORDANCE
  - NLP: "Improving" + Vitals: NEWS2=8, deteriorating trend → DISCORDANCE
"""

import logging
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger(__name__)


class ModalitySource(str, Enum):
    IMAGING  = "imaging"
    NLP      = "nlp"
    VITALS   = "vitals"
    LABS     = "labs"


class FindingSeverity(str, Enum):
    INCIDENTAL = "incidental"
    MILD       = "mild"
    MODERATE   = "moderate"
    SEVERE     = "severe"
    CRITICAL   = "critical"


class DiscordanceType(str, Enum):
    CLINICAL_IMAGING   = "clinical-imaging discordance"
    IMAGING_VITALS     = "imaging-vitals discordance"
    NLP_VITALS         = "nlp-vitals discordance"
    MULTI_MODAL        = "multi-modal discordance"


@dataclass
class ModalityFinding:
    """A single finding from one AI modality."""
    source: ModalitySource
    finding_type: str           # "pneumonia", "sepsis_risk", "deterioration", etc.
    description: str
    severity: Optional[FindingSeverity]
    confidence: float           # 0.0–1.0
    uncertainty: Optional[float] = None
    supporting_data: Dict = field(default_factory=dict)
    timestamp: str = ""
    negated: bool = False       # "No pneumonia" → negated=True


@dataclass
class Discordance:
    """A detected contradiction between modality findings."""
    discordance_type: DiscordanceType
    finding_a: ModalityFinding
    finding_b: ModalityFinding
    description: str
    clinical_significance: str  # "low"|"medium"|"high"|"critical"
    requires_physician_review: bool = True
    auto_resolvable: bool = False   # ALWAYS False — safety design


@dataclass
class UnifiedFinding:
    """
    The fusion output consumed by the LLM reasoning engine.

    This is the single coherent representation of all AI modality outputs.
    Contradictions are explicitly flagged — never silently resolved.
    """
    patient_deident_id: str
    timestamp: str

    # Aggregated findings from all modalities
    all_findings: List[ModalityFinding]

    # Discordances detected
    discordances: List[Discordance]

    # Unified risk assessment (confidence-weighted from all modalities)
    overall_risk_level: str         # CRITICAL|HIGH|MEDIUM|LOW
    overall_confidence: float
    requires_human_review: bool

    # Per-modality summaries
    imaging_summary: Optional[str] = None
    nlp_summary: Optional[str] = None
    vitals_summary: Optional[str] = None

    # Top findings (sorted by severity × confidence)
    top_findings: List[ModalityFinding] = field(default_factory=list)

    # Urgent flags
    imaging_urgent: bool = False
    sepsis_alert: bool = False
    deterioration_alert: bool = False

    # Processing metadata
    modalities_available: List[str] = field(default_factory=list)
    missing_modalities: List[str] = field(default_factory=list)
    fusion_version: str = "1.0.0"


class DiscordanceDetector:
    """
    Detects clinically significant contradictions between modality outputs.

    Rule-based detection (not ML) — must be deterministic and auditable.
    Each rule is documented with clinical rationale.
    """

    # Condition synonyms (for cross-modality matching)
    CONDITION_SYNONYMS = {
        "pneumonia": {"pneumonia", "consolidation", "infiltrate", "airspace disease", "infection"},
        "pleural_effusion": {"pleural effusion", "fluid", "effusion"},
        "pulmonary_edema": {"pulmonary edema", "fluid overload", "congestion", "wet lungs"},
        "pneumothorax": {"pneumothorax", "ptx", "collapsed lung"},
        "heart_failure": {"heart failure", "chf", "cardiac failure", "decompensated"},
    }

    def detect(
        self,
        findings: List[ModalityFinding],
    ) -> List[Discordance]:
        """Find all contradictions between modality findings."""
        discordances = []

        # Check all pairs of findings
        for i, finding_a in enumerate(findings):
            for finding_b in findings[i + 1:]:
                # Skip same-modality comparisons
                if finding_a.source == finding_b.source:
                    continue

                discord = self._check_pair(finding_a, finding_b)
                if discord:
                    discordances.append(discord)

        return discordances

    def _check_pair(
        self,
        a: ModalityFinding,
        b: ModalityFinding,
    ) -> Optional[Discordance]:
        """Check if two findings from different modalities contradict."""

        # Rule 1: Imaging says NO condition, clinical note says YES
        # Example: CXR "No pneumonia" + Note "Suspected pneumonia"
        if a.source == ModalitySource.IMAGING and b.source == ModalitySource.NLP:
            return self._check_imaging_nlp_discord(a, b)
        if b.source == ModalitySource.IMAGING and a.source == ModalitySource.NLP:
            return self._check_imaging_nlp_discord(b, a)

        # Rule 2: Imaging says normal, vitals say critically abnormal
        if a.source == ModalitySource.IMAGING and b.source == ModalitySource.VITALS:
            return self._check_imaging_vitals_discord(a, b)
        if b.source == ModalitySource.IMAGING and a.source == ModalitySource.VITALS:
            return self._check_imaging_vitals_discord(b, a)

        # Rule 3: NLP says improving, vitals say deteriorating
        if a.source == ModalitySource.NLP and b.source == ModalitySource.VITALS:
            return self._check_nlp_vitals_discord(a, b)
        if b.source == ModalitySource.NLP and a.source == ModalitySource.VITALS:
            return self._check_nlp_vitals_discord(b, a)

        return None

    def _check_imaging_nlp_discord(
        self,
        imaging: ModalityFinding,
        nlp: ModalityFinding,
    ) -> Optional[Discordance]:
        """
        Detect: Imaging negates condition, NLP affirms it (or vice versa).

        Rule rationale: CXR can miss early pneumonia (sensitivity 60-70%).
        If imaging is negative but clinical picture strongly suggests infection,
        this needs physician adjudication — not algorithmic resolution.
        """
        # Find shared condition
        for condition, synonyms in self.CONDITION_SYNONYMS.items():
            imaging_mentions = any(s in imaging.description.lower() for s in synonyms)
            nlp_mentions = any(s in nlp.description.lower() for s in synonyms)

            if imaging_mentions and nlp_mentions:
                imaging_positive = not imaging.negated
                nlp_positive = not nlp.negated

                if imaging_positive != nlp_positive:
                    return Discordance(
                        discordance_type=DiscordanceType.CLINICAL_IMAGING,
                        finding_a=imaging,
                        finding_b=nlp,
                        description=(
                            f"Imaging {'confirms' if imaging_positive else 'denies'} {condition} "
                            f"but clinical note {'suggests' if nlp_positive else 'denies'} it. "
                            f"Physician adjudication required."
                        ),
                        clinical_significance="high",
                        requires_physician_review=True,
                        auto_resolvable=False,
                    )

        return None

    def _check_imaging_vitals_discord(
        self,
        imaging: ModalityFinding,
        vitals: ModalityFinding,
    ) -> Optional[Discordance]:
        """
        Detect: Imaging shows clear lungs but vitals show severe hypoxemia.

        Rule rationale: SpO2 < 88% + RR > 30 with "clear CXR" is
        highly discordant — possible ARDS, PE, or imaging miss.
        """
        # Check if imaging says "normal/clear" for respiratory conditions
        imaging_normal = any(w in imaging.description.lower() for w in
                           ["clear", "no acute", "unremarkable", "normal", "no evidence"])

        # Check if vitals show severe respiratory compromise
        supporting = vitals.supporting_data
        spo2 = supporting.get("spo2", 100)
        rr = supporting.get("respiratory_rate", 16)
        news2 = supporting.get("news2_score", 0)

        severely_compromised = spo2 < 90 or rr > 28 or news2 >= 7

        if imaging_normal and severely_compromised:
            return Discordance(
                discordance_type=DiscordanceType.IMAGING_VITALS,
                finding_a=imaging,
                finding_b=vitals,
                description=(
                    f"Imaging appears normal/unremarkable but vitals show severe compromise "
                    f"(SpO2={spo2}%, RR={rr}, NEWS2={news2}). "
                    f"Consider: PE, early ARDS, imaging timing, or positioning artifact."
                ),
                clinical_significance="critical",
                requires_physician_review=True,
                auto_resolvable=False,
            )

        return None

    def _check_nlp_vitals_discord(
        self,
        nlp: ModalityFinding,
        vitals: ModalityFinding,
    ) -> Optional[Discordance]:
        """
        Detect: Clinical note says "improving" but vitals say deteriorating.

        This catches cases where note-writing lags behind real-time physiology.
        """
        note_improving = any(w in nlp.description.lower() for w in
                           ["improving", "better", "improved", "stable", "resolving"])

        vitals_support = vitals.supporting_data
        news2 = vitals_support.get("news2_score", 0)
        trend = vitals_support.get("trend", "stable")
        deterioration_prob = vitals_support.get("deterioration_6h", 0)

        vitals_worsening = news2 >= 5 or trend == "worsening" or deterioration_prob > 0.70

        if note_improving and vitals_worsening:
            return Discordance(
                discordance_type=DiscordanceType.NLP_VITALS,
                finding_a=nlp,
                finding_b=vitals,
                description=(
                    f"Clinical note suggests improvement but vitals indicate deterioration "
                    f"(NEWS2={news2}, trend={trend}, deterioration_probability={deterioration_prob:.0%}). "
                    f"Note may not reflect current clinical status."
                ),
                clinical_significance="high",
                requires_physician_review=True,
                auto_resolvable=False,
            )

        return None


class CrossModalFusionEngine:
    """
    Fuses outputs from all AI modalities into a unified finding.

    Design: confidence-weighted aggregation with explicit discordance flagging.
    Never silently resolves contradictions — always surfaces them.

    The unified output feeds the LLM reasoning engine.
    """

    # Modality confidence weights for risk aggregation
    MODALITY_WEIGHTS = {
        ModalitySource.VITALS:  0.40,   # Real-time, objective
        ModalitySource.LABS:    0.25,   # Quantitative, objective
        ModalitySource.IMAGING: 0.20,   # Structural, slightly delayed
        ModalitySource.NLP:     0.15,   # Subjective, note-writing lag
    }

    def __init__(self):
        self._discordance_detector = DiscordanceDetector()

    def fuse(
        self,
        patient_deident_id: str,
        vitals_output: Optional[Dict] = None,
        nlp_outputs: Optional[List[Dict]] = None,
        imaging_output: Optional[Dict] = None,
        labs: Optional[List[Dict]] = None,
    ) -> UnifiedFinding:
        """
        Fuse all modality outputs into unified representation.

        All parameters optional — fusion handles missing modalities gracefully.
        Missing modality → noted in missing_modalities, reduces confidence.
        """
        all_findings: List[ModalityFinding] = []
        modalities_available = []
        modalities_missing = []

        # ── Vitals Modality ───────────────────────────────────
        if vitals_output:
            modalities_available.append("vitals")
            vitals_findings = self._extract_vitals_findings(vitals_output)
            all_findings.extend(vitals_findings)
        else:
            modalities_missing.append("vitals")

        # ── NLP Modality ──────────────────────────────────────
        if nlp_outputs:
            modalities_available.append("nlp")
            nlp_findings = self._extract_nlp_findings(nlp_outputs)
            all_findings.extend(nlp_findings)
        else:
            modalities_missing.append("nlp")

        # ── Imaging Modality ──────────────────────────────────
        if imaging_output:
            modalities_available.append("imaging")
            imaging_findings = self._extract_imaging_findings(imaging_output)
            all_findings.extend(imaging_findings)
        else:
            modalities_missing.append("imaging")

        # ── Lab Modality ──────────────────────────────────────
        if labs:
            modalities_available.append("labs")
            lab_findings = self._extract_lab_findings(labs)
            all_findings.extend(lab_findings)
        else:
            modalities_missing.append("labs")

        # ── Discordance Detection ─────────────────────────────
        discordances = self._discordance_detector.detect(all_findings)
        has_critical_discordance = any(
            d.clinical_significance == "critical" for d in discordances
        )

        # ── Risk Aggregation ──────────────────────────────────
        overall_risk, overall_confidence = self._aggregate_risk(all_findings, modalities_available)

        # Discordances always elevate to at least HIGH
        if discordances and overall_risk not in ["HIGH", "CRITICAL"]:
            overall_risk = "HIGH"

        # ── Top Findings (by severity × confidence) ───────────
        def finding_priority(f: ModalityFinding) -> float:
            sev_weight = {"critical": 4, "severe": 3, "moderate": 2, "mild": 1, "incidental": 0}
            sev = sev_weight.get(f.severity.value if f.severity else "incidental", 0)
            return sev * f.confidence

        top_findings = sorted(
            [f for f in all_findings if not f.negated],
            key=finding_priority,
            reverse=True,
        )[:5]

        # ── Summaries ─────────────────────────────────────────
        return UnifiedFinding(
            patient_deident_id=patient_deident_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            all_findings=all_findings,
            discordances=discordances,
            overall_risk_level=overall_risk,
            overall_confidence=overall_confidence,
            requires_human_review=(
                has_critical_discordance or
                overall_risk in ["HIGH", "CRITICAL"] or
                bool(discordances)
            ),
            imaging_summary=self._summarize_imaging(imaging_output) if imaging_output else None,
            nlp_summary=self._summarize_nlp(nlp_outputs) if nlp_outputs else None,
            vitals_summary=self._summarize_vitals(vitals_output) if vitals_output else None,
            top_findings=top_findings,
            imaging_urgent=imaging_output.get("urgent_flag", False) if imaging_output else False,
            sepsis_alert=(vitals_output or {}).get("ai_predictions", {}).get("sepsis_12h", 0) > 0.50,
            deterioration_alert=(vitals_output or {}).get("ai_predictions", {}).get("deterioration_6h", 0) > 0.70,
            modalities_available=modalities_available,
            missing_modalities=modalities_missing,
        )

    def _extract_vitals_findings(self, vitals: Dict) -> List[ModalityFinding]:
        findings = []
        predictions = vitals.get("ai_predictions", {})
        news2 = vitals.get("news2_score", 0)
        trend = vitals.get("trend", "stable")

        if predictions.get("deterioration_6h", 0) > 0.50:
            findings.append(ModalityFinding(
                source=ModalitySource.VITALS,
                finding_type="deterioration_risk",
                description=f"AI deterioration probability {predictions['deterioration_6h']:.0%} in 6 hours",
                severity=FindingSeverity.CRITICAL if predictions["deterioration_6h"] > 0.70 else FindingSeverity.MODERATE,
                confidence=1.0 - vitals.get("deterioration_uncertainty", 0.1),
                uncertainty=vitals.get("deterioration_uncertainty"),
                supporting_data={
                    "news2_score": news2,
                    "trend": trend,
                    "deterioration_6h": predictions.get("deterioration_6h"),
                    "spo2": vitals.get("latest_vitals", {}).get("spo2_pulse_ox"),
                    "respiratory_rate": vitals.get("latest_vitals", {}).get("respiratory_rate"),
                },
            ))

        if predictions.get("sepsis_12h", 0) > 0.40:
            findings.append(ModalityFinding(
                source=ModalitySource.VITALS,
                finding_type="sepsis_risk",
                description=f"AI sepsis probability {predictions['sepsis_12h']:.0%} in 12 hours",
                severity=FindingSeverity.CRITICAL if predictions["sepsis_12h"] > 0.60 else FindingSeverity.SEVERE,
                confidence=1.0 - vitals.get("sepsis_uncertainty", 0.1),
                uncertainty=vitals.get("sepsis_uncertainty"),
                supporting_data={"sepsis_12h": predictions.get("sepsis_12h"), "news2_score": news2},
            ))

        return findings

    def _extract_nlp_findings(self, nlp_outputs: List[Dict]) -> List[ModalityFinding]:
        findings = []
        for doc in nlp_outputs:
            for entity in doc.get("entities", []):
                if entity.get("type") in ["DISEASE", "FINDING"] and not entity.get("negated"):
                    findings.append(ModalityFinding(
                        source=ModalitySource.NLP,
                        finding_type=entity.get("type", "").lower(),
                        description=entity.get("text", ""),
                        severity=None,
                        confidence=entity.get("confidence", 0.8),
                        negated=entity.get("negated", False),
                    ))
        return findings

    def _extract_imaging_findings(self, imaging: Dict) -> List[ModalityFinding]:
        findings = []
        for finding in imaging.get("findings", []):
            findings.append(ModalityFinding(
                source=ModalitySource.IMAGING,
                finding_type=finding.get("finding", ""),
                description=f"[{finding.get('region', '')}] {finding.get('finding', '')}",
                severity=FindingSeverity(finding.get("severity", "incidental")) if finding.get("severity") in [s.value for s in FindingSeverity] else FindingSeverity.INCIDENTAL,
                confidence=finding.get("confidence", 0.8),
                uncertainty=finding.get("uncertainty"),
                supporting_data={"icd10_codes": finding.get("icd10_codes", [])},
                negated=False,
            ))
        return findings

    def _extract_lab_findings(self, labs: List[Dict]) -> List[ModalityFinding]:
        findings = []
        for lab in labs:
            if lab.get("is_critical") or lab.get("abnormal_flag") in ["HH", "LL"]:
                findings.append(ModalityFinding(
                    source=ModalitySource.LABS,
                    finding_type="critical_lab",
                    description=f"Critical lab: {lab.get('test', '')}={lab.get('value', '')} {lab.get('unit', '')}",
                    severity=FindingSeverity.CRITICAL,
                    confidence=0.99,
                    supporting_data={"loinc_code": lab.get("loinc_code")},
                ))
        return findings

    def _aggregate_risk(
        self,
        findings: List[ModalityFinding],
        modalities: List[str],
    ) -> tuple[str, float]:
        """Confidence-weighted risk aggregation across modalities."""
        if not findings:
            return "LOW", 0.5

        severity_scores = {
            "critical": 4, "severe": 3, "moderate": 2, "mild": 1, "incidental": 0
        }

        total_weight = 0.0
        weighted_severity = 0.0

        for f in findings:
            if f.negated:
                continue
            sev = severity_scores.get(f.severity.value if f.severity else "incidental", 0)
            weight = self.MODALITY_WEIGHTS.get(f.source, 0.10) * f.confidence
            weighted_severity += sev * weight
            total_weight += weight

        if total_weight == 0:
            return "LOW", 0.5

        avg_severity = weighted_severity / total_weight
        confidence = min(0.95, total_weight / len(modalities)) if modalities else 0.5

        # Reduce confidence for missing modalities
        confidence *= (len(modalities) / 4.0)  # 4 = max modalities

        if avg_severity >= 3.0:
            return "CRITICAL", confidence
        elif avg_severity >= 2.0:
            return "HIGH", confidence
        elif avg_severity >= 1.0:
            return "MEDIUM", confidence
        else:
            return "LOW", confidence

    def _summarize_vitals(self, vitals: Dict) -> str:
        news2 = vitals.get("news2_score", 0)
        trend = vitals.get("trend", "stable")
        preds = vitals.get("ai_predictions", {})
        return (
            f"NEWS2={news2}, trend={trend}. "
            f"AI: deterioration {preds.get('deterioration_6h', 0):.0%}/6h, "
            f"sepsis {preds.get('sepsis_12h', 0):.0%}/12h."
        )

    def _summarize_nlp(self, nlp_outputs: List[Dict]) -> str:
        summaries = [doc.get("summary", "") for doc in nlp_outputs if doc.get("summary")]
        return " | ".join(summaries[:2]) if summaries else "No clinical notes analyzed."

    def _summarize_imaging(self, imaging: Dict) -> str:
        findings = imaging.get("findings", [])
        if not findings:
            return "No significant imaging findings."
        top = findings[:2]
        return "; ".join(f"{f.get('region', '')}: {f.get('finding', '')}" for f in top)
