// Domain types mirror the REST/FHIR contract in the architecture spec (§5).
// Keeping these centralized means swapping the mock API for the real
// backend later only touches lib/api.ts, not every component.

export type Role = 'radiologist' | 'referring_physician' | 'technologist' | 'admin';

export interface User {
  id: string;
  name: string;
  username: string;
  role: Role;
  institution: string;
  active: boolean;
  mustChangePassword?: boolean;
}

export type TumourClass = 'glioma' | 'meningioma' | 'pituitary' | 'notumor';

export interface ClassProbabilities {
  glioma: number;
  meningioma: number;
  pituitary: number;
  notumor: number;
}

export type StudyStatus =
  | 'uploaded'
  | 'preprocessing'
  | 'analyzing'
  | 'analyzed'
  | 'report_pending_signoff'
  | 'report_signed'
  | 'failed';

export interface Study {
  id: string;
  patientAlias: string; // de-identified display alias, never real MRN/name
  modality: 'MR';
  sequences: string[]; // e.g. ['T1c', 'T2', 'FLAIR']
  uploadedAt: string;
  status: StudyStatus;
  ohifStudyInstanceUID: string;
  imageIds: string[];
  skippedFileCount: number;
  skipReasons: string[];
}

export interface SegmentationVolumes {
  enhancingTumourCm3: number | null;
  edemaCm3: number | null;
  necroticCoreCm3: number | null;
}

export interface LesionMeasurement {
  lesionId: string;
  areaCm2: number;
  maxDiameterMm: number;
  widthMm: number;
  heightMm: number;
  volumeCm3Estimate: number | null;
  boundingBoxNormalized: [number, number, number, number];
  centroidNormalized: [number, number];
  locationDescription: string;
  boundaryRefined: boolean;
}

export interface WeakLocalization {
  areaCm2: number | null;
  boundingBoxNormalized: [number, number, number, number] | null;
  centroidNormalized: [number, number] | null;
  locationDescription: string;
  isDiffuse: boolean;
  lesionCount: number;
  lesions: LesionMeasurement[];
  methodNote: string;
}

export interface ModelInfo {
  name: string;
  version: string;
  validationMacroF1: number | null;
  validationNote: string;
  checkpointId?: string | null;
  checkpointSha256?: string | null;
}

export interface PerImageResult {
  imageId: string;
  predictedClass: TumourClass;
  probabilities: ClassProbabilities;
  calibratedConfidence: number;
}

export interface ConsensusInfo {
  numImages: number;
  agreeingImages: number;
  agreementFraction: number;
  reliabilityScore: number;
  reliabilityReason: string;
}

export interface AnalysisResult {
  studyId: string;
  predictedClass: TumourClass;
  probabilities: ClassProbabilities;
  calibratedConfidence: number; // 0-1, post temperature-scaling
  predictiveEntropy: number; // epistemic uncertainty signal
  belowConfidenceGate: boolean; // true => must be flagged for specialist referral
  segmentation: SegmentationVolumes;
  weakLocalization: WeakLocalization | null;
  perImageResults: PerImageResult[] | null; // populated only for multi-image studies
  consensus: ConsensusInfo | null; // populated only for multi-image studies
  primaryImageId: string | null;
  model: ModelInfo;
  inferredAt: string;
}

export interface ClinicalReport {
  studyId: string;
  status: 'preliminary' | 'signed';
  clinicalIndication: string;
  technique: string;
  findings: string;
  impression: string;
  recommendation: string;
  summary: string;
  signedBy: string | null;
  signedAt: string | null;
}

export interface JobStatus {
  jobId: string;
  status: 'queued' | 'running' | 'done' | 'failed';
  error: string | null;
  durationMs: number | null;
}

export interface StudyEvent {
  type: 'status' | 'progress';
  status?: 'none' | 'queued' | 'running' | 'done' | 'failed';
  message?: string;
  error?: string | null;
  jobId?: string | null;
  durationMs?: number;
}

export interface ModelRegistryEntry {
  id: number;
  version: string;
  sha256: string;
  checkpointId: string | null;
  sizeBytes: number;
  status: 'pending' | 'validated' | 'failed';
  validationDetail: Record<string, unknown> | null;
  isActive: boolean;
  externallyValidated: boolean;
  notes: string;
  source: 'upload' | 'bootstrap' | 'watch-folder';
  sourcePath: string | null;
  sourceMtime: string | null;
  createdAt: string;
  activatedAt: string | null;
}

export interface ModelRegistryState {
  locked: boolean;
  expectedCheckpointId: string;
  workerOnline: boolean;
  workerLoadedModel: { version: string; sha256: string; device: string; worker: string } | null;
  autoActivate: boolean;
  trainingFolder: { folder: string; last_scan: string; checkpoint_files: number; error: string | null } | null;
  models: ModelRegistryEntry[];
}

export type DoctorRole = 'radiologist' | 'referring_physician';

export interface RegistrationForm {
  fullName: string;
  email: string;
  phone: string;
  nmcNumber: string;
  specialty: string;
  requestedRole: DoctorRole;
  institution: string;
  notes: string;
  website: string; // honeypot, always empty for real users
}

export interface RegistrationRequest {
  id: string;
  fullName: string;
  email: string;
  phone: string;
  nmcNumber: string;
  specialty: string;
  requestedRole: DoctorRole;
  institution: string;
  notes: string;
  status: 'pending' | 'approved' | 'rejected';
  submittedAt: string;
  reviewedAt: string | null;
  decisionNote: string | null;
  username: string | null;
}

export interface GeneratedCredentials {
  registration: RegistrationRequest;
  username: string;
  temporaryPassword: string;
}

/** Mirrors the server's password policy (GET /auth/password-policy); the
 * server remains the authority and re-checks every password. */
export const PASSWORD_MIN = 8;
export const PASSWORD_MAX = 64;
export const PASSWORD_RULES = `${PASSWORD_MIN}–${PASSWORD_MAX} characters, with at least one letter and one number`;

export function passwordProblems(pw: string, username?: string): string[] {
  const p: string[] = [];
  if (pw.length < PASSWORD_MIN) p.push(`at least ${PASSWORD_MIN} characters`);
  if (pw.length > PASSWORD_MAX) p.push(`at most ${PASSWORD_MAX} characters`);
  if (!/[A-Za-z]/.test(pw)) p.push('a letter');
  if (!/\d/.test(pw)) p.push('a number');
  if (username && username.length >= 3 && pw.toLowerCase().includes(username.toLowerCase())) p.push('not your user ID');
  return p;
}
