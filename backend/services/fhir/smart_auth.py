"""
SMART on FHIR OAuth2 Authentication
=====================================
SMART on FHIR is the actual OAuth2 standard hospitals use for third-party app access.
Without it, Epic and Cerner will REJECT your integration.

Flow:
1. App registered in Epic App Orchard / Cerner App Gallery
2. Hospital IT enables app for their instance
3. Physician launches app from within Epic (EHR launch)
4. SMART OAuth2 flow exchanges launch context for access token
5. Access token used for FHIR R4 API calls

Two launch contexts:
  - EHR Launch: launched from within Epic by physician (has patient context)
  - Standalone Launch: launched independently (user selects patient)

Scopes required:
  patient/*.read     — read patient data
  openid profile    — identify the user
  launch/patient    — patient in context (EHR launch)
  online_access     — refresh tokens
"""

import hashlib
import base64
import secrets
import logging
import httpx
from typing import Optional, Dict, Tuple
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode, urlparse, parse_qs
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class SMARTConfig:
    """SMART on FHIR configuration for a specific hospital's EHR."""
    hospital_id: str
    fhir_base_url: str          # e.g. https://fhir.smary.org/api/FHIR/R4
    client_id: str              # Registered in Epic App Orchard
    client_secret: Optional[str] = None  # None for public clients
    redirect_uri: str = "https://app.cliniqai.com/auth/callback"
    scopes: list = field(default_factory=lambda: [
        "patient/*.read",
        "user/*.read",
        "openid",
        "profile",
        "launch",
        "launch/patient",
        "online_access",
    ])

    @property
    def authorize_url(self) -> str:
        return f"{self.fhir_base_url}/../oauth2/authorize"

    @property
    def token_url(self) -> str:
        return f"{self.fhir_base_url}/../oauth2/token"

    @property
    def scope_string(self) -> str:
        return " ".join(self.scopes)


@dataclass
class SMARTTokenResponse:
    """Parsed SMART token response."""
    access_token: str
    token_type: str
    expires_in: int
    scope: str
    patient_id: Optional[str] = None       # EHR launch: patient in context
    encounter_id: Optional[str] = None     # EHR launch: encounter in context
    id_token: Optional[str] = None         # OpenID Connect
    refresh_token: Optional[str] = None    # If online_access scope granted
    issued_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def expires_at(self) -> datetime:
        return self.issued_at + timedelta(seconds=self.expires_in)

    @property
    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) >= self.expires_at

    @property
    def needs_refresh(self) -> bool:
        """Refresh if expiring in less than 5 minutes."""
        return datetime.now(timezone.utc) >= (self.expires_at - timedelta(minutes=5))


class PKCEChallenge:
    """
    PKCE (Proof Key for Code Exchange) for public clients.
    Prevents authorization code interception attacks.
    Required for Epic public client apps.
    """

    @staticmethod
    def generate() -> Tuple[str, str]:
        """
        Returns (code_verifier, code_challenge).
        code_verifier: random high-entropy string (43-128 chars)
        code_challenge: SHA256(code_verifier) base64url-encoded
        """
        code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
        digest = hashlib.sha256(code_verifier.encode()).digest()
        code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        return code_verifier, code_challenge


