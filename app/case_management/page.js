'use client';
import { useState, useEffect, useRef } from 'react';
import AppLayout from '../../components/AppLayout';

const API = process.env.NEXT_PUBLIC_API_URL || 'https://api-prod.docuaction.io';

const RISK_COLORS = {
  LOW: { color: '#16A34A', bg: '#F0FDF4' },
  MODERATE: { color: '#D97706', bg: '#FFFBEB' },
  HIGH: { color: '#EA580C', bg: '#FFF7ED' },
  COMPLEX: { color: '#DC2626', bg: '#FEF2F2' },
};

const MODULES = [
  { id: 'voice_note', label: '🎙 Voice → CCM Note', desc: 'Record a call, get a billable note in 15 sec', badge: 'WOW' },
  { id: 'care_plan', label: '📋 Care Plan', desc: 'Generate SMART-goal care plans', badge: 'AI' },
  { id: 'discharge', label: '🏥 Discharge Summary', desc: 'Joint Commission compliant', badge: 'AI' },
  { id: 'education', label: '📖 Patient Education', desc: '6th grade level, multilingual', badge: 'AI' },
  { id: 'tcm', label: '🔄 TCM Note', desc: 'Transitional Care 99495/99496', badge: 'AI' },
  { id: 'sdoh', label: '🏘 SDOH Assessment', desc: 'AHC HRSN screening narrative', badge: 'AI' },
  { id: 'gov_case', label: '🏛 Government Case', desc: 'CMS appeals, VA, FWA investigations', badge: 'FEDERAL' },
  { id: 'billing', label: '💰 Billing Codes', desc: 'CPT code determination + revenue calc', badge: 'RCM' },
];

const CPT_RATES = {
  '99490': 66.13, '99439': 50.44, '99491': 88.90, '99437': 65.77,
  '99487': 131.56, '99489': 71.49, '99495': 211.16, '99496': 278.04,
  '99424': 95.00, '99426': 76.00,
};

function Badge({ label, type }) {
  const styles = {
    WOW:     { bg: '#EEF2FF', color: '#4338CA' },
    AI:      { bg: '#F0FDF4', color: '#16A34A' },
    FEDERAL: { bg: '#EFF6FF', color: '#1D4ED8' },
    RCM:     { bg: '#FFF7ED', color: '#C2410C' },
  };
  const s = styles[type] || styles.AI;
  return (
    <span className="text-[8px] font-bold px-1.5 py-0.5 rounded-full" style={{ backgroundColor: s.bg, color: s.color }}>
      {label}
    </span>
  );
}

