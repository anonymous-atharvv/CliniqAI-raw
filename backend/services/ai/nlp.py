"""
Clinical NLP Pipeline (Layer 3 — Multi-Modal AI)
=================================================
Uses: BioMedBERT / GatorTron fine-tuned for clinical NER

Tasks:
  a) Named Entity Recognition — extract diseases, symptoms, medications,
     procedures, anatomical locations, lab values
  b) Relation Extraction — link entities (medication → dosage → frequency)
  c) Temporal Reasoning — extract event timelines from notes
  d) ICD-10 Code Suggestion — from free-text diagnoses
  e) Semantic De-duplication — cosine similarity > 0.92 = duplicate, skip

Key design: Negation detection is CRITICAL.
"No fever" ≠ "Fever". Missing this kills clinical accuracy.
"""

import re
import logging
import hashlib
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Entity Types
# ─────────────────────────────────────────────

class EntityType(str, Enum):
    DISEASE       = "DISEASE"
    SYMPTOM       = "SYMPTOM"
    MEDICATION    = "MEDICATION"
    PROCEDURE     = "PROCEDURE"
    LAB           = "LAB"
    VITAL         = "VITAL"
    ANATOMY       = "ANATOMY"
    FINDING       = "FINDING"
    SEVERITY      = "SEVERITY"
    TEMPORAL      = "TEMPORAL"


class DocumentType(str, Enum):
    PROGRESS_NOTE   = "progress_note"
    DISCHARGE       = "discharge"
    RADIOLOGY       = "radiology"
    PATHOLOGY       = "pathology"
    NURSING         = "nursing"
    CONSULT         = "consult"
    OPERATIVE       = "operative"
    EMERGENCY       = "emergency"


class ClinicalSentiment(str, Enum):
    IMPROVING       = "improving"
    STABLE          = "stable"
    DETERIORATING   = "deteriorating"


# ─────────────────────────────────────────────
# Output Schemas
# ─────────────────────────────────────────────

@dataclass
class ClinicalEntity:
    """A clinical entity extracted from text."""
    text: str                                   # Surface form in text
    entity_type: EntityType
    normalized: Optional[str]                   # SNOMED CT concept ID
    start_char: int
    end_char: int
    confidence: float                           # 0.0–1.0

    # Modifiers
    negated: bool = False                       # "no fever" → negated=True
    uncertainty_marker: bool = False            # "possible", "suspected"
    temporal: str = "current"                   # current|historical|family_history
    severity: Optional[str] = None             # mild|moderate|severe|critical

    # Relations
    related_entities: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "text": self.text,
            "type": self.entity_type.value,
            "normalized": self.normalized,
            "negated": self.negated,
            "uncertainty_marker": self.uncertainty_marker,
            "temporal": self.temporal,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class ClinicalNLPOutput:
    """Complete NLP analysis output for a clinical document."""
    document_id: str
    document_type: DocumentType
    document_date: Optional[str]
    patient_deident_id: str

    # Extracted entities
    entities: List[ClinicalEntity]

    # Derived outputs
    summary: str                                # 2-3 sentence clinical summary
    sentiment: ClinicalSentiment

    # ICD-10 suggestions
    suggested_icd10_codes: List[Dict]

    # De-duplication hash (for cross-note dedup)
    semantic_hash: Optional[str] = None

    # Processing metadata
    model_name: str = "biomedbert-ner-v1"
    processing_ms: int = 0
    token_count: int = 0

    @property
    def active_diagnoses(self) -> List[ClinicalEntity]:
        return [e for e in self.entities
                if e.entity_type == EntityType.DISEASE
                and not e.negated and e.temporal == "current"]

    @property
    def active_symptoms(self) -> List[ClinicalEntity]:
        return [e for e in self.entities
                if e.entity_type == EntityType.SYMPTOM
                and not e.negated and e.temporal == "current"]

    @property
    def current_medications(self) -> List[ClinicalEntity]:
        return [e for e in self.entities
                if e.entity_type == EntityType.MEDICATION
                and not e.negated]


# ─────────────────────────────────────────────
# Negation Detector
# ─────────────────────────────────────────────

