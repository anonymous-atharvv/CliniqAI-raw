"""Drift Detector — monitors AI model performance degradation weekly."""
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

@dataclass
class WeeklySnapshot:
    week_start: str; hospital_id: str; model_name: str
    auroc: Optional[float]=None; acceptance_rate: float=0.0
    false_positive_rate: float=0.0; rejection_rate: float=0.0
    drift_detected: bool=False; drift_alerts: List[str]=field(default_factory=list)
    auto_updates_frozen: bool=False

class DriftDetector:
    AUROC_DROP_THRESHOLD=0.05; FP_INCREASE_THRESHOLD=0.10; REJECTION_THRESHOLD=0.30

    def __init__(self, baseline:Optional[Dict]=None, alert_callback=None):
        self._baseline=baseline or {"sepsis_auroc":0.878,"acceptance_rate":0.72,"false_positive_rate":0.139}
        self._alert_callback=alert_callback; self._frozen=False

    def analyze(self, snapshot:WeeklySnapshot) -> Tuple[bool,List[str]]:
        alerts=[]
        if snapshot.auroc and self._baseline.get("sepsis_auroc"):
            drop=self._baseline["sepsis_auroc"]-snapshot.auroc
            if drop>self.AUROC_DROP_THRESHOLD:
                alerts.append(f"DRIFT: AUROC dropped {drop:.1%} ({self._baseline['sepsis_auroc']:.3f}→{snapshot.auroc:.3f})")
        if snapshot.false_positive_rate:
            increase=snapshot.false_positive_rate-self._baseline.get("false_positive_rate",0.139)
            if increase>self.FP_INCREASE_THRESHOLD:
                alerts.append(f"DRIFT: False positive rate +{increase:.1%}")
        if snapshot.rejection_rate>self.REJECTION_THRESHOLD:
            alerts.append(f"DRIFT: Rejection rate {snapshot.rejection_rate:.1%} exceeds {self.REJECTION_THRESHOLD:.0%}")
        if alerts and not self._frozen:
            self._frozen=True
            logger.critical(f"MODEL DRIFT DETECTED — auto-updates FROZEN. Alerts: {alerts}")
            if self._alert_callback: self._alert_callback(alerts)
        snapshot.drift_detected=bool(alerts); snapshot.drift_alerts=alerts; snapshot.auto_updates_frozen=self._frozen
        return bool(alerts),alerts

    def unfreeze(self, authorized_by:str):
        logger.info(f"MODEL drift freeze LIFTED by {authorized_by}")
        self._frozen=False

