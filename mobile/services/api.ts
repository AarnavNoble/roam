import axios from 'axios';
import AsyncStorage from '@react-native-async-storage/async-storage';

const BASE_URL = process.env.EXPO_PUBLIC_API_URL || 'http://localhost:8000/api';

// Share links live on the API's origin, outside /api (https://host/t/<slug>)
export const API_ORIGIN = BASE_URL.replace(/\/api\/?$/, '');

const api = axios.create({ baseURL: BASE_URL, timeout: 120000 });

// ── Errors ────────────────────────────────────────────────────────────────────

// Every failed API call throws one of these, with a message that can be shown as is.
// status is the HTTP status, or 0 when the server could not be reached.
export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function errorMessage(status: number, detail: unknown, retryAfter?: string | null): string {
  if (status === 429) {
    const secs = Math.ceil(Number(retryAfter));
    return secs > 0
      ? `Too many requests. Try again in ${secs} seconds.`
      : 'Too many requests. Try again in a moment.';
  }
  if (status === 503) return 'The service is temporarily unavailable. Please try again shortly.';
  // Validation errors (422) arrive as a list of { msg }, everything else as a string
  if (Array.isArray(detail) && detail[0]?.msg) return String(detail[0].msg).replace(/^Value error, /, '');
  if (typeof detail === 'string' && detail) return detail;
  return `Request failed (HTTP ${status})`;
}

async function toApiError(status: number, detail: unknown, retryAfter: string | null | undefined, sentToken: boolean): Promise<ApiError> {
  // A 401 on a request that carried a token means the token expired or was revoked.
  // Sign out so the next request goes through anonymously instead of failing again.
  if (status === 401 && sentToken) {
    await logout();
    return new ApiError(401, 'Your session has expired. Please sign in again.');
  }
  return new ApiError(status, errorMessage(status, detail, retryAfter));
}

// ── Account ───────────────────────────────────────────────────────────────────

const SESSION_KEY = 'roam_session';

export interface User {
  id: string;
  email: string;
}

interface Session {
  token: string;
  user: User;
}

// The token is stored in AsyncStorage, which is plain unencrypted app storage,
// not the keychain. That is an accepted trade-off for this app: the token only
// grants access to saved trips and expires on the server.
let _session: Session | null = null;
let _sessionLoaded = false;

async function getSession(): Promise<Session | null> {
  if (!_sessionLoaded) {
    let stored: Session | null = null;
    try {
      const raw = await AsyncStorage.getItem(SESSION_KEY);
      stored = raw ? JSON.parse(raw) : null;
    } catch {}
    // A sign in or sign out may have finished while we were reading; it wins
    if (!_sessionLoaded) { _session = stored; _sessionLoaded = true; }
  }
  return _session;
}

export async function getCurrentUser(): Promise<User | null> {
  const session = await getSession();
  return session ? session.user : null;
}

async function authHeaders(): Promise<Record<string, string>> {
  const session = await getSession();
  return session ? { Authorization: `Bearer ${session.token}` } : {};
}

api.interceptors.request.use(async config => {
  const session = await getSession();
  if (session) config.headers.Authorization = `Bearer ${session.token}`;
  return config;
});

api.interceptors.response.use(
  res => res,
  async error => {
    if (!error.response) throw new ApiError(0, 'Could not reach the server. Check your connection and try again.');
    const { status, data, headers } = error.response;
    throw await toApiError(status, data?.detail, headers?.['retry-after'], !!error.config?.headers?.Authorization);
  },
);

async function authenticate(path: '/auth/register' | '/auth/login', email: string, password: string): Promise<User> {
  const res = await api.post<Session>(path, { email, password });
  _session = { token: res.data.token, user: res.data.user };
  _sessionLoaded = true;
  await AsyncStorage.setItem(SESSION_KEY, JSON.stringify(_session));
  return _session.user;
}

export function register(email: string, password: string): Promise<User> {
  return authenticate('/auth/register', email, password);
}

export function login(email: string, password: string): Promise<User> {
  return authenticate('/auth/login', email, password);
}

// Tokens are stateless on the server, so signing out is just forgetting the token.
export async function logout(): Promise<void> {
  _session = null;
  _sessionLoaded = true;
  try { await AsyncStorage.removeItem(SESSION_KEY); } catch {}
}

// Asks the server who the stored token belongs to. Throws (and signs out) if it has expired.
export async function fetchMe(): Promise<User> {
  const res = await api.get<User>('/auth/me');
  return res.data;
}