class NegationDetector:
    """
    Clinical negation detection using rule-based triggers.

    CRITICAL for accuracy: "no fever", "denies chest pain",
    "ruled out sepsis" — all negated clinical findings.

    Production: NegEx algorithm or BioMedBERT-NegEx fine-tuned model.
    This implementation: rule-based for reliability + speed.
    """

    # Negation triggers (appear BEFORE the entity)
    PRE_NEGATION = [
        r"\bno\b", r"\bnot\b", r"\bdenies?\b", r"\bwithout\b",
        r"\babsence of\b", r"\bfree of\b", r"\bnegative for\b",
        r"\brule[sd]? out\b", r"\brunning out\b", r"\bno evidence of\b",
        r"\bunremarkable\b", r"\bno sign of\b", r"\bno signs of\b",
        r"\bno history of\b", r"\bno complaints? of\b",
    ]

    # Negation triggers (appear AFTER the entity)
    POST_NEGATION = [
        r"\bwas ruled out\b", r"\bnot present\b", r"\bwas absent\b",
        r"\bnot detected\b", r"\bnot found\b", r"\bwas negative\b",
    ]

    # Uncertainty triggers
    UNCERTAINTY = [
        r"\bpossible\b", r"\bpossibly\b", r"\bprobable\b", r"\bprobably\b",
        r"\bsuspected\b", r"\bsuspect\b", r"\blikely\b", r"\bcould be\b",
        r"\bmay be\b", r"\bmight be\b", r"\bquestion of\b", r"\bconcern for\b",
        r"\brule out\b", r"\bworking diagnosis\b", r"\bdifferential includes\b",
    ]

    # Temporal modifiers
    HISTORICAL = [
        r"\bhistory of\b", r"\bpast history of\b", r"\bpreviously\b",
        r"\bprior\b", r"\bold\b.*\bhistory\b", r"\bin the past\b",
        r"\bformerly\b", r"\bpast medical history\b",
    ]

    FAMILY_HISTORY = [
        r"\bfamily history of\b", r"\bfather had\b", r"\bmother had\b",
        r"\bsister had\b", r"\bbrother had\b", r"\bfamilial\b",
    ]

    def __init__(self, window_tokens: int = 5):
        """window_tokens: how many tokens before/after to check for negation."""
        self.window = window_tokens

        self._pre_neg_patterns = [re.compile(p, re.IGNORECASE) for p in self.PRE_NEGATION]
        self._post_neg_patterns = [re.compile(p, re.IGNORECASE) for p in self.POST_NEGATION]
        self._uncertainty_patterns = [re.compile(p, re.IGNORECASE) for p in self.UNCERTAINTY]
        self._historical_patterns = [re.compile(p, re.IGNORECASE) for p in self.HISTORICAL]
        self._family_patterns = [re.compile(p, re.IGNORECASE) for p in self.FAMILY_HISTORY]

    def analyze(self, text: str, entity_start: int, entity_end: int) -> Dict[str, Any]:
        """
        Analyze context around an entity for negation and uncertainty.

        Returns:
            {"negated": bool, "uncertainty": bool, "temporal": str}
        """
        # Context window: N characters before and after
        pre_context = text[max(0, entity_start - 150):entity_start]
        post_context = text[entity_end:min(len(text), entity_end + 100)]

        negated = False
        uncertainty = False
        temporal = "current"

        # Check pre-negation
        for pattern in self._pre_neg_patterns:
            if pattern.search(pre_context):
                negated = True
                break

        # Check post-negation
        if not negated:
            for pattern in self._post_neg_patterns:
                if pattern.search(post_context):
                    negated = True
                    break

        # Check uncertainty
        for pattern in self._uncertainty_patterns:
            if pattern.search(pre_context) or pattern.search(post_context):
                uncertainty = True
                break

        # Check temporal
        for pattern in self._family_patterns:
            if pattern.search(pre_context):
                temporal = "family_history"
                break

        if temporal == "current":
            for pattern in self._historical_patterns:
                if pattern.search(pre_context):
                    temporal = "historical"
                    break

        return {
            "negated": negated,
            "uncertainty_marker": uncertainty,
            "temporal": temporal,
        }


# ─────────────────────────────────────────────
# Rule-Based NER (fast fallback)
# ─────────────────────────────────────────────