function Spinner() {
  return (
    <svg className="animate-spin w-4 h-4" viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" strokeOpacity="0.2" />
      <path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

// ── Voice Note Form ──────────────────────────────────────────────────────────
function VoiceNoteForm({ onResult }) {
  const [transcript, setTranscript] = useState('');
  const [minutes, setMinutes] = useState(20);
  const [providerType, setProviderType] = useState('clinical_staff');
  const [complexity, setComplexity] = useState('non_complex');
  const [cumulativeMinutes, setCumulativeMinutes] = useState(0);
  const [caseManager, setCaseManager] = useState('');
  const [loading, setLoading] = useState(false);
  const [patientCtx, setPatientCtx] = useState({
    first_name: 'Sarah', last_name: 'Johnson',
    diagnoses_icd10: ['E11.9', 'I10', 'E78.5'],
    risk_tier: 'HIGH', medications: [],
  });

  const DEMO_TRANSCRIPT = `Spoke with Mrs. Johnson today regarding her diabetes management. She reports her blood sugars have been running higher than usual this week, ranging from 180 to 250 in the mornings. She mentioned she ran out of metformin three days ago and hasn't been able to get to the pharmacy. She's also been having increased fatigue and some swelling in her ankles which may be related to her blood pressure medications. She missed her cardiology appointment last week due to transportation issues. Her daughter usually drives her but has been out of town. We discussed the importance of medication adherence and I will arrange a medication delivery through her pharmacy. I also reached out to her cardiologist's office to reschedule the appointment. Discussed warning signs of hyperglycemia and provided instructions on when to go to the ER. Patient verbalized understanding. Total time spent: 22 minutes.`;

  const handleSubmit = async () => {
    if (!transcript.trim()) return;
    setLoading(true);
    try {
      const r = await fetch(`${API}/api/v1/case-management/notes/voice-to-note`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          patient_context: { ...patientCtx, first_name: patientCtx.first_name || 'Patient' },
          voice_transcript: transcript,
          case_manager_name: caseManager || 'Case Manager',
          total_minutes: parseInt(minutes),
          provider_type: providerType,
          complexity,
          cumulative_minutes_this_month: parseInt(cumulativeMinutes),
          service_date: new Date().toISOString().split('T')[0],
        }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      onResult(data);
    } catch (e) {
      onResult({ error: e.message });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 text-[10px] text-blue-700">
        <strong>WOW FACTOR:</strong> Paste a voice transcript below — the AI generates a complete, billing-compliant CCM note with CPT code and reimbursement estimate in under 30 seconds.
      </div>

      <div className="grid grid-cols-3 gap-3">
        <div>
          <label className="text-[9px] font-semibold text-[#64748B] block mb-1">Patient First Name</label>
          <input className="w-full border border-[#E2E8F0] rounded px-2 py-1.5 text-[10px]"
            value={patientCtx.first_name} onChange={e => setPatientCtx(p => ({ ...p, first_name: e.target.value }))} />
        </div>
        <div>
          <label className="text-[9px] font-semibold text-[#64748B] block mb-1">Patient Last Name</label>
          <input className="w-full border border-[#E2E8F0] rounded px-2 py-1.5 text-[10px]"
            value={patientCtx.last_name} onChange={e => setPatientCtx(p => ({ ...p, last_name: e.target.value }))} />
        </div>
        <div>
          <label className="text-[9px] font-semibold text-[#64748B] block mb-1">Case Manager Name</label>
          <input className="w-full border border-[#E2E8F0] rounded px-2 py-1.5 text-[10px]"
            placeholder="Your name" value={caseManager} onChange={e => setCaseManager(e.target.value)} />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="text-[9px] font-semibold text-[#64748B] block mb-1">Diagnoses (ICD-10)</label>
          <input className="w-full border border-[#E2E8F0] rounded px-2 py-1.5 text-[10px]"
            placeholder="E11.9, I10, E78.5"
            value={patientCtx.diagnoses_icd10?.join(', ')}
            onChange={e => setPatientCtx(p => ({ ...p, diagnoses_icd10: e.target.value.split(',').map(s => s.trim()) }))} />
        </div>
        <div>
          <label className="text-[9px] font-semibold text-[#64748B] block mb-1">Risk Tier</label>
          <select className="w-full border border-[#E2E8F0] rounded px-2 py-1.5 text-[10px]"
            value={patientCtx.risk_tier} onChange={e => setPatientCtx(p => ({ ...p, risk_tier: e.target.value }))}>
            {['LOW', 'MODERATE', 'HIGH', 'COMPLEX'].map(t => <option key={t}>{t}</option>)}
          </select>
        </div>
      </div>

      <div className="grid grid-cols-4 gap-3">
        <div>
          <label className="text-[9px] font-semibold text-[#64748B] block mb-1">Minutes This Visit</label>
          <input type="number" min="1" max="120" className="w-full border border-[#E2E8F0] rounded px-2 py-1.5 text-[10px]"
            value={minutes} onChange={e => setMinutes(e.target.value)} />
        </div>
        <div>
          <label className="text-[9px] font-semibold text-[#64748B] block mb-1">Cumulative This Month</label>
          <input type="number" min="0" className="w-full border border-[#E2E8F0] rounded px-2 py-1.5 text-[10px]"
            value={cumulativeMinutes} onChange={e => setCumulativeMinutes(e.target.value)} />
        </div>
        <div>
          <label className="text-[9px] font-semibold text-[#64748B] block mb-1">Provider Type</label>
          <select className="w-full border border-[#E2E8F0] rounded px-2 py-1.5 text-[10px]"
            value={providerType} onChange={e => setProviderType(e.target.value)}>
            <option value="clinical_staff">Clinical Staff</option>
            <option value="physician_npp">Physician / NPP</option>
          </select>
        </div>
        <div>
          <label className="text-[9px] font-semibold text-[#64748B] block mb-1">Complexity</label>
          <select className="w-full border border-[#E2E8F0] rounded px-2 py-1.5 text-[10px]"
            value={complexity} onChange={e => setComplexity(e.target.value)}>
            <option value="non_complex">Non-Complex</option>
            <option value="complex">Complex</option>
          </select>
        </div>
      </div>

      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="text-[9px] font-semibold text-[#64748B]">Voice Transcript / Call Notes</label>
          <button className="text-[9px] text-[#2563EB] hover:underline"
            onClick={() => setTranscript(DEMO_TRANSCRIPT)}>Load Demo Transcript</button>
        </div>
        <textarea
          rows={6}
          className="w-full border border-[#E2E8F0] rounded px-3 py-2 text-[10px] text-[#0F172A] resize-none"
          placeholder="Paste voice transcript or type call notes here..."
          value={transcript}
          onChange={e => setTranscript(e.target.value)}
        />
        <div className="text-[8px] text-[#CBD5E1] mt-0.5">{transcript.length} characters</div>
      </div>

      <button
        onClick={handleSubmit}
        disabled={loading || !transcript.trim()}
        className="w-full py-2.5 rounded-lg text-[11px] font-bold text-white flex items-center justify-center gap-2 transition-all disabled:opacity-50"
        style={{ backgroundColor: loading ? '#94A3B8' : '#2563EB' }}
      >
        {loading ? <><Spinner /> Generating Note...</> : '⚡ Generate Billable CCM Note'}
      </button>
    </div>
  );
}

// ── Result Display ───────────────────────────────────────────────────────────
function NoteResult({ result }) {
  const [showFull, setShowFull] = useState(false);
  if (!result) return null;
  if (result.error) return (
    <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-[10px] text-red-600">
      Error: {result.error}
    </div>
  );

  const reimbursement = result.estimated_reimbursement || 0;
  const allCodes = result.all_billing_codes || (result.cpt_code ? [result.cpt_code] : []);

  return (
    <div className="space-y-3">
      {/* Billing summary */}
      <div className="grid grid-cols-4 gap-3">
        <div className="bg-white border border-[#E2E8F0] rounded-lg p-3">
          <div className="text-[9px] text-[#94A3B8]">Primary CPT Code</div>
          <div className="text-[18px] font-bold text-[#2563EB] mt-0.5">{result.cpt_code || '—'}</div>
          <div className="text-[8px] text-[#64748B]">{result.cpt_label}</div>
        </div>
        <div className="bg-white border border-[#E2E8F0] rounded-lg p-3">
          <div className="text-[9px] text-[#94A3B8]">Estimated Revenue</div>
          <div className="text-[18px] font-bold text-[#16A34A] mt-0.5">${reimbursement.toFixed(2)}</div>
          <div className="text-[8px] text-[#64748B]">Medicare avg rate</div>
        </div>
        <div className="bg-white border border-[#E2E8F0] rounded-lg p-3">
          <div className="text-[9px] text-[#94A3B8]">Minutes Documented</div>
          <div className="text-[18px] font-bold text-[#0F172A] mt-0.5">{result.total_minutes || '—'}</div>
          <div className="text-[8px] text-[#64748B]">This visit</div>
        </div>
        <div className="bg-white border border-[#E2E8F0] rounded-lg p-3">
          <div className="text-[9px] text-[#94A3B8]">Ready to Bill</div>
          <div className={`text-[18px] font-bold mt-0.5 ${result.ready_to_bill ? 'text-[#16A34A]' : 'text-[#DC2626]'}`}>
            {result.ready_to_bill ? '✓ Yes' : '✗ No'}
          </div>
          <div className="text-[8px] text-[#64748B]">After review</div>
        </div>
      </div>

      {/* Add-on codes */}
      {allCodes.length > 1 && (
        <div className="bg-[#FFFBEB] border border-[#FDE68A] rounded-lg p-3">
          <div className="text-[9px] font-bold text-[#B45309] mb-1">All Billing Codes</div>
          <div className="flex flex-wrap gap-2">
            {allCodes.map(c => (
              <span key={c} className="text-[9px] font-mono font-bold bg-white border border-[#FDE68A] px-2 py-0.5 rounded text-[#B45309]">
                CPT {c} — ${(CPT_RATES[c] || 0).toFixed(2)}
              </span>
            ))}
          </div>
          <div className="text-[9px] font-bold text-[#B45309] mt-2">
            Total: ${allCodes.reduce((s, c) => s + (CPT_RATES[c] || 0), 0).toFixed(2)}
          </div>
        </div>
      )}

      {/* Billing rationale */}
      {result.billing_rationale && (
        <div className="bg-[#F0FDF4] border border-[#BBF7D0] rounded-lg p-3 text-[9px] text-[#15803D]">
          <strong>Billing Rationale:</strong> {result.billing_rationale}
        </div>
      )}

      {/* Risk flags */}
      {result.risk_flags?.length > 0 && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3">
          <div className="text-[9px] font-bold text-[#DC2626] mb-1">⚠ Risk Flags Identified</div>
          {result.risk_flags.map((f, i) => (
            <div key={i} className="text-[9px] text-red-600">• {f}</div>
          ))}
        </div>
      )}

      {/* Action items */}
      {result.action_items?.length > 0 && (
        <div className="bg-white border border-[#E2E8F0] rounded-lg p-3">
          <div className="text-[9px] font-bold text-[#0F172A] mb-1">Action Items</div>
          {result.action_items.map((a, i) => (
            <div key={i} className="text-[9px] text-[#475569] flex gap-2">
              <span className="text-[#2563EB]">→</span>{a}
            </div>
          ))}
        </div>
      )}

      {/* Note body */}
      <div className="bg-white border border-[#E2E8F0] rounded-lg">
        <div className="flex items-center justify-between px-4 py-2 border-b border-[#F1F5F9]">
          <span className="text-[10px] font-bold text-[#0F172A]">Generated Note</span>
          <div className="flex items-center gap-2">
            <span className="text-[8px] text-[#94A3B8]">
              Generated in {result.pipeline_time_seconds || result.ai_generation_time}s · {result.ai_model_used}
            </span>
            <button
              onClick={() => setShowFull(!showFull)}
              className="text-[9px] text-[#2563EB] hover:underline"
            >
              {showFull ? 'Collapse' : 'Expand'}
            </button>
          </div>
        </div>
        <div className={`px-4 py-3 overflow-hidden transition-all ${showFull ? '' : 'max-h-48'}`}>
          <pre className="text-[9px] text-[#475569] whitespace-pre-wrap leading-relaxed font-sans">
            {result.note_body}
          </pre>
        </div>
      </div>

      {/* AI disclosure */}
      {result.ai_disclosure && (
        <div className="text-[8px] text-[#CBD5E1] italic text-center">{result.ai_disclosure}</div>
      )}

      {/* Approve button */}
      <div className="flex gap-3">
        <button className="flex-1 py-2 rounded-lg text-[10px] font-semibold text-white bg-[#16A34A] hover:bg-green-700 transition">
          ✓ Approve & Sign Note
        </button>
        <button className="px-4 py-2 rounded-lg text-[10px] font-semibold text-[#475569] bg-[#F1F5F9] hover:bg-gray-200 transition">
          ✏ Edit
        </button>
        <button className="px-4 py-2 rounded-lg text-[10px] font-semibold text-[#475569] bg-[#F1F5F9] hover:bg-gray-200 transition">
          ↓ Export PDF
        </button>
      </div>
    </div>
  );
}

// ── Billing Calculator ───────────────────────────────────────────────────────
function BillingCalculator() {
  const [minutes, setMinutes] = useState(25);
  const [cumulative, setCumulative] = useState(0);
  const [providerType, setProviderType] = useState('clinical_staff');
  const [complexity, setComplexity] = useState('non_complex');
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);

  const calculate = async () => {
    setLoading(true);
    try {
      const r = await fetch(`${API}/api/v1/case-management/billing/determine-code`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          total_minutes: parseInt(minutes),
          provider_type: providerType,
          note_type: 'CCM_PROGRESS',
          complexity,
          cumulative_minutes_this_month: parseInt(cumulative),
        }),
      });
      const data = await r.json();
      setResult(data);
    } catch (e) {
      setResult({ error: e.message });
    } finally {
      setLoading(false);
    }
  };

  const totalMinutes = parseInt(minutes) + parseInt(cumulative);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="text-[9px] font-semibold text-[#64748B] block mb-1">Minutes This Visit</label>
          <input type="number" min="1" max="120" className="w-full border border-[#E2E8F0] rounded px-2 py-1.5 text-[10px]"
            value={minutes} onChange={e => setMinutes(e.target.value)} />
        </div>
        <div>
          <label className="text-[9px] font-semibold text-[#64748B] block mb-1">Prior Minutes This Month</label>
          <input type="number" min="0" className="w-full border border-[#E2E8F0] rounded px-2 py-1.5 text-[10px]"
            value={cumulative} onChange={e => setCumulative(e.target.value)} />
        </div>
        <div>
          <label className="text-[9px] font-semibold text-[#64748B] block mb-1">Provider Type</label>
          <select className="w-full border border-[#E2E8F0] rounded px-2 py-1.5 text-[10px]"
            value={providerType} onChange={e => setProviderType(e.target.value)}>
            <option value="clinical_staff">Clinical Staff</option>
            <option value="physician_npp">Physician / NPP</option>
          </select>
        </div>
        <div>
          <label className="text-[9px] font-semibold text-[#64748B] block mb-1">Complexity</label>
          <select className="w-full border border-[#E2E8F0] rounded px-2 py-1.5 text-[10px]"
            value={complexity} onChange={e => setComplexity(e.target.value)}>
            <option value="non_complex">Non-Complex</option>
            <option value="complex">Complex</option>
          </select>
        </div>
      </div>

      <div className="bg-[#F8FAFC] rounded-lg p-3 text-center">
        <div className="text-[11px] text-[#94A3B8]">Total Monthly Minutes</div>
        <div className={`text-[28px] font-bold ${totalMinutes >= 20 ? 'text-[#16A34A]' : 'text-[#DC2626]'}`}>
          {totalMinutes}
        </div>
        <div className="text-[9px] text-[#94A3B8]">{totalMinutes >= 20 ? 'Billable ✓' : 'Not yet billable (need 20 min)'}</div>
      </div>

      <button onClick={calculate} disabled={loading}
        className="w-full py-2 rounded-lg text-[11px] font-bold text-white flex items-center justify-center gap-2 transition-all disabled:opacity-50"
        style={{ backgroundColor: '#2563EB' }}>
        {loading ? <><Spinner /> Calculating...</> : '💰 Calculate Billing Codes'}
      </button>

      {result && !result.error && (
        <div className="space-y-2">
          <div className="bg-[#F0FDF4] border border-[#BBF7D0] rounded-lg p-3">
            <div className="text-[11px] font-bold text-[#15803D]">
              Primary Code: CPT {result.primary_cpt_code || 'No billable code'}
            </div>
            {result.addon_cpt_codes?.length > 0 && (
              <div className="text-[9px] text-[#16A34A] mt-0.5">
                Add-ons: {result.addon_cpt_codes.map(c => `CPT ${c}`).join(', ')}
              </div>
            )}
            <div className="text-[14px] font-bold text-[#16A34A] mt-1">
              Estimated: ${result.estimated_reimbursement?.toFixed(2) || '0.00'}
            </div>
          </div>
          <div className="text-[9px] text-[#64748B] bg-[#F8FAFC] rounded p-2">
            {result.billing_rationale}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────
export default function CaseManagementPage() {
  const [activeModule, setActiveModule] = useState('voice_note');
  const [noteResult, setNoteResult] = useState(null);
  const [moduleInfo, setModuleInfo] = useState(null);

  useEffect(() => {
    fetch(`${API}/api/v1/case-management/info`)
      .then(r => r.json())
      .then(setModuleInfo)
      .catch(() => {});
  }, []);

  const handleNoteResult = (result) => {
    setNoteResult(result);
  };

  return (
    <AppLayout>
      <div className="max-w-[1200px] mx-auto space-y-4">

        {/* ── Header ─── */}
        <div className="rounded-xl overflow-hidden" style={{ background: 'linear-gradient(135deg, #0F172A 0%, #1A3557 100%)' }}>
          <div className="p-5 flex items-start justify-between">
            <div>
              <div className="text-[10px] font-semibold text-green-300 mb-1 uppercase tracking-wider">
                Module 11 · DocuAction AI
              </div>
              <h1 className="text-[18px] font-bold text-white">Case Management</h1>
              <p className="text-[11px] text-blue-200 mt-1">
                CCM · TCM · PCM · Clinical CM · Discharge Planning · Government Cases
              </p>
              <div className="flex flex-wrap gap-2 mt-3">
                {['CCM 99490–99489', 'TCM 99495/99496', 'PCM 99424–99427', 'Joint Commission', 'CMS CoP §482.43', '42 CFR Part 2'].map(b => (
                  <span key={b} className="text-[8px] font-medium px-2 py-0.5 rounded bg-white/10 text-white/70">{b}</span>
                ))}
              </div>
            </div>
            <div className="text-right text-[9px] text-blue-300 space-y-1 flex-shrink-0 ml-4">
              <div className="text-[11px] font-bold text-green-300">$66–$278/patient/month</div>
              <div>Medicare reimbursement range</div>
              <div className="text-[8px] text-blue-400 mt-1">Voice → billable note in 15 sec</div>
            </div>
          </div>
          <div className="grid grid-cols-5 border-t border-white/10">
            {[
              { v: 'CCM', d: 'Chronic Care Mgmt' },
              { v: 'TCM', d: 'Transitional CM' },
              { v: 'PCM', d: 'Principal CM' },
              { v: 'Discharge', d: 'Joint Commission' },
              { v: 'Gov Cases', d: 'Federal/State' },
            ].map((s, i) => (
              <div key={i} className="p-3 border-r border-white/10 last:border-0 text-center">
                <div className="text-[12px] font-bold text-white">{s.v}</div>
                <div className="text-[8px] text-blue-300 mt-0.5">{s.d}</div>
              </div>
            ))}
          </div>
        </div>

        {/* ── Module selector ─── */}
        <div className="grid grid-cols-4 gap-3">
          {MODULES.map(m => (
            <button key={m.id} onClick={() => { setActiveModule(m.id); setNoteResult(null); }}
              className={`rounded-lg border p-3 text-left transition-all ${
                activeModule === m.id ? 'border-[#2563EB] bg-[#EFF6FF]' : 'border-[#E2E8F0] bg-white hover:border-[#93C5FD]'
              }`}>
              <div className="flex items-start justify-between mb-1">
                <span className="text-[11px]">{m.label.slice(0, 3)}</span>
                <Badge label={m.badge} type={m.badge} />
              </div>
              <div className="text-[10px] font-semibold text-[#0F172A]">{m.label.slice(4)}</div>
              <div className="text-[8px] text-[#94A3B8] mt-0.5">{m.desc}</div>
            </button>
          ))}
        </div>

        {/* ── Active module content ─── */}
        <div className="bg-white rounded-lg border border-[#E2E8F0] p-4">
          {activeModule === 'voice_note' && (
            <div>
              <h2 className="text-[11px] font-bold text-[#0F172A] mb-3">🎙 Voice Transcript → Billable CCM Note</h2>
              <VoiceNoteForm onResult={handleNoteResult} />
              {noteResult && (
                <div className="mt-6 pt-6 border-t border-[#F1F5F9]">
                  <h3 className="text-[11px] font-bold text-[#0F172A] mb-3">Generated Note & Billing</h3>
                  <NoteResult result={noteResult} />
                </div>
              )}
            </div>
          )}

          {activeModule === 'billing' && (
            <div>
              <h2 className="text-[11px] font-bold text-[#0F172A] mb-3">💰 CCM Billing Code Calculator</h2>
              <BillingCalculator />
            </div>
          )}

          {activeModule === 'care_plan' && (
            <div className="text-center py-12 text-[#94A3B8]">
              <div className="text-4xl mb-3">📋</div>
              <h2 className="text-[12px] font-semibold text-[#0F172A]">Care Plan Generator</h2>
              <p className="text-[10px] mt-1 max-w-sm mx-auto">
                Generate comprehensive SMART-goal care plans. Uses Claude Opus for complex multi-condition patients.
              </p>
              <div className="mt-4 text-[9px] text-[#CBD5E1]">API endpoint: POST /api/v1/case-management/care-plans/generate</div>
              <div className="mt-2 text-[9px] text-[#2563EB]">Full UI coming in Phase 2</div>
            </div>
          )}

          {activeModule === 'discharge' && (
            <div className="text-center py-12 text-[#94A3B8]">
              <div className="text-4xl mb-3">🏥</div>
              <h2 className="text-[12px] font-semibold text-[#0F172A]">Discharge Summary Generator</h2>
              <p className="text-[10px] mt-1 max-w-sm mx-auto">
                Joint Commission RC.02.01.25 compliant. Synthesizes H&P + progress notes + procedure notes into complete discharge summary + patient instructions.
              </p>
              <div className="mt-4 text-[9px] text-[#CBD5E1]">API endpoint: POST /api/v1/case-management/discharge/generate</div>
              <div className="mt-2 text-[9px] text-[#2563EB]">Full UI coming in Phase 2</div>
            </div>
          )}

          {activeModule === 'education' && (
            <div className="text-center py-12 text-[#94A3B8]">
              <div className="text-4xl mb-3">📖</div>
              <h2 className="text-[12px] font-semibold text-[#0F172A]">Patient Education Materials</h2>
              <p className="text-[10px] mt-1 max-w-sm mx-auto">
                6th grade reading level, multilingual (English/Spanish). Section 1557 compliant. Topics include diabetes, heart failure, COPD, hypertension, and more.
              </p>
              <div className="mt-4 text-[9px] text-[#CBD5E1]">API endpoint: POST /api/v1/case-management/education/generate</div>
              <div className="mt-2 text-[9px] text-[#2563EB]">Full UI coming in Phase 2</div>
            </div>
          )}

          {activeModule === 'tcm' && (
            <div className="text-center py-12 text-[#94A3B8]">
              <div className="text-4xl mb-3">🔄</div>
              <h2 className="text-[12px] font-semibold text-[#0F172A]">Transitional Care Management</h2>
              <p className="text-[10px] mt-1 max-w-sm mx-auto">
                CPT 99495 (14-day) and 99496 (7-day). Documents discharge review, initial contact within 2 business days, face-to-face visit, medication reconciliation.
              </p>
              <div className="mt-4 text-[9px] text-[#CBD5E1]">API endpoint: POST /api/v1/case-management/notes/tcm</div>
              <div className="mt-2 text-[9px] text-[#2563EB]">Full UI coming in Phase 2</div>
            </div>
          )}

          {activeModule === 'sdoh' && (
            <div className="text-center py-12 text-[#94A3B8]">
              <div className="text-4xl mb-3">🏘</div>
              <h2 className="text-[12px] font-semibold text-[#0F172A]">SDOH Assessment</h2>
              <p className="text-[10px] mt-1 max-w-sm mx-auto">
                AHC HRSN screening narrative. Identifies food security, housing, transportation, utilities, social isolation, safety, and financial concerns.
              </p>
              <div className="mt-4 text-[9px] text-[#CBD5E1]">API endpoint: POST /api/v1/case-management/sdoh/assess</div>
              <div className="mt-2 text-[9px] text-[#2563EB]">Full UI coming in Phase 2</div>
            </div>
          )}

          {activeModule === 'gov_case' && (
            <div className="text-center py-12 text-[#94A3B8]">
              <div className="text-4xl mb-3">🏛</div>
              <h2 className="text-[12px] font-semibold text-[#0F172A]">Government Case Management</h2>
              <p className="text-[10px] mt-1 max-w-sm mx-auto">
                Medicare/Medicaid appeals, VA benefits, FWA investigations, Medicaid eligibility, CMS complaints. Uses Claude Opus for complex investigations.
              </p>
              <div className="mt-4 text-[9px] text-[#CBD5E1]">API: POST /api/v1/case-management/government/cases/generate</div>
              <div className="mt-2 text-[9px] text-[#2563EB]">Full UI coming in Phase 2</div>
            </div>
          )}
        </div>

        {/* ── CPT Reference ─── */}
        <div className="bg-white rounded-lg border border-[#E2E8F0] p-4">
          <h3 className="text-[10px] font-bold text-[#0F172A] uppercase tracking-wide mb-3">2026 CCM/TCM CPT Reimbursement Reference</h3>
          <div className="grid grid-cols-5 gap-2">
            {Object.entries(CPT_RATES).map(([code, rate]) => (
              <div key={code} className="bg-[#F8FAFC] rounded p-2 text-center border border-[#E2E8F0]">
                <div className="text-[10px] font-bold text-[#2563EB]">CPT {code}</div>
                <div className="text-[12px] font-bold text-[#16A34A] mt-0.5">${rate.toFixed(2)}</div>
                <div className="text-[7px] text-[#94A3B8] mt-0.5">Medicare avg</div>
              </div>
            ))}
          </div>
          <p className="text-[8px] text-[#CBD5E1] mt-2 italic text-center">
            CMS increased CCM reimbursement ~10% for 2026. Rates vary by locality. Always verify with your MAC.
          </p>
        </div>

        {/* ── Module info ─── */}
        {moduleInfo && (
          <div className="bg-[#0F172A] rounded-xl p-4 text-white">
            <div className="text-[9px] font-bold text-white/50 uppercase tracking-wider mb-2">Module Status</div>
            <div className="grid grid-cols-3 gap-3">
              <div>
                <div className="text-[8px] text-white/40 mb-1">AI Pipeline</div>
                {Object.entries(moduleInfo.ai_pipeline || {}).map(([k, v]) => (
                  <div key={k} className="text-[8px] text-white/60">• {v}</div>
                ))}
              </div>
              <div>
                <div className="text-[8px] text-white/40 mb-1">Compliance Coverage</div>
                {(moduleInfo.compliance?.regulations || []).slice(0, 5).map((r, i) => (
                  <div key={i} className="text-[8px] text-white/60">• {r}</div>
                ))}
              </div>
              <div>
                <div className="text-[8px] text-white/40 mb-1">AGT Credentials</div>
                <div className="text-[8px] text-white/60">{moduleInfo.agt_credentials?.certifications}</div>
                <div className="text-[8px] text-white/60 mt-1">UEI: {moduleInfo.agt_credentials?.uei}</div>
              </div>
            </div>
          </div>
        )}

      </div>
    </AppLayout>
  );
}
