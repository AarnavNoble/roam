import axios from 'axios';

const BASE_URL = process.env.EXPO_PUBLIC_API_URL || 'http://localhost:8000/api';

const api = axios.create({ baseURL: BASE_URL, timeout: 30000 });

export interface TripRequest {
  destination: string;
  days: number;
  transport: 'driving' | 'walking' | 'cycling' | 'transit';
  goals: string[];
}

export interface Stop {
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

export interface Itinerary {
  days: Day[];
  overview: string;
}

export async function generateItinerary(req: TripRequest): Promise<Itinerary> {
  const res = await api.post<Itinerary>('/itinerary', req);
  return res.data;
}

export async function submitFeedback(poi_id: number, relevant: boolean) {
  await api.post('/feedback', { poi_id, relevant });
}
