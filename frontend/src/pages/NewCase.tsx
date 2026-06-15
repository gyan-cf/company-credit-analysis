import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { createCase } from '../api'

const UEN_RE = /^(?:\d{8}[A-Z]|\d{9}[A-Z]|[TSR]\d{2}[A-Z]{2}\d{4}[A-Z])$/

const industryOptions = [
  { code: 'services', label: 'Services', hint: 'Services' },
  { code: 'manufacturing', label: 'Manufacturing', hint: 'Manufacturing' },
  { code: 'generic', label: 'General corporate', hint: 'Generic' },
]

const entityTypes = [
  'Private Company Limited by Shares',
  'Exempt Private Company Limited by Shares',
  'Limited Liability Partnership',
  'Sole Proprietorship',
  'Public Company Limited by Shares',
]

const facilityTypes = [
  'Working Capital Facility',
  'Trade Finance',
  'Term Loan',
  'Invoice Financing',
  'Revolving Credit Facility',
]

function cleanUen(value: string): string {
  return value.replace(/[\s-]/g, '').toUpperCase()
}

export default function NewCase() {
  const nav = useNavigate()
  const [form, setForm] = useState({
    company_name: '',
    uen: '',
    entity_type: entityTypes[0],
    company_status: 'Live',
    incorporation_date: '',
    fiscal_year_end: '31 Dec',
    industry_code: 'services',
    industry_hint: 'Services',
    primary_ssic_code: '',
    primary_ssic_desc: '',
    registered_address: '',
    currency: 'SGD',
    facility_type: facilityTypes[0],
    requested_limit: '',
    relationship_manager: '',
    priority: 'normal',
  })
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const fyRange = useMemo(() => {
    const currentYear = new Date().getFullYear()
    return [0, 1, 2].map((offset) => `FY${currentYear - offset - 1}`)
  }, [])

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    const uen = cleanUen(form.uen)
    if (!UEN_RE.test(uen)) {
      setError('Enter a valid Singapore UEN, for example 202037175R or T15LL0001A.')
      return
    }

    setError('')
    setLoading(true)
    try {
      const c = await createCase({
        ...form,
        company_name: form.company_name.trim(),
        uen,
        cin: uen,
        country: 'Singapore',
        jurisdiction: 'Singapore',
        onboarding_stage: 'entity_profile',
        fy_range: fyRange,
      })
      nav(`/cases/${c.case_id}/intake`)
    } catch (err) {
      setError(String(err))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="new-case-page">
      <div className="new-case-hero">
        <div>
          <Link to="/" className="breadcrumb-link">Cases</Link>
          <p className="eyebrow">Singapore borrower onboarding</p>
          <h2>New Credit Case</h2>
          <p className="new-case-lede">
            Capture legal identity, ACRA profile markers and facility context before financial statement intake.
          </p>
        </div>
        <div className="new-case-context">
          <div>
            <span>Jurisdiction</span>
            <strong>Singapore</strong>
          </div>
          <div>
            <span>Currency</span>
            <strong>SGD</strong>
          </div>
          <div>
            <span>Review pack</span>
            <strong>ACRA + FS</strong>
          </div>
        </div>
      </div>

      <form className="new-case-form" onSubmit={submit}>
        <section className="new-case-panel">
          <div className="panel-title-row">
            <div>
              <p className="eyebrow">Borrower identity</p>
              <h3>Company profile</h3>
            </div>
            <span className="panel-chip">Required</span>
          </div>

          <div className="form-grid two">
            <label className="form-field wide">
              <span>Company legal name</span>
              <input
                required
                value={form.company_name}
                placeholder="GoImpact Capital Partners (Singapore) Pte. Ltd."
                onChange={(e) => setForm({ ...form, company_name: e.target.value })}
              />
            </label>

            <label className="form-field">
              <span>UEN</span>
              <input
                required
                value={form.uen}
                placeholder="202037175R"
                onChange={(e) => setForm({ ...form, uen: cleanUen(e.target.value) })}
              />
              <small>Accepted formats include ACRA company and registered-entity UENs.</small>
            </label>

            <label className="form-field">
              <span>Entity type</span>
              <select
                value={form.entity_type}
                onChange={(e) => setForm({ ...form, entity_type: e.target.value })}
              >
                {entityTypes.map((type) => <option key={type} value={type}>{type}</option>)}
              </select>
            </label>

            <label className="form-field">
              <span>Company status</span>
              <select
                value={form.company_status}
                onChange={(e) => setForm({ ...form, company_status: e.target.value })}
              >
                <option value="Live">Live</option>
                <option value="Struck Off">Struck Off</option>
                <option value="In Liquidation">In Liquidation</option>
                <option value="Dormant">Dormant</option>
              </select>
            </label>

            <label className="form-field">
              <span>Incorporation date</span>
              <input
                type="date"
                value={form.incorporation_date}
                onChange={(e) => setForm({ ...form, incorporation_date: e.target.value })}
              />
            </label>

            <label className="form-field">
              <span>Financial year end</span>
              <input
                value={form.fiscal_year_end}
                placeholder="31 Dec"
                onChange={(e) => setForm({ ...form, fiscal_year_end: e.target.value })}
              />
            </label>
          </div>
        </section>

        <section className="new-case-panel">
          <div className="panel-title-row">
            <div>
              <p className="eyebrow">Credit request</p>
              <h3>Facility context</h3>
            </div>
            <span className="panel-chip muted">SGD</span>
          </div>

          <div className="form-grid two">
            <label className="form-field">
              <span>Facility type</span>
              <select
                value={form.facility_type}
                onChange={(e) => setForm({ ...form, facility_type: e.target.value })}
              >
                {facilityTypes.map((type) => <option key={type} value={type}>{type}</option>)}
              </select>
            </label>

            <label className="form-field">
              <span>Requested limit</span>
              <div className="input-with-prefix">
                <span>S$</span>
                <input
                  inputMode="numeric"
                  value={form.requested_limit}
                  placeholder="500000"
                  onChange={(e) => setForm({ ...form, requested_limit: e.target.value })}
                />
              </div>
            </label>

            <label className="form-field">
              <span>Relationship manager</span>
              <input
                value={form.relationship_manager}
                placeholder="Credit analyst or RM"
                onChange={(e) => setForm({ ...form, relationship_manager: e.target.value })}
              />
            </label>

            <label className="form-field">
              <span>Priority</span>
              <select
                value={form.priority}
                onChange={(e) => setForm({ ...form, priority: e.target.value })}
              >
                <option value="normal">Normal</option>
                <option value="urgent">Urgent</option>
                <option value="watchlist">Watchlist</option>
              </select>
            </label>
          </div>
        </section>

        <section className="new-case-panel">
          <div className="panel-title-row">
            <div>
              <p className="eyebrow">ACRA and industry</p>
              <h3>Operating profile</h3>
            </div>
            <span className="panel-chip muted">Optional</span>
          </div>

          <div className="form-grid two">
            <label className="form-field">
              <span>Industry</span>
              <select
                value={form.industry_code}
                onChange={(e) => {
                  const selected = industryOptions.find((opt) => opt.code === e.target.value)
                  setForm({
                    ...form,
                    industry_code: e.target.value,
                    industry_hint: selected?.hint || e.target.value,
                  })
                }}
              >
                {industryOptions.map((opt) => <option key={opt.code} value={opt.code}>{opt.label}</option>)}
              </select>
            </label>

            <label className="form-field">
              <span>Primary SSIC code</span>
              <input
                inputMode="numeric"
                maxLength={5}
                value={form.primary_ssic_code}
                placeholder="85409"
                onChange={(e) => setForm({ ...form, primary_ssic_code: e.target.value.replace(/\D/g, '') })}
              />
            </label>

            <label className="form-field wide">
              <span>Primary SSIC description</span>
              <input
                value={form.primary_ssic_desc}
                placeholder="Training courses n.e.c."
                onChange={(e) => setForm({ ...form, primary_ssic_desc: e.target.value })}
              />
            </label>

            <label className="form-field wide">
              <span>Registered office address</span>
              <textarea
                rows={3}
                value={form.registered_address}
                placeholder="10 Anson Road, #20-05 International Plaza, Singapore 079903"
                onChange={(e) => setForm({ ...form, registered_address: e.target.value })}
              />
            </label>
          </div>
        </section>

        <aside className="new-case-side-panel">
          <div className="side-card">
            <p className="eyebrow">Onboarding pack</p>
            <h3>SG case markers</h3>
            <ul className="case-marker-list">
              <li><strong>UEN</strong><span>Legal identity and ACRA matching key</span></li>
              <li><strong>SSIC</strong><span>Sector peer set and industry narrative</span></li>
              <li><strong>FYE</strong><span>Financial statement period alignment</span></li>
              <li><strong>Facility</strong><span>Exposure framing for memo generation</span></li>
            </ul>
          </div>

          <div className="side-card quiet">
            <p className="eyebrow">Expected uploads</p>
            <div className="upload-pack">
              {fyRange.map((fy) => <span key={fy}>{fy}</span>)}
            </div>
            <p className="side-note">Financial statements, ACRA business profile and annual return details can be reconciled during document intake.</p>
          </div>

          {error && <div className="form-error">{error}</div>}

          <div className="form-actions">
            <Link to="/" className="secondary-button">Cancel</Link>
            <button type="submit" className="primary" disabled={loading}>
              {loading ? 'Creating...' : 'Create Case'}
            </button>
          </div>
        </aside>
      </form>
    </div>
  )
}
