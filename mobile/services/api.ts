import axios from 'axios';
import AsyncStorage from '@react-native-async-storage/async-storage';

const BASE_URL = process.env.EXPO_PUBLIC_API_URL || 'http://localhost:8000/api';

const api = axios.create({ baseURL: BASE_URL, timeout: 120000 });

export interface TripRequest {
  city: string;
  start_location: string;
  duration_hours: number;
  goals: string[];
  transport: 'walking' | 'transit';
  pace: 'relaxed' | 'moderate' | 'packed';
  budget: 'free' | 'budget' | 'mid' | 'splurge';
  style: 'solo' | 'couple' | 'family' | 'group';
  dietary: 'none' | 'vegetarian' | 'vegan' | 'halal' | 'kosher';
  mobility: 'easy' | 'moderate' | 'active';
  familiarity: 'first_time' | 'returning';
  start_time: 'morning' | 'afternoon' | 'evening';
  notes: string;
}

export interface Stop {
  id?: number;
  name: string;
  arrival_time: string;
  duration_min: number;
  description: string;
  tip: string;
  lat: number;
  lon: number;
  category: string;
  photo_url?: string;
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
}

export interface PipelineProgress {
  step: string;
  message: string;
  progress: number;
}

// Module-level cache to avoid URL param size limits
let _lastItinerary: Itinerary | null = null;
export function storeItinerary(it: Itinerary) { _lastItinerary = it; }
export function getStoredItinerary(): Itinerary | null { return _lastItinerary; }

// ── Saved trips ───────────────────────────────────────────────────────────────

const SAVED_KEY = 'roam_saved_trips';

export interface SavedTrip {
  id: string;
  city: string;
  goals: string[];
  savedAt: number; // timestamp
  itinerary: Itinerary;
}

export async function getSavedTrips(): Promise<SavedTrip[]> {
  try {
    const raw = await AsyncStorage.getItem(SAVED_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch { return []; }
}

export async function saveTrip(city: string, goals: string[], itinerary: Itinerary): Promise<SavedTrip> {
  const trips = await getSavedTrips();
  const trip: SavedTrip = { id: Date.now().toString(), city, goals, savedAt: Date.now(), itinerary };
  await AsyncStorage.setItem(SAVED_KEY, JSON.stringify([trip, ...trips].slice(0, 20)));
  return trip;
}

export async function deleteTrip(id: string): Promise<void> {
  const trips = await getSavedTrips();
  await AsyncStorage.setItem(SAVED_KEY, JSON.stringify(trips.filter(t => t.id !== id)));
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

export async function generateItinerary(req: TripRequest): Promise<Itinerary> {
  const res = await api.post<Itinerary>('/itinerary', req);
  return res.data;
}

export async function generateItineraryStreaming(
  req: TripRequest,
  onProgress: (progress: PipelineProgress) => void,
): Promise<Itinerary> {
  const response = await fetch(`${BASE_URL}/itinerary/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
    body: JSON.stringify(req),
  });

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  if (!response.body) {
    // Fallback: streaming not supported, use regular endpoint
    const res = await api.post<Itinerary>('/itinerary', req);
    return res.data;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let result: Itinerary | null = null;
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const lines = buffer.split('\n');
    buffer = lines.pop() || '';

    for (const line of lines) {
      const trimmed = line.trim();
      if (trimmed.startsWith('data: ')) {
        const raw = trimmed.slice(6).trim();
        if (!raw) continue;
        try {
          const parsed = JSON.parse(raw);
          if (parsed.step !== undefined) {
            onProgress(parsed as PipelineProgress);
          } else if (parsed.days) {
            return parsed as Itinerary;
          } else if (parsed.message) {
            throw new Error(parsed.message);
          }
        } catch (e: any) {
          if (e instanceof SyntaxError) continue;
          throw e;
        }
      }
    }
  }

  if (!result) throw new Error('No result received from pipeline');
  return result;
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