class RuleBasedNER:
    """
    Rule-based NER for common clinical entities.
    Used as:
    1. Fast fallback when BioMedBERT is unavailable
    2. High-confidence extraction for known patterns (labs, vitals)
    3. Post-processing filter for ML model outputs

    In production: ML model (BioMedBERT) handles most entities.
    This catches the highly structured ones (labs with values).
    """

    # Lab value patterns: "WBC 12.5 K/uL", "Creatinine: 2.1 mg/dL"
    LAB_PATTERN = re.compile(
        r"(WBC|Hemoglobin|Hgb|Platelet|Creatinine|Sodium|Na|Potassium|K|"
        r"Glucose|BUN|Bicarbonate|HCO3|Lactate|Troponin|BNP|"
        r"Procalcitonin|PCT|CRP|D-dimer|INR|PT|PTT|"
        r"ALT|AST|Total Bili|Direct Bili|Albumin|"
        r"pH|pO2|pCO2|SaO2|FiO2|PEEP)"
        r"\s*[:=]?\s*([<>]?\d+\.?\d*)\s*([\w/µ]*)",
        re.IGNORECASE,
    )

    # Vital sign patterns
    VITAL_PATTERNS = {
        "heart_rate": re.compile(r"(?:HR|Heart Rate|Pulse)\s*[:=]?\s*(\d+)\s*(?:bpm|/min)?", re.IGNORECASE),
        "bp_combined": re.compile(r"(?:BP|Blood Pressure)\s*[:=]?\s*(\d+)/(\d+)\s*(?:mmHg)?", re.IGNORECASE),
        "spo2": re.compile(r"(?:SpO2|O2 Sat|Oxygen Saturation)\s*[:=]?\s*(\d+)\s*%?", re.IGNORECASE),
        "temperature": re.compile(r"(?:Temp|Temperature)\s*[:=]?\s*(\d+\.?\d*)\s*(?:°?[CF])?", re.IGNORECASE),
        "respiratory_rate": re.compile(r"(?:RR|Resp Rate|Respiratory Rate)\s*[:=]?\s*(\d+)\s*(?:/min)?", re.IGNORECASE),
        "weight": re.compile(r"(?:Weight|Wt)\s*[:=]?\s*(\d+\.?\d*)\s*(?:kg|lbs?)?", re.IGNORECASE),
    }

    # Common clinical findings (ICD-10 concept groups)
    DISEASE_PATTERNS = {
        "sepsis": re.compile(r"\bsepsis\b|\bseptic shock\b|\bsepticemia\b", re.IGNORECASE),
        "pneumonia": re.compile(r"\bpneumonia\b|\bCAP\b|\bHAP\b|\bVAP\b", re.IGNORECASE),
        "heart_failure": re.compile(r"\bheart failure\b|\bCHF\b|\bcongestive heart failure\b", re.IGNORECASE),
        "ards": re.compile(r"\bARDS\b|\bacute respiratory distress\b", re.IGNORECASE),
        "aki": re.compile(r"\bacute kidney injury\b|\bAKI\b|\bacute renal failure\b", re.IGNORECASE),
        "mi": re.compile(r"\bmyocardial infarction\b|\bMI\b|\bSTEMI\b|\bNSTEMI\b", re.IGNORECASE),
        "stroke": re.compile(r"\bstroke\b|\bCVA\b|\bcerebrovascular accident\b", re.IGNORECASE),
        "pe": re.compile(r"\bpulmonary embolism\b|\bPE\b", re.IGNORECASE),
        "dvt": re.compile(r"\bdeep vein thrombosis\b|\bDVT\b", re.IGNORECASE),
        "copd": re.compile(r"\bCOPD\b|\bchronic obstructive pulmonary disease\b|\bCOPD exacerbation\b", re.IGNORECASE),
    }

    # ICD-10 mapping for common conditions
    CONDITION_ICD10 = {
        "sepsis": "A41.9",
        "pneumonia": "J18.9",
        "heart_failure": "I50.9",
        "ards": "J80",
        "aki": "N17.9",
        "mi": "I21.9",
        "stroke": "I63.9",
        "pe": "I26.99",
        "dvt": "I82.409",
        "copd": "J44.1",
    }

    def extract_labs(self, text: str) -> List[ClinicalEntity]:
        entities = []
        for match in self.LAB_PATTERN.finditer(text):
            entities.append(ClinicalEntity(
                text=match.group(0),
                entity_type=EntityType.LAB,
                normalized=None,
                start_char=match.start(),
                end_char=match.end(),
                confidence=0.95,
            ))
        return entities

    def extract_vitals(self, text: str) -> List[Dict]:
        """Extract vital sign values from text."""
        found = {}
        for param, pattern in self.VITAL_PATTERNS.items():
            match = pattern.search(text)
            if match:
                try:
                    if param == "bp_combined":
                        found["bp_systolic"] = float(match.group(1))
                        found["bp_diastolic"] = float(match.group(2))
                    else:
                        found[param] = float(match.group(1))
                except (ValueError, IndexError):
                    pass
        return found

    def extract_conditions(self, text: str) -> List[ClinicalEntity]:
        entities = []
        for condition, pattern in self.DISEASE_PATTERNS.items():
            for match in pattern.finditer(text):
                entities.append(ClinicalEntity(
                    text=match.group(0),
                    entity_type=EntityType.DISEASE,
                    normalized=self.CONDITION_ICD10.get(condition),
                    start_char=match.start(),
                    end_char=match.end(),
                    confidence=0.90,
                ))
        return entities


