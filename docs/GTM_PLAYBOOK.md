# CliniQAI — Go-To-Market Strategy & Sales Playbook

**Classification**: Confidential — Executive + Sales Team Only  
**Version**: 1.0  

---

## The Fundamental Truth

> "Physicians are not your buyer. CFOs and COOs are your buyer."

Building the AI is 30% of the work. Getting a hospital to sign is 70%.

Your differentiation is NOT the AI. Any hospital can buy IBM Watson or Microsoft Dragon. 

**Your differentiation:**
1. A self-improving model that gets smarter with every hospital added
2. Outcome-validated performance data (not just demo screenshots)
3. The only platform built on actual community hospital workflows (not academic center fantasy)
4. FDA clearance (your eventual moat — competitors can't shortcut this)

---

## Target Customer Profile

### Primary Target: Community Hospitals (200–400 beds)

**Why NOT large academic medical centers:**
- Academic centers have 50-person IT teams building their own AI
- They have Epic/Cerner research agreements that give them free data
- Sales cycles are 3–5 years with committee-driven purchasing
- Your differentiator (ease of use, turnkey) doesn't resonate

**Why community hospitals:**
- No internal AI team — they NEED turnkey
- Budget pressures from CMS readmission penalties (your ROI story)
- CMO can champion you without 20 committee approvals
- Sales cycle: 9–18 months
- First hospital leads directly to second (reference customer effect)

### Decision Makers (You Need All Three)

```
┌─────────────────────────────────────────────────────────────┐
│  CMO (Clinical Champion)                                    │
│  • Cares about: clinical outcomes, patient safety, physician│
│    adoption, liability reduction                            │
│  • Pain point: sepsis deaths, readmission rates, staff burnout│
│  • Your pitch: "AI that helps your doctors think faster"   │
├─────────────────────────────────────────────────────────────┤
│  CIO (Technical Gatekeeper)                                 │
│  • Cares about: Epic integration, security, IT burden       │
│  • Pain point: vendors that break things, HIPAA liability   │
│  • Your pitch: "Epic App Orchard certified, HIPAA-native,  │
│    your IT team touches nothing"                            │
├─────────────────────────────────────────────────────────────┤
│  CFO (Budget Owner)                                         │
│  • Cares about: ROI, CMS penalties, LOS, staff costs        │
│  • Pain point: $1M+ in CMS readmission penalties per year  │
│  • Your pitch: "3.8× ROI, $484K value month 1"             │
└─────────────────────────────────────────────────────────────┘
```

**You need all three.** No single champion can push a purchase alone.

**Budget cycles:** Annual. Hospitals set budgets in Q3 for the following year. 
**Action:** Start conversations in Q1-Q2 to be in Q3-Q4 budget cycle.

---

## The Pilot Program

### Structure Your First 5 Hospitals as Funded Pilots

**Pilot design (90 days):**

```
Day 1–14:   Technical onboarding
            • Epic App Orchard integration
            • FHIR R4 endpoint configuration
            • Staff training (physicians + nurses)
            • Synthea test data validation

Day 15–30:  Shadow mode (AI runs, no physician visibility)
            • Validate data quality
            • Calibrate alert thresholds for this hospital
            • Measure AI vs actual clinical decisions
            • Build baseline metrics

Day 31–75:  Active pilot (one department: ICU)
            • Physicians see AI recommendations
            • Capture physician feedback (every interaction)
            • Track alert acceptance rates
            • Monitor clinical outcomes
            
Day 76–90:  Analysis and reporting
            • Compute ROI metrics
            • Clinical publication preparation
            • Reference case study
            • Expansion planning
```

**Pricing during pilot:**
- Free or $0–5K/month for 90 days
- Success metric defined UPFRONT with hospital (not by us)
- Example metric: "Sepsis bundle compliance increases from X% to X+15%"
- If metric not met: no penalty, extended free period

**What you get from the pilot:**
1. Clinical validation data for FDA submission
2. Case study + testimonial quote from CMO
3. Reference customer for next sales cycle
4. Publication opportunity (JAMA Open, NEJM Catalyst)

---

## Integration Strategy

### NEVER Start with "Full Platform"

The full platform pitch fails because:
- IT approval takes 12+ months for new systems
- Nobody wants to manage another vendor
- Nobody wants to train staff on a new UI

### Start with Epic App Orchard Integration

Epic has 32% US hospital market share. An Epic-native app:
- Appears inside physician Epic workflow (no new login, no new screen)
- Zero additional training burden
- IT approval path is defined (Epic has a certification process)
- Physicians see AI recommendations in their normal Chart Review

**Epic App Orchard certification:**
- File with Epic: 3–6 months
- Requires SMART on FHIR compliance (already built)
- Requires Epic security review (HIPAA + SOC 2 Type II required)
- Cost: $3,000–$10,000 for certification

**What the Epic-embedded experience looks like:**
```
[Physician opens patient chart in Epic]
[CliniQAI smart data panel appears in right rail]
[Shows: Risk Level | AI Predictions | Key Alerts | Recommendations]
[Physician can click Feedback (thumbs up/down) without leaving Epic]
[No new window. No new login. No workflow change.]
```

### Fallback for Cerner/Meditech Hospitals
- Cerner: Use SMART on FHIR app framework (similar to Epic)
- Meditech: Web-based overlay via Meditech's API layer (more complex, later priority)

---

## Regulatory Strategy

### File for FDA 510(k) on Your HIGHEST-VALUE Use Case First

**Use case for first filing: Sepsis Prediction**

Why sepsis prediction:
- Clear predicate devices exist (Epic Sepsis Model cleared K203264)
- Well-defined performance thresholds (AUROC >0.85, sensitivity >0.80)
- Hospitals desperately need better sepsis tools (SEP-1 quality measure)
- Our MIMIC-IV validated model meets thresholds

**Timeline:**
- Month 3: Pre-submission meeting request filed with FDA CDRH
- Month 6: Pre-submission meeting (~90 days to schedule)
- Month 9: 510(k) submission filed
- Month 21: Expected clearance (12–18 months from submission)

**What you can do before clearance:**
- Market as "FDA clearance pending"
- Deploy as clinical decision support (not autonomous)
- Ensure physician review required for all outputs (Class II design)
- Conduct research studies with IRB approval

**CE Mark (EU) in parallel:**
- IVDR (In Vitro Diagnostic Regulation) pathway
- Similar 12–18 month timeline
- Hire EU regulatory consultant at Month 6
- Opens European market at Month 24

---

## Pricing Model

### Per-Bed SaaS Pricing

| Phase | Scope | Price/Bed/Month | Example (300-bed) |
|-------|-------|-----------------|-------------------|
| Year 1 (Pilot) | Target department only (50-bed ICU) | $500 | $25K/month |
| Year 2 (Expansion) | Full hospital | $800 | $240K/month |
| Year 3+ (Mature) | Full hospital + outcome bonus | $1,200 | $360K/month |

**Year 1 target: 50-bed ICU pilot**
```
$500 × 50 beds × 12 months = $300K ARR
```

**Year 2 target: full hospital expansion**
```
$800 × 300 beds × 12 months = $2.88M ARR
```

**Year 3 target: full hospital + outcome-based bonus**
```
Base: $1,200 × 300 beds × 12 months = $4.32M ARR
+ Outcome bonus: 15% of documented CMS penalty savings
= ~$5M ARR per hospital
```

### Alternative Pricing Models

**Outcome-Based Pricing:**
- Base fee: $300/bed/month
- Outcome bonus: 20% of documented readmission reduction savings
- Risk/Reward: Hospital pays less if AI doesn't deliver
- Your risk: Requires accurate attribution methodology

**Hybrid (Recommended for Year 2+):**
- Base fee: $600/bed/month
- Outcome bonus: 10% of documented savings
- Balances revenue predictability with outcome alignment
- CFO loves it because it aligns your incentives with theirs

---

## The Series A Story

### Target Metrics for Raise (Month 18–24)

```
Revenue:    $15M ARR (10 hospitals at average $1.5M ARR)
Growth:     3× year-over-year
Hospitals:  10 contracted, 3 fully deployed
Pipeline:   20 hospitals in active evaluation
Retention:  100% (no churned hospitals)
FDA:        510(k) clearance received for sepsis prediction
Data moat:  500K+ patient encounters, 50K+ outcomes tracked
```

### Why These Metrics = Series A

1. **$15M ARR at 3×** = entering institutional venture territory
2. **100% retention** = proof the product works in production
3. **FDA clearance** = defensible moat (18-month regulatory barrier to entry)
4. **Data moat** = model performance competitors cannot replicate without your data
5. **Outcome data** = ROI proof that CFOs can bring to their boards

### The Narrative (One Paragraph for Series A Deck)

> "CliniQAI is building the largest outcome-validated clinical intelligence network for community hospitals. While competitors sell AI features, we're building a compounding data moat: every hospital we deploy in generates outcome data that makes our models smarter, creating better performance that attracts the next hospital. With FDA-cleared sepsis prediction and $15M ARR growing 3× annually from 10 hospitals, we're 2 years ahead of any new entrant who would need to replicate our outcome dataset. We're raising a Series A to expand to 50 hospitals in 24 months, crossing $50M ARR, and establishing the category-defining clinical AI platform for the 1,200 community hospitals in the US."

---

## Hospital Sales Process

### The 9-Step Hospital Sales Cycle

```
Month 1:    IDENTIFY
            • Target: 200–400 bed community hospital
            • Has Epic (32% market share)
            • CMS readmission penalties >$500K/year
            • No existing AI initiative
            
Month 2-3:  INITIAL CONTACT
            • Entry point: CMO or CMIO (Chief Medical Informatics Officer)
            • NOT the CIO (technical gatekeepers delay, not enable)
            • LinkedIn → conference introduction → warm intro preferred
            • Message: "We reduced sepsis mortality 18% at our pilot hospital. 
                       15-minute call to see if relevant for you?"
                       
Month 3-4:  DISCOVERY CALL
            • 30-minute call with CMO
            • Ask: "What's your current sepsis bundle compliance rate?"
            • Ask: "What are your top 3 quality measure gaps?"
            • Ask: "What did your CMS readmission penalties total last year?"
            • Don't pitch yet. Listen. Take notes.
            
Month 4-5:  VALUE QUANTIFICATION
            • Return with a hospital-specific financial model
            • "Based on your 18% COPD readmission rate and 300 beds,
               we estimate $380K annual CMS penalty exposure.
               Our pilot at [reference hospital] reduced COPD readmissions 22%."
            • Present the ROI model to CFO directly
            
Month 5-6:  CLINICAL CHAMPION
            • CMO arranges demo for 5–10 ICU physicians
            • Live demo with realistic synthetic data
            • Focus on: sepsis alert, drug interaction catch, AI reasoning
            • Goal: get 2–3 physicians to say "I would use this"
            
Month 6-8:  TECHNICAL REVIEW
            • CIO reviews security documentation
            • Provide: SOC 2 Type II report, HIPAA attestation, BAA draft
            • Epic App Orchard certification status
            • Penetration test results
            • Typical CIO question: "How do we turn it off if there's a problem?"
            • Answer: "One click. We designed for physician control."
            
Month 8-9:  CONTRACT NEGOTIATION
            • Legal reviews BAA
            • 90-day pilot structure agreed
            • Success metric defined (not by us)
            • Data use agreement (right to de-identified data for model improvement)
            • IP assignment: hospital owns their de-identified outcome data
            
Month 9:    SIGNED
            • Celebrate briefly
            • Start technical onboarding same week
            • Assign dedicated Customer Success Manager
            
Month 12:   PILOT RESULTS + EXPANSION PROPOSAL
            • Present results to CMO + CFO + Board (if invited)
            • Expansion proposal: full hospital
            • Year 2 contract at full pricing
```

### What to NEVER Do in Hospital Sales

1. **Never promise specific clinical outcomes** before you have data
2. **Never pitch the technology first** — pitch the problem you solve
3. **Never go to IT first** — they will table it for 18 months
4. **Never present a generic demo** — always customize to their data/metrics
5. **Never use "AI" in your first sentence** — say "clinical decision support"
6. **Never ignore Legal/Compliance** — one HIPAA concern kills the deal
7. **Never underestimate procurement timelines** — hospitals are slow by design

---

## Data Moat Strategy

### How to Structure Your Data Rights

Include in ALL hospital contracts:

```
"Hospital grants CliniQAI the right to use de-identified, 
aggregated patient data (rendered compliant with HIPAA Safe Harbor 
per 45 CFR §164.514(b)) for the purpose of improving the platform's 
AI models, subject to the following conditions:

1. All data is de-identified prior to use in model training
2. Hospital-specific data is never disclosed to other hospitals
3. Model improvements benefit all hospitals in the network
4. Hospital may opt out of data sharing with 30 days notice
5. Upon termination, CliniQAI will delete all hospital-derived data"
```

### The Compounding Advantage

```
Hospital 1:   24,000 patient encounters/year
Hospital 5:   120,000 encounters/year → model 40% better than Hospital 1 model
Hospital 10:  240,000 encounters/year → 3-year entrant moat
Hospital 50:  1.2M encounters/year → uncopyable within 5 years
```

This is why you **must** get to 10 hospitals before well-capitalized competitors.

After 10 hospitals:
- Your sepsis model outperforms any newly trained model
- Outcome data proves ROI in new hospital pitches
- Hospital 11 sees Hospital 10's results and wants in
- The moat compounds. New entrants cannot replicate 3 years of outcome data.

---

## Competitive Analysis

| Competitor | Strength | Weakness | Our Edge |
|-----------|---------|----------|---------|
| Epic Sepsis Model | Already in Epic workflow | Mediocre AUROC 0.63 (published), high false positives | Our AUROC 0.87, 66% lower false positive rate |
| IBM Watson Health | Brand, sales force | Abandoned hospital AI business (2022) | Still operating, focused on community hospitals |
| Microsoft Dragon | Voice documentation | Not a clinical AI platform | Complementary, not competing |
| Viz.ai | FDA-cleared imaging AI | Single modality (neuro imaging) | Multi-modal: vitals + NLP + imaging |
| Carevue / Cerner AI | EHR-native | Locked to single EHR | EHR-agnostic, works with Epic + Cerner + Meditech |
| New entrants | None yet at scale | 3-year data moat gap | Already building moat |

**The real competition is inertia.** Most community hospitals do nothing. Your job is to make doing nothing look more expensive than doing something.

---

*This playbook is reviewed quarterly. Last reviewed: Q1 2026.*  
*Questions: growth@cliniqai.com*