// ── Types ─────────────────────────────────────────────────────────────────────

export interface TripRequest {
  city: string;
  start_location: string;
  duration_hours: number;
  goals: string[];
  transport: 'walking' | 'transit' | 'cycling' | 'driving';
  pace: 'relaxed' | 'moderate' | 'packed';
  budget: 'free' | 'budget' | 'mid' | 'splurge';
  style: 'solo' | 'couple' | 'family' | 'group';
  dietary: 'none' | 'vegetarian' | 'vegan' | 'halal' | 'kosher';
  mobility: 'easy' | 'moderate' | 'active';
  familiarity: 'first_time' | 'returning';
  start_time: 'morning' | 'afternoon' | 'evening';
  notes: string;
  trip_date?: string; // ISO date e.g. "2026-04-12"
}

export interface Stop {
  id: number; // OSM id. Trips saved on this device by older versions may not have it
  name: string;
  arrival_time: string;
  duration_min: number;
  description: string;
  tip: string;
  lat: number;
  lon: number;
  category: string;
  photo_url?: string;
  opening_hours?: string;
}

export interface Day {
  day: number;
  theme: string;
  stops: Stop[];
  summary: string;
}

export interface FeatureExplanation {
  features: Record<string, number>;
  contributions: Record<string, number>;
}

export interface Itinerary {
  days: Day[];
  overview: string;
  ranking_explanations?: Record<string, FeatureExplanation>;
  global_feature_importance?: Record<string, number>;
  // The three below are missing on trips saved on this device by older versions
  trip_id?: string | null;               // server id, null if the server could not store the trip
  degraded?: string[];                   // fallbacks the server used, e.g. "llm:fallback"
  timings_ms?: Record<string, number>;
}

export interface PipelineProgress {
  step: string;
  message: string;
  progress: number;
}

// Module-level cache to avoid URL param size limits
const LAST_ITINERARY_KEY = 'roam_last_itinerary';
let _lastItinerary: Itinerary | null = null;
export function storeItinerary(it: Itinerary) {
  _lastItinerary = it;
  AsyncStorage.setItem(LAST_ITINERARY_KEY, JSON.stringify(it)).catch(() => {});
}
export function getStoredItinerary(): Itinerary | null { return _lastItinerary; }
export async function loadLastItinerary(): Promise<Itinerary | null> {
  if (_lastItinerary) return _lastItinerary;
  try {
    const raw = await AsyncStorage.getItem(LAST_ITINERARY_KEY);
    if (raw) { _lastItinerary = JSON.parse(raw); }
  } catch {}
  return _lastItinerary;
}

// Selected stop for detail screen
interface SelectedStop { stop: Stop; goals: string[]; dayColor: string; }
let _selectedStop: SelectedStop | null = null;
export function storeSelectedStop(stop: Stop, goals: string[], dayColor: string) {
  _selectedStop = { stop, goals, dayColor };
}
export function getSelectedStop(): SelectedStop | null { return _selectedStop; }

// ── Saved trips ───────────────────────────────────────────────────────────────

const SAVED_KEY = 'roam_saved_trips';

export interface SavedTrip {
  id: string;
  city: string;
  goals: string[];
  savedAt: number; // timestamp
  tripDate?: string; // ISO date the trip was planned for
  itinerary: Itinerary;
}