class SMARTAuthClient:
    """
    SMART on FHIR OAuth2 client.
    
    Handles both EHR Launch (physician opens app inside Epic)
    and Standalone Launch (app opened independently).
    
    Token storage: Redis with TTL matching token expiry.
    Token refresh: automatic when needed.
    """

    def __init__(self, config: SMARTConfig, redis_cache=None):
        self._config = config
        self._cache = redis_cache
        self._pkce_store: Dict[str, str] = {}   # state → code_verifier

    def build_authorize_url(
        self,
        launch_token: Optional[str] = None,
        state: Optional[str] = None,
    ) -> Tuple[str, str]:
        """
        Build the OAuth2 authorization URL.

        For EHR Launch: launch_token is provided by Epic in the launch URL.
        For Standalone: launch_token is None.

        Returns (authorization_url, state) where state should be stored
        in session to validate the callback.
        """
        state = state or secrets.token_urlsafe(32)
        code_verifier, code_challenge = PKCEChallenge.generate()

        # Store code_verifier indexed by state (for callback validation)
        self._pkce_store[state] = code_verifier

        params = {
            "response_type": "code",
            "client_id": self._config.client_id,
            "redirect_uri": self._config.redirect_uri,
            "scope": self._config.scope_string,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "aud": self._config.fhir_base_url,
        }

        # EHR Launch requires the launch token
        if launch_token:
            params["launch"] = launch_token

        url = f"{self._config.authorize_url}?{urlencode(params)}"
        logger.debug(f"SMART authorize URL built for hospital={self._config.hospital_id}")
        return url, state

    async def exchange_code(
        self,
        authorization_code: str,
        state: str,
    ) -> SMARTTokenResponse:
        """
        Exchange authorization code for access + refresh tokens.
        Called in the OAuth2 callback.

        PKCE: validates code_verifier matches earlier code_challenge.
        """
        code_verifier = self._pkce_store.pop(state, None)
        if not code_verifier:
            raise ValueError(f"No PKCE verifier found for state={state} — possible CSRF attack")

        token_params = {
            "grant_type": "authorization_code",
            "code": authorization_code,
            "redirect_uri": self._config.redirect_uri,
            "client_id": self._config.client_id,
            "code_verifier": code_verifier,
        }

        # Confidential client: add client secret
        if self._config.client_secret:
            token_params["client_secret"] = self._config.client_secret

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                self._config.token_url,
                data=token_params,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

        if response.status_code != 200:
            logger.error(f"SMART token exchange failed: {response.status_code} {response.text[:200]}")
            raise ValueError(f"Token exchange failed: {response.status_code}")

        token_data = response.json()

        smart_token = SMARTTokenResponse(
            access_token=token_data["access_token"],
            token_type=token_data.get("token_type", "Bearer"),
            expires_in=token_data.get("expires_in", 3600),
            scope=token_data.get("scope", ""),
            patient_id=token_data.get("patient"),
            encounter_id=token_data.get("encounter"),
            id_token=token_data.get("id_token"),
            refresh_token=token_data.get("refresh_token"),
        )

        # Cache token in Redis
        if self._cache:
            await self._cache_token(smart_token)

        logger.info(
            f"SMART token obtained: hospital={self._config.hospital_id} "
            f"patient_in_context={smart_token.patient_id} "
            f"expires_in={smart_token.expires_in}s"
        )

        return smart_token

    async def refresh_access_token(self, refresh_token: str) -> SMARTTokenResponse:
        """
        Use refresh token to obtain new access token.
        Called automatically when token expires.
        """
        token_params = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self._config.client_id,
        }
        if self._config.client_secret:
            token_params["client_secret"] = self._config.client_secret

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                self._config.token_url,
                data=token_params,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

        if response.status_code != 200:
            raise ValueError(f"Token refresh failed: {response.status_code}")

        token_data = response.json()
        return SMARTTokenResponse(
            access_token=token_data["access_token"],
            token_type=token_data.get("token_type", "Bearer"),
            expires_in=token_data.get("expires_in", 3600),
            scope=token_data.get("scope", ""),
            patient_id=token_data.get("patient"),
            refresh_token=token_data.get("refresh_token", refresh_token),
        )

    async def _cache_token(self, token: SMARTTokenResponse):
        """Cache token in Redis with TTL matching token expiry."""
        import json
        key = f"smart_token:{self._config.hospital_id}:{token.patient_id or 'standalone'}"
        data = {
            "access_token": token.access_token,
            "expires_in": token.expires_in,
            "patient_id": token.patient_id,
            "refresh_token": token.refresh_token,
        }
        await self._cache._r.setex(key, token.expires_in, json.dumps(data))


