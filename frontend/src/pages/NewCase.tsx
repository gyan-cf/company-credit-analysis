import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { createCase } from '../api'

export default function NewCase() {
  const nav = useNavigate()
  const [form, setForm] = useState({
    company_name: '',
    industry_code: 'services',
    industry_hint: 'Services',
    cin: '',
    pan: '',
  })
  const [loading, setLoading] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    try {
      const c = await createCase(form)
      nav(`/cases/${c.case_id}/intake`)
    } catch (err) {
      alert(String(err))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      <h2 style={{ marginBottom: '1rem' }}>New Credit Case</h2>
      <form className="card" onSubmit={submit} style={{ maxWidth: 480 }}>
        <div className="form-row">
          <label>Company Name</label>
          <input
            required
            value={form.company_name}
            onChange={(e) => setForm({ ...form, company_name: e.target.value })}
          />
        </div>
        <div className="form-row">
          <label>Industry</label>
          <select
            value={form.industry_code}
            onChange={(e) =>
              setForm({
                ...form,
                industry_code: e.target.value,
                industry_hint: e.target.options[e.target.selectedIndex].text,
              })
            }
          >
            <option value="generic">Generic</option>
            <option value="manufacturing">Manufacturing</option>
            <option value="services">Services</option>
          </select>
        </div>
        <div className="form-row">
          <label>CIN</label>
          <input value={form.cin} onChange={(e) => setForm({ ...form, cin: e.target.value })} />
        </div>
        <div className="form-row">
          <label>PAN</label>
          <input value={form.pan} onChange={(e) => setForm({ ...form, pan: e.target.value })} />
        </div>
        <button type="submit" className="primary" disabled={loading}>
          {loading ? 'Creating...' : 'Create Case'}
        </button>
      </form>
    </div>
  )
}
