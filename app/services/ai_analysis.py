"""
AI RFQ Analysis & Proposal Generation Service
Uses Claude for analysis and multi-section proposal generation.
Leverages proposal library for context-aware, high-quality output.
"""
import json
import os
from anthropic import Anthropic

_client = None

def _get_client():
    global _client
    if _client is None:
        key = os.getenv("ANTHROPIC_API_KEY", "")
        if not key:
            raise ValueError("ANTHROPIC_API_KEY not set")
        _client = Anthropic(api_key=key)
    return _client


def _call_claude(prompt: str, max_tokens: int = 4096) -> str:
    c = _get_client()
    msg = c.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text


AGT_CONTEXT = """
COMPANY: Alliance Global Tech, Inc. (AGT)
LOCATION: 5457 Twin Knolls Rd, Suite 300, Columbia, MD 21045
PHONE: (443) 832-4278
WEBSITE: www.agtbi.com
CEO: Imran Siddiqui, PMP, SMC, MBA

CERTIFICATIONS:
- SBA 8(a) Business Development Program (Certified)
- CMMI Level 3 (Development & Services)
- ISO 27001 Information Security
- ISO 9001 Quality Management
- ISO 20000 IT Service Management
- DoD Facility Clearance (Active)
- GSA MAS Contract 47QTCA21D003M
- CAGE Code: 8ERE8
- UEI: MP2FLV1MAW93

CORE COMPETENCIES:
- Enterprise Data Management & Analytics (SAS Viya, Power BI, Databricks)
- Cloud Architecture (Azure, AWS GovCloud)
- Application Development & Modernization
- Cybersecurity & RMF/FISMA Compliance
- IT Infrastructure & Help Desk Support
- AI/ML Solutions & Intelligent Automation
- Contact Center Technology (Cisco Finesse, UCCE)

KEY PAST PERFORMANCE:
1. CMS OnePI/OPIDS (10+ years) - Enterprise data management, Oracle Siebel, IBM BIP, Cognos, AWS VDC
2. IRS Contact Center Support - Cisco Finesse development, configuration management
3. U.S. Air Force IT Infrastructure - Network engineering, cybersecurity, classified environments
4. Treasury OIG - IT support services
5. Navy/SPAWAR - Cybersecurity support, DoD 8140 compliance
6. USPTO - IT modernization support

DIFFERENTIATORS:
- 10+ years continuous CMS prime contractor performance
- 8(a) sole-source eligible up to $4.5M
- Full lifecycle capability: requirements → design → build → deploy → operate
- Cleared facility and personnel for classified work
- Hybrid methodology: Agile/Scrum + Waterfall + DevSecOps
"""


# ── ANALYSIS ──

ANALYSIS_PROMPT = """You are an expert government contracting analyst. Analyze this RFQ/SOW document
and return ONLY valid JSON (no markdown, no backticks, no explanation):

{
  "summary": "2-3 sentence summary",
  "title": "RFQ title",
  "agency": "Agency name",
  "solicitation_number": "If found",
  "due_date": "YYYY-MM-DD if found",
  "estimated_value": "Dollar amount if found",
  "naics_code": "NAICS code if found",
  "set_aside": "Set-aside type if found",
  "contract_type": "FFP, T&M, CPFF, IDIQ if found",
  "requirements": [
    {"id": 1, "description": "...", "category": "Technical/Management/Staffing", "priority": "Must/Should/Nice-to-have"}
  ],
  "compliance_requirements": [
    {"requirement": "e.g. TAA", "description": "...", "agt_status": "Met/Partially Met/Gap"}
  ],
  "labor_categories": [
    {"title": "...", "level": "Junior/Mid/Senior", "estimated_hours_monthly": 160, "suggested_rate": 150.00}
  ],
  "deliverables": [
    {"name": "...", "description": "...", "due": "timeline"}
  ],
  "evaluation_criteria": [
    {"factor": "...", "weight": "...", "description": "..."}
  ],
  "risks": [
    {"risk": "...", "severity": "High/Medium/Low", "mitigation": "..."}
  ],
  "agt_strengths": ["strength 1", "strength 2"],
  "recommended_approach": "2-3 paragraphs",
  "win_probability": "High/Medium/Low - brief justification",
  "key_personnel_needed": [
    {"role": "...", "skills": ["..."], "clearance": "if needed"}
  ]
}

""" + AGT_CONTEXT + "\n\nDOCUMENT TO ANALYZE:\n\n"


