import type { AnalysisResult, ClinicalReport, Study, TumourClass, ClassProbabilities, User, JobStatus, StudyEvent, ModelRegistryState, ModelRegistryEntry, RegistrationForm, RegistrationRequest, GeneratedCredentials } from './types';

// ── API CLIENT — swap-in point ──────────────────────────────────────────
// This file is the ONLY place that should know whether responses are mocked
// or real. Every function signature here matches the REST contract in the
// architecture spec (§5). To connect the real backend, set
// NEXT_PUBLIC_USE_MOCK_API=false and NEXT_PUBLIC_API_BASE_URL, and delete
// the mock branch in each function below — component code does not change.

const USE_MOCK = process.env.NEXT_PUBLIC_USE_MOCK_API !== 'false';
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || '/api/v1';

function authHeaders(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}` };
}

/**
 * Any authenticated request that comes back 401 means the token is dead
 * (backend restarted and wiped all sessions, or it was revoked) — not a
 * transient error worth retrying. Clear the stale session and bounce to
 * login instead of leaving the UI showing a confusing generic error
 * message forever (this is exactly what happened when a backend restart
 * invalidated an old session but the UI kept showing "Failed to fetch
 * users" with no indication *why*).
 */
function handleUnauthorized() {
  if (typeof window === 'undefined') return;
  window.localStorage.removeItem('btdm_auth_token');
  window.localStorage.removeItem('btdm_auth_user');
  if (window.location.pathname !== '/login') {
    window.location.href = '/login';
  }
}

/** Session token saved by lib/auth.tsx at login. Every study/report
 * endpoint on the backend now requires it, so calls that previously went
 * out unauthenticated read it from here instead of changing every
 * component's call signature. */
function storedToken(): string | null {
  if (typeof window === 'undefined') return null;
  return window.localStorage.getItem('btdm_auth_token');
}

async function authedFetch(url: string, options: RequestInit = {}): Promise<Response> {
  const token = storedToken();
  const headers = new Headers(options.headers);
  if (token && !headers.has('Authorization')) headers.set('Authorization', `Bearer ${token}`);
  const res = await fetch(url, { ...options, headers });
  if (res.status === 401) {
    handleUnauthorized();
  }
  return res;
}

/**
 * Grad-CAM heatmap image URL for a study. Backend returns a real PNG
 * (actual backprop through the trained CNN branch's last Conv2d layer —
 * see app/models/gradcam.py) once weights are loaded and analysis has
 * run; 404 otherwise. In mock mode there's no backend to hit, so this
 * points at a placeholder that the <GradCamPanel> component treats as
 * "not available" via its onError handler.
 */
export type GradcamMode = 'plain' | 'heatmap' | 'region' | 'both';

export function getGradcamUrl(studyId: string, imageId?: string, mode: GradcamMode = 'both'): string {
  if (USE_MOCK) return `/mock-gradcam-unavailable/${studyId}`;
  const params = new URLSearchParams();
  if (imageId) params.set('image_id', imageId);
  params.set('mode', mode);
  return `${API_BASE}/studies/${studyId}/gradcam?${params.toString()}`;
}

/** Grad-CAM PNGs are behind auth, so a plain <img src> can't load them
 * (it can't send the Authorization header). Fetch with the token and
 * hand back a blob: URL instead. Caller must URL.revokeObjectURL() it. */
export async function fetchGradcamObjectUrl(studyId: string, imageId?: string, mode: GradcamMode = 'both'): Promise<string> {
  if (USE_MOCK) throw new Error('Grad-CAM unavailable in mock mode');
  const res = await fetchGradcam(studyId, imageId, mode);
  return URL.createObjectURL(await res.blob());
}

export async function fetchGradcam(studyId: string, imageId?: string, mode: GradcamMode = 'both'): Promise<Response> {
  const res = await authedFetch(getGradcamUrl(studyId, imageId, mode));
  if (!res.ok) throw new Error(`Grad-CAM not available (${res.status})`);
  return res;
}

/** Backend /health — reports which checkpoint file is actually loaded. */
export async function getHealth(): Promise<{ status: string; checkpoint_id: string | null; expected_checkpoint_id: string; checkpoint_matches_manuscript: boolean; model_locked: boolean; checks: Record<string, boolean> } | null> {
  if (USE_MOCK) return null;
  const base = API_BASE.replace(/\/api\/v1\/?$/, '');
  try {
    const res = await fetch(`${base}/health`);
    return res.ok ? res.json() : null;
  } catch {
    return null;
  }
}

function delay<T>(value: T, ms = 500): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

// -- Mock data -------------------------------------------------------------

const MOCK_STUDIES: Study[] = [
  {
    id: 'study-001',
    patientAlias: 'PT-ALIAS-8841',
    modality: 'MR',
    sequences: ['T1c', 'T2', 'FLAIR'],
    uploadedAt: '2026-07-09T08:12:00Z',
    status: 'report_pending_signoff',
    ohifStudyInstanceUID: '1.2.826.0.1.3680043.8.1055.1.20111102150758591',
    imageIds: ['img-mock-001'],
    skippedFileCount: 0,
    skipReasons: [],
  },
  {
    id: 'study-002',
    patientAlias: 'PT-ALIAS-2290',
    modality: 'MR',
    sequences: ['T1c', 'FLAIR'],
    uploadedAt: '2026-07-08T14:44:00Z',
    status: 'analyzed',
    ohifStudyInstanceUID: '1.2.826.0.1.3680043.8.1055.1.20111102150758592',
    imageIds: ['img-mock-002'],
    skippedFileCount: 0,
    skipReasons: [],
  },
  {
    id: 'study-003',
    patientAlias: 'PT-ALIAS-1075',
    modality: 'MR',
    sequences: ['T1', 'T2'],
    uploadedAt: '2026-07-07T09:30:00Z',
    status: 'report_signed',
    ohifStudyInstanceUID: '1.2.826.0.1.3680043.8.1055.1.20111102150758593',
    imageIds: ['img-mock-003'],
    skippedFileCount: 0,
    skipReasons: [],
  },
];

function mockProbabilities(dominant: TumourClass): ClassProbabilities {
  const base: ClassProbabilities = { glioma: 0.04, meningioma: 0.04, pituitary: 0.04, notumor: 0.04 };
  base[dominant] = 0.88;
  return base;
}

const MOCK_RESULTS: Record<string, AnalysisResult> = {
  'study-001': {
    studyId: 'study-001',
    predictedClass: 'glioma',
    probabilities: mockProbabilities('glioma'),
    calibratedConfidence: 0.88,
    predictiveEntropy: 0.21,
    belowConfidenceGate: false,
    segmentation: { enhancingTumourCm3: null, edemaCm3: null, necroticCoreCm3: null },
    model: {
      name: 'btdm-hybrid-cnn-transformer',
      version: 'v1.0.0-seed42',
      validationMacroF1: null,
      validationNote: 'Not externally validated — research use only. Architecture verified against training notebook (25,057,731 params: EfficientNet-B0 ∥ Transformer ∥ GRL ∥ SupCon).',
    },
    weakLocalization: null,
    perImageResults: null,
    consensus: null,
    primaryImageId: 'img-mock-001',
    inferredAt: '2026-07-09T08:20:00Z',
  },
  'study-002': {
    studyId: 'study-002',
    predictedClass: 'meningioma',
    probabilities: mockProbabilities('meningioma'),
    calibratedConfidence: 0.61,
    predictiveEntropy: 0.54,
    belowConfidenceGate: true,
    segmentation: { enhancingTumourCm3: null, edemaCm3: null, necroticCoreCm3: null },
    model: {
      name: 'btdm-hybrid-cnn-transformer',
      version: 'v1.0.0-seed42',
      validationMacroF1: null,
      validationNote: 'Not externally validated — research use only. Architecture verified against training notebook (25,057,731 params: EfficientNet-B0 ∥ Transformer ∥ GRL ∥ SupCon).',
    },
    weakLocalization: null,
    perImageResults: null,
    consensus: null,
    primaryImageId: 'img-mock-002',
    inferredAt: '2026-07-08T14:52:00Z',
  },
  'study-003': {
    studyId: 'study-003',
    predictedClass: 'notumor',
    probabilities: mockProbabilities('notumor'),
    calibratedConfidence: 0.93,
    predictiveEntropy: 0.09,
    belowConfidenceGate: false,
    segmentation: { enhancingTumourCm3: null, edemaCm3: null, necroticCoreCm3: null },
    model: {
      name: 'btdm-hybrid-cnn-transformer',
      version: 'v1.0.0-seed42',
      validationMacroF1: null,
      validationNote: 'Not externally validated — research use only. Architecture verified against training notebook (25,057,731 params: EfficientNet-B0 ∥ Transformer ∥ GRL ∥ SupCon).',
    },
    weakLocalization: null,
    perImageResults: null,
    consensus: null,
    primaryImageId: 'img-mock-003',
    inferredAt: '2026-07-07T09:41:00Z',
  },
};

const MOCK_REPORTS: Record<string, ClinicalReport> = {
  'study-001': {
    studyId: 'study-001', status: 'preliminary',
    clinicalIndication: '', technique: 'MR brain, T1c, T2, FLAIR (1 image submitted).',
    findings: 'AI-assisted classification: Glioma (calibrated confidence 88%). No segmentation model in this release.',
    impression: '', recommendation: '',
    summary: 'Technique: MR brain, T1c, T2, FLAIR (1 image submitted). Findings: AI-assisted classification: Glioma (calibrated confidence 88%). Correlate clinically; not a final diagnosis until reviewed and co-signed by a radiologist.',
    signedBy: null, signedAt: null,
  },
  'study-002': {
    studyId: 'study-002', status: 'preliminary',
    clinicalIndication: '', technique: 'MR brain, T1c, FLAIR (1 image submitted).',
    findings: 'AI-assisted classification: Meningioma (calibrated confidence 61% — BELOW the 70% confidence gate). Flagged for mandatory specialist review.',
    impression: '', recommendation: '',
    summary: 'Technique: MR brain, T1c, FLAIR (1 image submitted). Findings: AI-assisted classification: Meningioma (calibrated confidence 61%). Correlate clinically; not a final diagnosis until reviewed and co-signed by a radiologist.',
    signedBy: null, signedAt: null,
  },
  'study-003': {
    studyId: 'study-003', status: 'signed',
    clinicalIndication: 'Incidental finding on prior imaging.', technique: 'MR brain, T1, T2 (1 image submitted).',
    findings: 'AI-assisted classification: No tumour detected (calibrated confidence 93%).',
    impression: 'No intracranial mass lesion identified. Normal study.', recommendation: 'No follow-up imaging required unless symptoms develop.',
    summary: 'Indication: Incidental finding on prior imaging. Technique: MR brain, T1, T2 (1 image submitted). Findings: AI-assisted classification: No tumour detected (calibrated confidence 93%). Impression: No intracranial mass lesion identified. Normal study. Recommendation: No follow-up imaging required unless symptoms develop.',
    signedBy: 'Dr. A. Reyes', signedAt: '2026-07-07T10:15:00Z',
  },
};

// -- Public API -------------------------------------------------------------

export async function listStudies(): Promise<Study[]> {
  if (USE_MOCK) return delay(MOCK_STUDIES);
  const res = await authedFetch(`${API_BASE}/studies`);
  if (!res.ok) throw new Error('Failed to fetch studies');
  return res.json();
}

export async function getStudy(id: string): Promise<Study | undefined> {
  if (USE_MOCK) return delay(MOCK_STUDIES.find((s) => s.id === id));
  const res = await authedFetch(`${API_BASE}/studies/${id}`);
  if (!res.ok) throw new Error('Failed to fetch study');
  return res.json();
}

export async function getAnalysis(studyId: string): Promise<AnalysisResult | undefined> {
  if (USE_MOCK) return delay(MOCK_RESULTS[studyId]);
  const res = await authedFetch(`${API_BASE}/studies/${studyId}/results`);
  if (!res.ok) throw new Error('Failed to fetch analysis');
  return res.json();
}

export async function getReport(studyId: string): Promise<ClinicalReport | undefined> {
  if (USE_MOCK) return delay(MOCK_REPORTS[studyId]);
  const res = await authedFetch(`${API_BASE}/studies/${studyId}/report`);
  if (!res.ok) throw new Error('Failed to fetch report');
  return res.json();
}

export async function getReportDraft(
  studyId: string,
  token: string
): Promise<{ clinicalIndication: string; impression: string }> {
  if (USE_MOCK) {
    return delay(
      {
        clinicalIndication: '[SUGGESTED — replace with actual clinical history] Evaluation of imaging findings.',
        impression:
          '1. Findings most consistent with the predicted class (see classification panel).\n2. [SUGGESTED] Correlate clinically.\n\n[Mock-mode draft — connect the real backend to see one generated from actual analysis data.]',
      },
      400
    );
  }
  const res = await authedFetch(`${API_BASE}/studies/${studyId}/report/draft`, {
    headers: authHeaders(token),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || 'Failed to generate draft');
  }
  return res.json();
}

export async function updateReport(
  studyId: string,
  token: string,
  fields: { clinicalIndication?: string; impression?: string; recommendation?: string }
): Promise<ClinicalReport> {
  if (USE_MOCK) {
    const existing = MOCK_REPORTS[studyId];
    const updated: ClinicalReport = {
      ...(existing as ClinicalReport),
      clinicalIndication: fields.clinicalIndication ?? existing?.clinicalIndication ?? '',
      impression: fields.impression ?? existing?.impression ?? '',
      recommendation: fields.recommendation ?? existing?.recommendation ?? '',
    };
    MOCK_REPORTS[studyId] = updated;
    return delay(updated, 300);
  }
  const res = await authedFetch(`${API_BASE}/studies/${studyId}/report`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...authHeaders(token) },
    body: JSON.stringify({
      clinicalIndication: fields.clinicalIndication,
      impression: fields.impression,
      recommendation: fields.recommendation,
    }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || 'Failed to update report');
  }
  return res.json();
}

export async function signReport(studyId: string, token: string): Promise<ClinicalReport> {
  if (USE_MOCK) {
    const existing = MOCK_REPORTS[studyId];
    const signed: ClinicalReport = {
      ...(existing as ClinicalReport),
      status: 'signed',
      signedBy: 'Demo User',
      signedAt: new Date().toISOString(),
    };
    MOCK_REPORTS[studyId] = signed;
    return delay(signed, 400);
  }
  // `signedBy` is derived server-side from the authenticated token now —
  // a client can no longer claim to be signing as someone else.
  const res = await authedFetch(`${API_BASE}/studies/${studyId}/report/sign`, {
    method: 'POST',
    headers: authHeaders(token),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || 'Failed to sign report');
  }
  return res.json();
}

export async function deleteStudy(studyId: string, token: string): Promise<void> {
  if (USE_MOCK) {
    const idx = MOCK_STUDIES.findIndex((s) => s.id === studyId);
    if (idx !== -1) MOCK_STUDIES.splice(idx, 1);
    delete MOCK_RESULTS[studyId];
    delete MOCK_REPORTS[studyId];
    return delay(undefined, 300);
  }
  const res = await authedFetch(`${API_BASE}/studies/${studyId}`, {
    method: 'DELETE',
    headers: authHeaders(token),
  });
  if (!res.ok) throw new Error('Failed to delete study');
}

export async function triggerAnalysis(studyId: string): Promise<{ jobId: string }> {
  if (USE_MOCK) return delay({ jobId: `job-${studyId}-${Date.now()}` }, 300);
  const res = await authedFetch(`${API_BASE}/studies/${studyId}/analyze`, { method: 'POST' });
  if (!res.ok) throw new Error('Failed to trigger analysis');
  return res.json();
}

export async function uploadStudy(params: {
  files: File[];
  patientAlias: string;
  sequences: string;
}): Promise<Study> {
  if (USE_MOCK) {
    const newStudy: Study = {
      id: `study-${Date.now()}`,
      patientAlias: params.patientAlias,
      modality: 'MR',
      sequences: params.sequences.split(',').map((s) => s.trim()).filter(Boolean),
      uploadedAt: new Date().toISOString(),
      status: 'uploaded',
      ohifStudyInstanceUID: `1.2.826.0.1.3680043.8.1055.1.${Date.now()}`,
      imageIds: params.files.map((_, i) => `mock-img-${Date.now()}-${i}`),
      skippedFileCount: 0,
      skipReasons: [],
    };
    MOCK_STUDIES.unshift(newStudy);
    return delay(newStudy, 500);
  }
  const formData = new FormData();
  for (const file of params.files) formData.append('files', file);
  formData.append('patient_alias', params.patientAlias);
  formData.append('sequences', params.sequences);
  const res = await authedFetch(`${API_BASE}/studies`, { method: 'POST', body: formData });
  if (res.status === 413) throw new Error((await res.json().catch(() => null))?.detail ?? 'Upload too large');
  if (!res.ok) throw new Error('Failed to upload study');
  return res.json();
}

// -- Auth & user management --------------------------------------------------

const MOCK_USERS_DB: User[] = [
  { id: 'u1', name: 'Dr. A. Reyes', username: 'areyes', role: 'radiologist', institution: 'St. Luke Neuro Institute', active: true },
  { id: 'u2', name: 'Dr. M. Okafor', username: 'mokafor', role: 'referring_physician', institution: 'St. Luke Neuro Institute', active: true },
  { id: 'u3', name: 'R. Bello, RT(R)', username: 'rbello', role: 'technologist', institution: 'St. Luke Neuro Institute', active: true },
  { id: 'u4', name: 'S. Patel (IT Admin)', username: 'admin', role: 'admin', institution: 'St. Luke Neuro Institute', active: true },
];

export async function login(username: string, password: string): Promise<{ token: string; user: User }> {
  if (USE_MOCK) {
    // Mock mode: any of the 4 demo usernames with ANY password works —
    // there's no real backend to check credentials against.
    const user = MOCK_USERS_DB.find((u) => u.username === username.trim().toLowerCase());
    if (!user) throw new Error('Invalid username or password');
    return delay({ token: `mock-token-${user.id}`, user }, 400);
  }
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (res.status === 429) {
    throw new Error((await res.json().catch(() => null))?.detail ?? 'Too many failed attempts. Try again later.');
  }
  if (!res.ok) throw new Error('Invalid username or password');
  return res.json();
}

export async function logout(token: string): Promise<void> {
  if (USE_MOCK) return delay(undefined, 200);
  await fetch(`${API_BASE}/auth/logout`, { method: 'POST', headers: authHeaders(token) }).catch(() => {});
}

export async function listUsers(token: string): Promise<User[]> {
  if (USE_MOCK) return delay(MOCK_USERS_DB, 300);
  const res = await authedFetch(`${API_BASE}/users`, { headers: authHeaders(token) });
  if (!res.ok) throw new Error('Failed to fetch users');
  return res.json();
}

export async function createUser(
  token: string,
  params: { name: string; username: string; password: string; role: string; institution: string }
): Promise<User> {
  if (USE_MOCK) {
    const newUser: User = {
      id: `u-${Date.now()}`,
      name: params.name,
      username: params.username,
      role: params.role as User['role'],
      institution: params.institution,
      active: true,
    };
    MOCK_USERS_DB.push(newUser);
    return delay(newUser, 400);
  }
  const res = await authedFetch(`${API_BASE}/users`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders(token) },
    // Admin-set passwords are temporary: the user must change it at first login.
    body: JSON.stringify({ ...params, requirePasswordChange: true }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || 'Failed to create user');
  }
  return res.json();
}

export async function setUserActive(token: string, userId: string, active: boolean): Promise<User> {
  if (USE_MOCK) {
    const user = MOCK_USERS_DB.find((u) => u.id === userId);
    if (user) user.active = active;
    return delay(user as User, 300);
  }
  const res = await authedFetch(`${API_BASE}/users/${userId}/active?active=${active}`, {
    method: 'PATCH',
    headers: authHeaders(token),
  });
  if (!res.ok) throw new Error('Failed to update user');
  return res.json();
}

export async function deleteUser(token: string, userId: string): Promise<void> {
  if (USE_MOCK) {
    const idx = MOCK_USERS_DB.findIndex((u) => u.id === userId);
    if (idx !== -1) MOCK_USERS_DB.splice(idx, 1);
    return delay(undefined, 300);
  }
  const res = await authedFetch(`${API_BASE}/users/${userId}`, { method: 'DELETE', headers: authHeaders(token) });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || 'Failed to delete user');
  }
}

// -- Analysis jobs & live status ---------------------------------------------

export async function getJob(studyId: string): Promise<JobStatus | null> {
  if (USE_MOCK) return { jobId: 'mock', status: 'done', error: null, durationMs: 0 };
  const res = await authedFetch(`${API_BASE}/studies/${studyId}/job`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error('Failed to fetch job status');
  return res.json();
}

/**
 * Live status for one study via Server-Sent Events. Uses fetch() rather
 * than EventSource because EventSource can't send the Authorization
 * header. Resolves when the server closes the stream (after "done" or
 * "failed"), rejects if the connection breaks — callers fall back to
 * polling getJob() in that case.
 */
export async function watchStudy(
  studyId: string,
  onEvent: (e: StudyEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  if (USE_MOCK) {
    onEvent({ type: 'status', status: 'done' });
    return;
  }
  const res = await authedFetch(`${API_BASE}/studies/${studyId}/events`, {
    headers: { Accept: 'text/event-stream' },
    signal,
  });
  if (!res.ok || !res.body) throw new Error(`Event stream unavailable (${res.status})`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buffer.indexOf('\n\n')) !== -1) {
      const chunk = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      for (const line of chunk.split('\n')) {
        if (line.startsWith('data: ')) {
          try {
            onEvent(JSON.parse(line.slice(6)));
          } catch {
            /* ignore malformed event */
          }
        }
      }
    }
  }
}

// -- Model registry (admin) ---------------------------------------------------

export async function getModelRegistry(): Promise<ModelRegistryState | null> {
  if (USE_MOCK) return null;
  const res = await authedFetch(`${API_BASE}/models`);
  if (!res.ok) throw new Error('Failed to fetch model registry');
  return res.json();
}

export async function registerModel(file: File, version: string, notes: string): Promise<ModelRegistryEntry> {
  const form = new FormData();
  form.append('file', file);
  form.append('version', version);
  form.append('notes', notes);
  const res = await authedFetch(`${API_BASE}/models`, { method: 'POST', body: form });
  if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? 'Upload failed');
  return res.json();
}

export async function activateModel(modelId: number): Promise<ModelRegistryEntry> {
  const res = await authedFetch(`${API_BASE}/models/${modelId}/activate`, { method: 'POST' });
  if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? 'Activation failed');
  return res.json();
}

// -- Doctor registration ------------------------------------------------------

async function errorDetail(res: Response, fallback: string): Promise<string> {
  const body = await res.json().catch(() => null);
  return typeof body?.detail === 'string' ? body.detail : fallback;
}

/** Public: no login. Returns the reference number. */
export async function submitRegistration(form: RegistrationForm): Promise<string> {
  const res = await fetch(`${API_BASE}/registrations`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(form),
  });
  if (!res.ok) throw new Error(await errorDetail(res, 'Registration could not be submitted'));
  return (await res.json()).reference;
}

export async function listRegistrations(status?: string): Promise<RegistrationRequest[]> {
  const q = status ? `?status=${encodeURIComponent(status)}` : '';
  const res = await authedFetch(`${API_BASE}/registrations${q}`);
  if (!res.ok) throw new Error(await errorDetail(res, 'Failed to load registrations'));
  return res.json();
}

export async function approveRegistration(id: string, role?: string): Promise<GeneratedCredentials> {
  const res = await authedFetch(`${API_BASE}/registrations/${id}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(role ? { role } : {}),
  });
  if (!res.ok) throw new Error(await errorDetail(res, 'Approval failed'));
  return res.json();
}

export async function rejectRegistration(id: string, reason: string): Promise<RegistrationRequest> {
  const res = await authedFetch(`${API_BASE}/registrations/${id}/reject`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason }),
  });
  if (!res.ok) throw new Error(await errorDetail(res, 'Rejection failed'));
  return res.json();
}

export async function changePassword(currentPassword: string, newPassword: string): Promise<User> {
  const res = await authedFetch(`${API_BASE}/auth/change-password`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ currentPassword, newPassword }),
  });
  if (!res.ok) throw new Error(await errorDetail(res, 'Password could not be changed'));
  return res.json();
}
