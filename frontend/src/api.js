// Tiny fetch wrappers for the local ApplyBro backend. All requests are
// same-origin (127.0.0.1) — no external network calls from the frontend.

async function req(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    let json = null;
    try {
      json = await res.json();
      // FastAPI wraps our handler's dict in {"detail": ...}; setup_required
      // responses put their structured body there (see server.py's
      // SetupRequiredError handler) — unwrap it so callers can read
      // err.body.missing without re-parsing.
      const d = json?.detail ?? json;
      detail = (d && (d.detail || d.message)) || d || detail;
    } catch {
      /* ignore */
    }
    const err = new Error(typeof detail === "string" ? detail : res.statusText);
    err.status = res.status;
    err.body = json?.detail ?? json;
    throw err;
  }
  return res.json();
}

export const api = {
  getSource: () => req("GET", "/api/source"),
  putSource: (body) => req("PUT", "/api/source", body),
  guessColumns: (tab) =>
    req("GET", `/api/source/guess-columns?tab=${encodeURIComponent(tab)}`),
  startRun: (body) => req("POST", "/api/run", body),
  runStatus: () => req("GET", "/api/run/status"),
  getCompanies: () => req("GET", "/api/companies"),
  addCompany: (name, url) => req("POST", "/api/companies", { name, url }),
  updateCompany: (body) => req("PUT", "/api/companies", body),
  removeCompany: (name, careers_url) =>
    req("DELETE", "/api/companies", { name, careers_url }),
  skipCompany: () => req("POST", "/api/run/skip-company"),
  stopRun: () => req("POST", "/api/run/stop"),
  getUiState: () => req("GET", "/api/ui-state"),
  putUiState: (body) => req("PUT", "/api/ui-state", body),

  // Unified Jobs tab
  getJobs: () => req("GET", "/api/jobs"),
  setJobStatus: (url, status) => req("POST", "/api/jobs/status", { url, status }),

  // Review queue
  getQueue: () => req("GET", "/api/queue"),
  tailorJob: (url) => req("POST", "/api/queue/tailor", { url }),
  commentJob: (url, text) => req("POST", "/api/queue/comment", { url, text }),
  skipJob: (url) => req("POST", "/api/queue/skip", { url }),
  unskipJob: (url) => req("POST", "/api/queue/unskip", { url }),
  verifyJob: (url) => req("POST", "/api/verify", { url }),
  pdfUrl: (url) => `/api/pdf?url=${encodeURIComponent(url)}`,
  pdfDownloadUrl: (url) => `/api/pdf?url=${encodeURIComponent(url)}&download=1`,

  // Apply room
  addJob: (url) => req("POST", "/api/queue/add", { url }),
  startApply: (urls) => req("POST", "/api/apply/start", { urls }),
  abortTailoring: () => req("POST", "/api/apply/abort-tailoring"),
  stopApplyBatch: () => req("POST", "/api/apply/stop"),
  getAi: () => req("GET", "/api/ai"),
  putAi: (patch) => req("PUT", "/api/ai", patch),
  getAiModels: (provider) =>
    req("GET", `/api/ai/models?provider=${encodeURIComponent(provider)}`),
  applyStatus: () => req("GET", "/api/apply/status"),
  applyJobs: () => req("GET", "/api/apply/jobs"),
  applyCheck: () => req("POST", "/api/apply/check"),
  markApply: (url, status) => req("POST", "/api/apply/mark", { url, status }),
  getApplications: () => req("GET", "/api/applications"),

  // Tracking
  getApplications: () => req("GET", "/api/applications"),
  getTracking: () => req("GET", "/api/tracking"),
  refreshTracking: () => req("POST", "/api/tracking/refresh"),

  // Me: profile / resume / targets
  getProfile: () => req("GET", "/api/profile"),
  putProfile: (updates) => req("PUT", "/api/profile", updates),
  getContent: () => req("GET", "/api/content"),
  putContent: (content) => req("PUT", "/api/content", { content }),
  rebuildContent: () => req("POST", "/api/content/rebuild"),
  resumePdfUrl: () => `/api/resume/pdf?t=${Date.now()}`,
  resumeUpload: (filename, pdf_base64) =>
    req("POST", "/api/resume/upload", { filename, pdf_base64 }),
  resumeBase: () => req("GET", "/api/resume/base"),
  getTargets: () => req("GET", "/api/targets"),
  putTargets: (role_keywords, locations, departments) =>
    req("PUT", "/api/targets", { role_keywords, locations, departments }),

  // Auth (Supabase email + password)
  authStatus: () => req("GET", "/api/auth/status"),
  adminUsers: () => req("GET", "/api/admin/users"),
  adminPatchUser: (userId, body) =>
    req("PATCH", `/api/admin/users/${encodeURIComponent(userId)}`, body),
  authSignup: (email, password) => req("POST", "/api/auth/signup", { email, password }),
  authLogin: (email, password) => req("POST", "/api/auth/login", { email, password }),
  authLogout: () => req("POST", "/api/auth/logout"),

  // First-run setup wizard
  getSetupStatus: () => req("GET", "/api/setup/status"),
  setupSpreadsheet: (spreadsheet_id) =>
    req("POST", "/api/setup/spreadsheet", { spreadsheet_id }),
  setupAuthStart: () => req("POST", "/api/setup/auth/start"),
  setupAuthStatus: () => req("GET", "/api/setup/auth/status"),
  setupProfileInit: () => req("POST", "/api/setup/profile/init"),
};

/** Subscribes to an SSE progress stream. Returns an unsubscribe function. */
export function subscribeEvents(onEvent, after = 0, path = "/api/events/stream") {
  const es = new EventSource(`${path}?after=${after}`);
  es.onmessage = (e) => {
    try {
      onEvent(JSON.parse(e.data));
    } catch {
      /* ignore malformed */
    }
  };
  return () => es.close();
}

export function subscribeApplyEvents(onEvent, after = 0) {
  return subscribeEvents(onEvent, after, "/api/apply/events/stream");
}