def analyze_rfq(document_text: str) -> dict:
    """Analyze an RFQ document and return structured JSON."""
    response = _call_claude(ANALYSIS_PROMPT + document_text[:15000])

    # Parse JSON
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        start = response.find('{')
        end = response.rfind('}') + 1
        if start >= 0 and end > start:
            try:
                return json.loads(response[start:end])
            except:
                pass
    return {"summary": response[:500], "raw_analysis": response, "parse_error": True}


# ── PROPOSAL GENERATION (Multi-Section) ──

def generate_section(section_name: str, analysis: dict, library_context: str = "") -> str:
    """Generate one section of a proposal."""
    prompt = f"""You are a senior GovCon proposal writer at Alliance Global Tech, Inc.
Write the {section_name} section for a government proposal response.

WRITING RULES:
- Write in professional, formal government proposal style
- Be specific and detailed — this is for a real submission
- Reference AGT's actual capabilities and certifications
- Use active voice, action-oriented language
- Include specific methodologies, tools, and processes
- Each section should be 2-4 pages when printed
- Do NOT use markdown headers — write flowing prose with clear paragraph breaks
- Use bullet points sparingly and only for lists of items

{AGT_CONTEXT}

RFQ ANALYSIS:
{json.dumps(analysis, indent=2)[:3000]}

{f"REFERENCE FROM PAST WINNING PROPOSALS:{chr(10)}{library_context[:3000]}" if library_context else ""}

Write the {section_name} section now. Make it detailed, specific, and compelling.
"""
    return _call_claude(prompt, max_tokens=4096)


def generate_full_proposal(analysis: dict, library_context: str = "") -> dict:
    """Generate a complete multi-section proposal using multiple AI calls."""

    sections = {}

    # Section 1: Executive Summary
    sections["executive_summary"] = generate_section(
        "Executive Summary",
        analysis,
        library_context
    )

    # Section 2: Technical Approach
    sections["technical_approach"] = generate_section(
        "Technical Approach (addressing all requirements, deliverables, and technical methodology)",
        analysis,
        library_context
    )

    # Section 3: Management Approach
    sections["management_approach"] = generate_section(
        "Management Approach (project management, risk mitigation, quality assurance, communication plan)",
        analysis,
        library_context
    )

    # Section 4: Staffing Plan
    sections["staffing_plan"] = generate_section(
        "Staffing Plan (key personnel, labor categories, organizational chart, transition plan)",
        analysis,
        library_context
    )

    # Section 5: Past Performance
    sections["past_performance"] = generate_section(
        "Past Performance (relevant contracts, performance metrics, lessons learned, client references)",
        analysis,
        library_context
    )

    # Section 6: Compliance Matrix
    sections["compliance"] = generate_section(
        "Compliance Statement and Matrix (regulatory compliance, certifications, security posture)",
        analysis,
        library_context
    )

    # Section 7: Pricing Narrative
    sections["pricing_narrative"] = generate_section(
        "Pricing Narrative (cost methodology, value proposition, pricing transparency, cost controls)",
        analysis,
        library_context
    )

    return sections


def generate_response_draft(analysis: dict) -> str:
    """Generate a single combined response draft (backward compatible)."""
    prompt = f"""You are a senior GovCon proposal writer at Alliance Global Tech, Inc.
Write a comprehensive proposal response covering:
1. Executive Summary
2. Technical Approach
3. Management Approach
4. Staffing Plan
5. Compliance Statement
6. Pricing Narrative

{AGT_CONTEXT}

RFQ Analysis:
{json.dumps(analysis, indent=2)[:4000]}

Write a detailed, professional response. Use clear section headers.
"""
    return _call_claude(prompt, max_tokens=4096)
