import api from './client'

// ─── ATS Integrations ─────────────────────────────────────────────────────────

export async function listATSConnections() {
  const response = await api.get('/ats/connections')
  return response.data
}

export async function createATSConnection(data) {
  const response = await api.post('/ats/connections', data)
  return response.data
}

export async function updateATSConnection(id, data) {
  const response = await api.put(`/ats/connections/${id}`, data)
  return response.data
}

export async function deleteATSConnection(id) {
  await api.delete(`/ats/connections/${id}`)
}

export async function pushToATS(connectionId, data) {
  const response = await api.post(`/ats/connections/${connectionId}/push`, data)
  return response.data
}

export async function pullFromATS(connectionId, data) {
  const response = await api.post(`/ats/connections/${connectionId}/pull`, data)
  return response.data
}

export async function getATSSyncLogs(connectionId, limit = 50) {
  const response = await api.get(`/ats/connections/${connectionId}/logs`, { params: { limit } })
  return response.data
}

