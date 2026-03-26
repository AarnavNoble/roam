import axios from 'axios';

const BASE_URL = process.env.EXPO_PUBLIC_API_URL || 'http://localhost:8000/api';

const api = axios.create({ baseURL: BASE_URL, timeout: 120000 });

export interface TripRequest {
  destination: string;
  days: number;
  transport: 'driving' | 'walking' | 'cycling' | 'transit';
  goals: string[];
  pace: 'relaxed' | 'moderate' | 'packed';
  budget: 'free' | 'budget' | 'mid' | 'splurge';
  style: 'solo' | 'couple' | 'family' | 'group';
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
      const trimmed = line.trim(); // strip \r and whitespace
      if (trimmed.startsWith('data: ')) {
        const raw = trimmed.slice(6).trim();
        if (!raw) continue;
        try {
          const parsed = JSON.parse(raw);
          if (parsed.step !== undefined) {
            onProgress(parsed as PipelineProgress);
          } else if (parsed.days) {
            result = parsed as Itinerary;
            return result; // got what we need — exit immediately
          } else if (parsed.message) {
            throw new Error(parsed.message);
          }
        } catch (e: any) {
          if (e instanceof SyntaxError) continue; // skip malformed SSE lines
          throw e;
        }
      }
    }
  }

  if (!result) throw new Error('No result received from pipeline');
  return result;
}

export async function submitFeedback(
  poi_id: number,
  relevant: boolean,
  poi_name: string = '',
  category: string = '',
  goals: string[] = [],
) {
  await api.post('/feedback', { poi_id, relevant, poi_name, category, goals });
}