export async function getSavedTrips(): Promise<SavedTrip[]> {
  try {
    const raw = await AsyncStorage.getItem(SAVED_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch { return []; }
}

export async function saveTrip(city: string, goals: string[], itinerary: Itinerary, tripDate?: string): Promise<SavedTrip> {
  const trips = await getSavedTrips();
  const trip: SavedTrip = { id: Date.now().toString(), city, goals, savedAt: Date.now(), tripDate, itinerary };
  await AsyncStorage.setItem(SAVED_KEY, JSON.stringify([trip, ...trips].slice(0, 20)));
  return trip;
}

export async function deleteTrip(id: string): Promise<void> {
  const trips = await getSavedTrips();
  await AsyncStorage.setItem(SAVED_KEY, JSON.stringify(trips.filter(t => t.id !== id)));
}

// ── Trips on the server ───────────────────────────────────────────────────────
// Every generated itinerary is stored by the server. When signed in they are
// tied to the account, so they can be listed from any device.

export interface TripSummary {
  id: string;
  city: string;
  goals: string[];
  trip_date: string;
  created_at: string; // ISO timestamp
  stops: number;
  share_slug: string | null;
  parent_id: string | null; // set when this trip is an edit of another one
}

export interface TripDetail extends TripSummary {
  request: TripRequest;
  itinerary: Itinerary;
}

// An edit returns the new itinerary plus a list of what was changed
export interface EditedItinerary extends Itinerary {
  changes: string[];
}

export async function getServerTrips(): Promise<TripSummary[]> {
  const res = await api.get<{ trips: TripSummary[] }>('/trips');
  return res.data.trips;
}

export async function getServerTrip(id: string): Promise<TripDetail> {
  const res = await api.get<TripDetail>(`/trips/${id}`);
  return res.data;
}

export async function deleteServerTrip(id: string): Promise<void> {
  await api.delete(`/trips/${id}`);
}

// Creates (or returns the existing) public link for a trip
export async function shareTrip(id: string): Promise<string> {
  const res = await api.post<{ share_slug: string; path: string }>(`/trips/${id}/share`);
  return API_ORIGIN + res.data.path;
}

export async function unshareTrip(id: string): Promise<void> {
  await api.delete(`/trips/${id}/share`);
}

// Asks the server to change a trip. The original is kept; the result is a new trip with its own trip_id.
export async function editTrip(id: string, message: string): Promise<EditedItinerary> {
  const res = await api.post<EditedItinerary>(`/trips/${id}/edit`, { message });
  return res.data;
}

// ── User preferences ─────────────────────────────────────────────────────────

const PREFS_KEY = 'roam_user_prefs';

export interface UserPrefs {
  pace: TripRequest['pace'];
  budget: TripRequest['budget'];
  style: TripRequest['style'];
  dietary: TripRequest['dietary'];
  mobility: TripRequest['mobility'];
  familiarity: TripRequest['familiarity'];
  transport: TripRequest['transport'];
  durationHours: number;
  startTime: TripRequest['start_time'];
  goals: string[];
}

export async function loadPrefs(): Promise<Partial<UserPrefs>> {
  try {
    const raw = await AsyncStorage.getItem(PREFS_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch { return {}; }
}

export async function savePrefs(prefs: UserPrefs): Promise<void> {
  try { await AsyncStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch {}
}

export async function clearPrefs(): Promise<void> {
  try { await AsyncStorage.removeItem(PREFS_KEY); } catch {}
}

export async function clearAllSavedTrips(): Promise<void> {
  try { await AsyncStorage.removeItem(SAVED_KEY); } catch {}
}

export function generateICS(city: string, tripDate: Date, itinerary: Itinerary): string {
  const pad = (n: number) => n.toString().padStart(2, '0');
  const fmtDate = (d: Date) =>
    `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}`;

  const lines: string[] = [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'PRODID:-//Roam//Travel Itinerary//EN',
    'CALSCALE:GREGORIAN',
    'METHOD:PUBLISH',
  ];

  for (const day of itinerary.days) {
    const dayDate = new Date(tripDate);
    dayDate.setDate(dayDate.getDate() + day.day - 1);
    const ds = fmtDate(dayDate);

    for (const stop of day.stops) {
      const [h, m] = stop.arrival_time.split(':').map(Number);
      const startMin = h * 60 + m;
      const endMin   = startMin + (stop.duration_min || 60);
      const endH = Math.floor(endMin / 60) % 24;
      const endM = endMin % 60;
      const dtStart = `${ds}T${pad(h)}${pad(m)}00`;
      const dtEnd   = `${ds}T${pad(endH)}${pad(endM)}00`;

      const desc = (stop.description || '').replace(/\n/g, '\\n').replace(/,/g, '\\,');
      const tip  = stop.tip ? `\\nTip: ${stop.tip.replace(/\n/g, '\\n').replace(/,/g, '\\,')}` : '';

      lines.push('BEGIN:VEVENT');
      lines.push(`DTSTART:${dtStart}`);
      lines.push(`DTEND:${dtEnd}`);
      lines.push(`SUMMARY:${stop.name}`);
      lines.push(`DESCRIPTION:${desc}${tip}`);
      if (stop.lat && stop.lon) {
        lines.push(`GEO:${stop.lat};${stop.lon}`);
        lines.push(`LOCATION:${stop.lat},${stop.lon}`);
      }
      lines.push(`CATEGORIES:${city}`);
      lines.push('END:VEVENT');
    }
  }

  lines.push('END:VCALENDAR');
  return lines.join('\r\n');
}

export function formatItineraryAsText(city: string, itinerary: Itinerary): string {
  const lines: string[] = [`🗺️ ${city} — Roam Itinerary`, '', itinerary.overview, ''];
  for (const day of itinerary.days) {
    lines.push(`Day ${day.day}: ${day.theme}`);
    for (const stop of day.stops) {
      lines.push(`  ${stop.arrival_time}  ${stop.name} (${stop.duration_min} min)`);
      if (stop.tip) lines.push(`  💡 ${stop.tip}`);
    }
    lines.push('');
  }
  return lines.join('\n');
}

// ── Weather ───────────────────────────────────────────────────────────────────

export interface WeatherDay {
  tempMax: number;
  code: number;
  label: string;
  color: string;
}

const WMO_LABELS: [number[], string, string][] = [
  [[0],                        'Clear',         '#F59E0B'],
  [[1, 2],                     'Partly Cloudy', '#9CA3AF'],
  [[3],                        'Overcast',      '#6B7280'],
  [[45, 48],                   'Foggy',         '#6B7280'],
  [[51, 53, 55],               'Drizzle',       '#60A5FA'],
  [[61, 63, 65],               'Rain',          '#3B82F6'],
  [[71, 73, 75, 77],           'Snow',          '#93C5FD'],
  [[80, 81, 82],               'Showers',       '#3B82F6'],
  [[95, 96, 99],               'Thunderstorm',  '#8B5CF6'],
];

function wmoToLabel(code: number): { label: string; color: string } {
  for (const [codes, label, color] of WMO_LABELS) {
    if (codes.includes(code)) return { label, color };
  }
  return { label: 'Unknown', color: '#6B7280' };
}

export async function fetchWeather(lat: number, lon: number, days: number): Promise<WeatherDay[]> {
  try {
    const url = `https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}&daily=temperature_2m_max,weathercode&timezone=auto&forecast_days=${Math.min(days, 7)}`;
    const res = await fetch(url);
    if (!res.ok) return [];
    const data = await res.json();
    const temps: number[] = data.daily?.temperature_2m_max ?? [];
    const codes: number[] = data.daily?.weathercode ?? [];
    return temps.map((temp, i) => {
      const code = codes[i] ?? 0;
      const { label, color } = wmoToLabel(code);
      return { tempMax: Math.round(temp), code, label, color };
    });
  } catch { return []; }
}

export async function generateItinerary(req: TripRequest): Promise<Itinerary> {
  const res = await api.post<Itinerary>('/itinerary', req);
  return res.data;
}

// Handles one line of the event stream. Progress events are passed to onProgress,
// the result event is returned, and an error event is thrown.
function readStreamLine(line: string, onProgress: (progress: PipelineProgress) => void): Itinerary | null {
  const trimmed = line.trim();
  if (!trimmed.startsWith('data: ')) return null;
  let parsed: any;
  try { parsed = JSON.parse(trimmed.slice(6)); } catch { return null; }
  if (parsed.step !== undefined) onProgress(parsed as PipelineProgress);
  else if (parsed.days) return parsed as Itinerary;
  else if (parsed.message) throw new Error(parsed.message);
  return null;
}

export async function generateItineraryStreaming(
  req: TripRequest,
  onProgress: (progress: PipelineProgress) => void,
): Promise<Itinerary> {
  const auth = await authHeaders();
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}/itinerary/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream', ...auth },
      body: JSON.stringify(req),
    });
  } catch {
    throw new ApiError(0, 'Could not reach the server. Check your connection and try again.');
  }

  if (!response.ok) {
    let detail: unknown;
    try { detail = (await response.json()).detail; } catch {}
    throw await toApiError(response.status, detail, response.headers.get('Retry-After'), !!auth.Authorization);
  }

  if (!response.body) {
    // React Native's fetch cannot read a body as it arrives. The whole stream is
    // already here by now, so read the same events from the full text.
    const text = await response.text();
    for (const line of text.split('\n')) {
      const result = readStreamLine(line, onProgress);
      if (result) return result;
    }
    throw new Error('No result received from pipeline');
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const lines = buffer.split('\n');
    buffer = lines.pop() || '';

    for (const line of lines) {
      const result = readStreamLine(line, onProgress);
      if (result) return result;
    }
  }

  throw new Error('No result received from pipeline');
}

export interface FeedbackResult {
  total_feedback: number;
  retrained: boolean;
}

export async function submitFeedback(
  poi_id: number,
  relevant: boolean,
  poi_name: string = '',
  category: string = '',
  goals: string[] = [],
): Promise<FeedbackResult> {
  const res = await api.post<FeedbackResult>('/feedback', { poi_id, relevant, poi_name, category, goals });
  return res.data;
}