class FHIRClient:
    """
    FHIR R4 API client authenticated via SMART tokens.

    Makes FHIR calls to the hospital's EHR endpoint.
    Handles: automatic token refresh, rate limiting, pagination.
    """

    def __init__(self, fhir_base_url: str, token: SMARTTokenResponse, smart_client: SMARTAuthClient):
        self._base_url = fhir_base_url.rstrip("/")
        self._token = token
        self._smart = smart_client

    async def _get_headers(self) -> Dict[str, str]:
        """Get auth headers, refreshing token if needed."""
        if self._token.needs_refresh and self._token.refresh_token:
            self._token = await self._smart.refresh_access_token(self._token.refresh_token)
        return {
            "Authorization": f"Bearer {self._token.access_token}",
            "Accept": "application/fhir+json",
            "Content-Type": "application/fhir+json",
        }

    async def get_patient(self, patient_id: str) -> dict:
        """GET /Patient/{id}"""
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"{self._base_url}/Patient/{patient_id}",
                headers=await self._get_headers(),
            )
        r.raise_for_status()
        return r.json()

    async def get_patient_vitals(
        self,
        patient_id: str,
        hours: int = 24,
        loinc_codes: Optional[list] = None,
    ) -> dict:
        """
        GET /Observation?patient={id}&category=vital-signs&date=ge{hours_ago}
        Optionally filter by LOINC code.
        """
        from datetime import datetime, timezone, timedelta

        date_from = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        params = {
            "patient": patient_id,
            "category": "vital-signs",
            "date": f"ge{date_from}",
            "_count": 100,
            "_sort": "-date",
        }
        if loinc_codes:
            params["code"] = ",".join(f"http://loinc.org|{code}" for code in loinc_codes)

        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"{self._base_url}/Observation",
                params=params,
                headers=await self._get_headers(),
            )
        r.raise_for_status()
        return r.json()

    async def get_medications(self, patient_id: str, status: str = "active") -> dict:
        """GET /MedicationRequest?patient={id}&status=active"""
        params = {"patient": patient_id, "status": status, "_count": 50}
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"{self._base_url}/MedicationRequest",
                params=params,
                headers=await self._get_headers(),
            )
        r.raise_for_status()
        return r.json()

    async def get_labs(self, patient_id: str, hours: int = 48) -> dict:
        """GET /Observation?patient={id}&category=laboratory"""
        from datetime import datetime, timezone, timedelta
        date_from = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        params = {
            "patient": patient_id,
            "category": "laboratory",
            "date": f"ge{date_from}",
            "_count": 50,
            "_sort": "-date",
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"{self._base_url}/Observation",
                params=params,
                headers=await self._get_headers(),
            )
        r.raise_for_status()
        return r.json()

    async def get_allergies(self, patient_id: str) -> dict:
        """GET /AllergyIntolerance?patient={id}"""
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"{self._base_url}/AllergyIntolerance",
                params={"patient": patient_id},
                headers=await self._get_headers(),
            )
        r.raise_for_status()
        return r.json()

    async def get_conditions(self, patient_id: str) -> dict:
        """GET /Condition?patient={id}&clinical-status=active"""
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"{self._base_url}/Condition",
                params={"patient": patient_id, "clinical-status": "active"},
                headers=await self._get_headers(),
            )
        r.raise_for_status()
        return r.json()

    async def get_full_patient_context(self, patient_id: str) -> dict:
        """
        Fetch complete patient context for LLM reasoning engine.
        Parallel requests to minimize latency.
        """
        import asyncio
        vitals_task = self.get_patient_vitals(patient_id, hours=24)
        meds_task = self.get_medications(patient_id)
        labs_task = self.get_labs(patient_id, hours=48)
        allergies_task = self.get_allergies(patient_id)
        conditions_task = self.get_conditions(patient_id)
        patient_task = self.get_patient(patient_id)

        results = await asyncio.gather(
            vitals_task, meds_task, labs_task,
            allergies_task, conditions_task, patient_task,
            return_exceptions=True,
        )

        vitals, meds, labs, allergies, conditions, patient = results

        return {
            "patient": patient if not isinstance(patient, Exception) else None,
            "vitals_bundle": vitals if not isinstance(vitals, Exception) else {"entry": []},
            "medications_bundle": meds if not isinstance(meds, Exception) else {"entry": []},
            "labs_bundle": labs if not isinstance(labs, Exception) else {"entry": []},
            "allergies_bundle": allergies if not isinstance(allergies, Exception) else {"entry": []},
            "conditions_bundle": conditions if not isinstance(conditions, Exception) else {"entry": []},
            "fetch_errors": [str(r) for r in results if isinstance(r, Exception)],
        }