# ─────────────────────────────────────────────
# Semantic Deduplication
# ─────────────────────────────────────────────

class SemanticDeduplicator:
    """
    Detect duplicate clinical notes using embedding similarity.
    Clinical notes often repeat information across shifts.
    Threshold: cosine similarity > 0.92 = duplicate, skip.

    Uses sentence-transformers (Bio_ClinicalBERT embeddings).
    """

    SIMILARITY_THRESHOLD = 0.92

    def __init__(self, embedding_model=None):
        self._model = embedding_model  # sentence_transformers.SentenceTransformer
        self._seen_hashes: Dict[str, str] = {}  # simple_hash → doc_id

    def get_embedding(self, text: str) -> Optional[List[float]]:
        if self._model is None:
            return None
        try:
            return self._model.encode(text[:512]).tolist()  # Truncate to 512 tokens
        except Exception as e:
            logger.warning(f"Embedding failed: {e}")
            return None

    def cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Compute cosine similarity between two embedding vectors."""
        import math
        dot = sum(a * b for a, b in zip(vec1, vec2))
        mag1 = math.sqrt(sum(a * a for a in vec1))
        mag2 = math.sqrt(sum(b * b for b in vec2))
        if mag1 == 0 or mag2 == 0:
            return 0.0
        return dot / (mag1 * mag2)

    def is_duplicate(
        self,
        text: str,
        existing_embeddings: List[List[float]],
    ) -> Tuple[bool, float]:
        """
        Check if text is semantically duplicate of any existing document.
        Returns (is_duplicate, max_similarity_score).
        """
        new_embedding = self.get_embedding(text)
        if new_embedding is None:
            # Fallback: exact hash check
            text_hash = hashlib.md5(text.strip().encode()).hexdigest()
            return text_hash in self._seen_hashes, 0.0

        max_sim = 0.0
        for existing in existing_embeddings:
            sim = self.cosine_similarity(new_embedding, existing)
            max_sim = max(max_sim, sim)
            if sim >= self.SIMILARITY_THRESHOLD:
                return True, sim

        return False, max_sim


# ─────────────────────────────────────────────
# ICD-10 Code Suggester
# ─────────────────────────────────────────────

class ICD10Suggester:
    """
    Suggest ICD-10 codes from free-text clinical descriptions.

    Production: fine-tuned BioMedBERT with ICD-10 classification head.
    This implementation: rule-based mapping for common conditions.

    The LLM reasoning engine provides more sophisticated coding.
    """

    # Extended ICD-10 mapping
    ICD10_MAP = {
        # Infectious / Sepsis
        "septic shock": ("R57.2", "Septic shock", 0.92),
        "sepsis": ("A41.9", "Sepsis, unspecified organism", 0.88),
        "bacteremia": ("R78.81", "Bacteremia", 0.85),
        "pneumonia": ("J18.9", "Pneumonia, unspecified organism", 0.85),
        "community-acquired pneumonia": ("J18.9", "CAP", 0.90),
        "healthcare-associated pneumonia": ("J22", "HCAP", 0.88),

        # Cardiac
        "heart failure": ("I50.9", "Heart failure, unspecified", 0.88),
        "acute heart failure": ("I50.9", "Acute heart failure", 0.90),
        "atrial fibrillation": ("I48.91", "Atrial fibrillation, unspecified", 0.92),
        "myocardial infarction": ("I21.9", "Acute MI, unspecified", 0.90),
        "stemi": ("I21.3", "STEMI", 0.92),
        "nstemi": ("I21.4", "NSTEMI", 0.92),

        # Respiratory
        "ards": ("J80", "ARDS", 0.90),
        "respiratory failure": ("J96.00", "Respiratory failure, unspecified", 0.85),
        "copd exacerbation": ("J44.1", "COPD acute exacerbation", 0.90),
        "pulmonary embolism": ("I26.99", "PE without cardiac arrest", 0.90),

        # Renal
        "acute kidney injury": ("N17.9", "AKI, unspecified", 0.88),
        "aki": ("N17.9", "AKI", 0.88),
        "chronic kidney disease": ("N18.9", "CKD, unspecified", 0.85),

        # Neurological
        "stroke": ("I63.9", "Cerebral infarction, unspecified", 0.85),
        "ischemic stroke": ("I63.9", "Ischemic stroke", 0.90),
        "altered mental status": ("R41.3", "Other amnesia", 0.75),

        # Metabolic
        "diabetic ketoacidosis": ("E11.10", "DKA", 0.92),
        "hypoglycemia": ("E11.649", "Hypoglycemia", 0.88),
        "hyperkalemia": ("E87.5", "Hyperkalemia", 0.90),
        "hyponatremia": ("E87.1", "Hyponatremia", 0.90),

        # GI
        "gi bleed": ("K92.2", "GI hemorrhage, unspecified", 0.85),
        "upper gi bleed": ("K92.2", "Upper GI bleed", 0.88),
        "lower gi bleed": ("K92.1", "Melena", 0.85),
        "pancreatitis": ("K85.90", "Acute pancreatitis", 0.88),
    }

    def suggest(self, text: str, n: int = 5) -> List[Dict]:
        """
        Suggest up to n ICD-10 codes from free text.
        Returns list sorted by confidence descending.
        """
        text_lower = text.lower()
        suggestions = []

        for condition, (code, description, confidence) in self.ICD10_MAP.items():
            if condition in text_lower:
                suggestions.append({
                    "icd10_code": code,
                    "description": description,
                    "confidence": confidence,
                    "matched_term": condition,
                })

        # Sort by confidence
        suggestions.sort(key=lambda x: x["confidence"], reverse=True)
        return suggestions[:n]


# ─────────────────────────────────────────────
# Clinical NLP Pipeline Orchestrator
# ─────────────────────────────────────────────

class ClinicalNLPPipeline:
    """
    Orchestrates the full NLP pipeline for a clinical document.

    Pipeline:
    1. Document classification (what type of note?)
    2. Text preprocessing (section splitting, cleanup)
    3. NER (ML model or rule-based fallback)
    4. Negation + uncertainty + temporal analysis
    5. ICD-10 code suggestion
    6. Clinical summary generation (2-3 sentences)
    7. Sentiment classification (improving/stable/deteriorating)
    8. Semantic hash computation (for deduplication)
    """

    def __init__(
        self,
        ner_model=None,            # BioMedBERT NER model
        embedding_model=None,      # sentence-transformers
    ):
        self._ner = ner_model
        self._rule_ner = RuleBasedNER()
        self._negation = NegationDetector()
        self._dedup = SemanticDeduplicator(embedding_model)
        self._icd10 = ICD10Suggester()

    def process(
        self,
        document_id: str,
        document_type: str,
        text: str,
        patient_deident_id: str,
        document_date: Optional[str] = None,
    ) -> ClinicalNLPOutput:
        """Process a clinical document end-to-end."""
        import time
        start = time.time()

        doc_type = self._classify_document(document_type, text)

        # Extract entities
        entities = self._extract_entities(text)

        # Apply negation + temporal analysis
        for entity in entities:
            context = self._negation.analyze(text, entity.start_char, entity.end_char)
            entity.negated = context["negated"]
            entity.uncertainty_marker = context["uncertainty_marker"]
            entity.temporal = context["temporal"]

        # ICD-10 suggestions
        icd10_suggestions = self._icd10.suggest(text)

        # Clinical summary (rule-based for now; LLM in production)
        summary = self._generate_summary(entities, doc_type)

        # Sentiment
        sentiment = self._classify_sentiment(text, entities)

        # Semantic hash
        semantic_hash = hashlib.sha256(text[:1000].encode()).hexdigest()

        processing_ms = int((time.time() - start) * 1000)

        return ClinicalNLPOutput(
            document_id=document_id,
            document_type=doc_type,
            document_date=document_date,
            patient_deident_id=patient_deident_id,
            entities=entities,
            summary=summary,
            sentiment=sentiment,
            suggested_icd10_codes=icd10_suggestions,
            semantic_hash=semantic_hash,
            processing_ms=processing_ms,
        )

    def _classify_document(self, doc_type_str: str, text: str) -> DocumentType:
        type_map = {
            "progress": DocumentType.PROGRESS_NOTE,
            "discharge": DocumentType.DISCHARGE,
            "radiology": DocumentType.RADIOLOGY,
            "pathology": DocumentType.PATHOLOGY,
            "nursing": DocumentType.NURSING,
            "consult": DocumentType.CONSULT,
        }
        for key, dtype in type_map.items():
            if key in doc_type_str.lower() or key in text[:200].lower():
                return dtype
        return DocumentType.PROGRESS_NOTE

    def _extract_entities(self, text: str) -> List[ClinicalEntity]:
        """Extract entities using ML model + rule-based fallback."""
        entities = []

        # Rule-based extraction (always runs)
        entities.extend(self._rule_ner.extract_labs(text))
        entities.extend(self._rule_ner.extract_conditions(text))

        # ML NER model (when available)
        if self._ner:
            try:
                ml_entities = self._run_ner_model(text)
                entities.extend(ml_entities)
            except Exception as e:
                logger.warning(f"NER model failed, using rule-based only: {e}")

        # Deduplicate overlapping entities (prefer higher confidence)
        entities = self._deduplicate_entities(entities)
        return entities

    def _run_ner_model(self, text: str) -> List[ClinicalEntity]:
        """Run BioMedBERT NER model."""
        # Production: self._ner.predict(text)
        return []

    def _deduplicate_entities(self, entities: List[ClinicalEntity]) -> List[ClinicalEntity]:
        """Remove overlapping entity spans, keeping highest confidence."""
        if not entities:
            return []
        sorted_entities = sorted(entities, key=lambda e: e.confidence, reverse=True)
        result = []
        for entity in sorted_entities:
            overlap = any(
                not (entity.end_char <= kept.start_char or entity.start_char >= kept.end_char)
                for kept in result
            )
            if not overlap:
                result.append(entity)
        return sorted(result, key=lambda e: e.start_char)

    def _generate_summary(self, entities: List[ClinicalEntity], doc_type: DocumentType) -> str:
        """Generate 2-3 sentence clinical summary from extracted entities."""
        diagnoses = [e.text for e in entities if e.entity_type == EntityType.DISEASE and not e.negated][:3]
        symptoms = [e.text for e in entities if e.entity_type == EntityType.SYMPTOM and not e.negated][:3]
        meds = [e.text for e in entities if e.entity_type == EntityType.MEDICATION and not e.negated][:2]

        parts = []
        if diagnoses:
            parts.append(f"Active diagnoses: {', '.join(diagnoses)}.")
        if symptoms:
            parts.append(f"Current symptoms include: {', '.join(symptoms)}.")
        if meds:
            parts.append(f"Current medications include: {', '.join(meds)}.")

        return " ".join(parts) if parts else "No significant entities extracted."

    def _classify_sentiment(
        self,
        text: str,
        entities: List[ClinicalEntity],
    ) -> ClinicalSentiment:
        """Classify overall clinical trajectory."""
        text_lower = text.lower()

        improving_signals = [
            "improving", "better", "improved", "resolving", "stable", "afebrile",
            "tolerating", "ambulating", "weaning", "extubated", "discharge",
        ]
        deteriorating_signals = [
            "worsening", "worse", "deteriorating", "declining", "failing",
            "decompensating", "intubated", "vasopressors", "escalating",
        ]

        impr_count = sum(1 for s in improving_signals if s in text_lower)
        detr_count = sum(1 for s in deteriorating_signals if s in text_lower)

        if detr_count > impr_count:
            return ClinicalSentiment.DETERIORATING
        elif impr_count > detr_count:
            return ClinicalSentiment.IMPROVING
        else:
            return ClinicalSentiment.STABLE
