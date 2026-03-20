import { useLocalSearchParams, useRouter } from 'expo-router';
import { useState } from 'react';
import {
  View, Text, ScrollView, TouchableOpacity,
  StyleSheet, SafeAreaView, Dimensions, Platform,
} from 'react-native';
import { Itinerary, Day, Stop, submitFeedback } from '../services/api';

// MapLibre is native-only — conditionally import to avoid web crashes
let MapLibreGL: any = null;
if (Platform.OS !== 'web') {
  MapLibreGL = require('@maplibre/maplibre-react-native').default;
  MapLibreGL.setAccessToken(null);
}

const CATEGORY_COLORS: Record<string, string> = {
  food: '#F59E0B',
  nature: '#10B981',
  history: '#8B5CF6',
  culture: '#3B82F6',
  nightlife: '#EC4899',
  shopping: '#F97316',
  adventure: '#EF4444',
  attraction: '#6B7280',
};

// A distinct color per day index
const DAY_COLORS = ['#3B82F6', '#10B981', '#F59E0B', '#EC4899', '#8B5CF6', '#EF4444'];

// ── List view components ──────────────────────────────────────────────────────

function StopCard({ stop, goals }: { stop: Stop; goals: string[] }) {
  const [expanded, setExpanded] = useState(false);
  const [feedback, setFeedback] = useState<'up' | 'down' | null>(null);
  const color = CATEGORY_COLORS[stop.category] || '#6B7280';

  const handleFeedback = async (relevant: boolean) => {
    const signal = relevant ? 'up' : 'down';
    setFeedback(signal);
    try {
      await submitFeedback(stop.id || 0, relevant, stop.name, stop.category, goals);
    } catch {}
  };

  return (
    <TouchableOpacity style={styles.stopCard} onPress={() => setExpanded(e => !e)} activeOpacity={0.8}>
      <View style={styles.stopHeader}>
        <View style={[styles.dot, { backgroundColor: color }]} />
        <View style={styles.stopMeta}>
          <Text style={styles.stopTime}>{stop.arrival_time}</Text>
          <Text style={styles.stopDuration}>{stop.duration_min} min</Text>
        </View>
        <View style={styles.feedbackRow}>
          <TouchableOpacity onPress={() => handleFeedback(true)} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
            <Text style={[styles.feedbackBtn, feedback === 'up' && styles.feedbackActive]}>+</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={() => handleFeedback(false)} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
            <Text style={[styles.feedbackBtn, feedback === 'down' && styles.feedbackActive]}>−</Text>
          </TouchableOpacity>
        </View>
      </View>
      <Text style={styles.stopName}>{stop.name}</Text>
      <View style={[styles.categoryBadge, { backgroundColor: color + '20' }]}>
        <Text style={[styles.categoryText, { color }]}>{stop.category}</Text>
      </View>
      {expanded && (
        <View style={styles.stopDetails}>
          <Text style={styles.stopDescription}>{stop.description}</Text>
          <View style={styles.tipBox}>
            <Text style={styles.tipLabel}>Tip</Text>
            <Text style={styles.tipText}>{stop.tip}</Text>
          </View>
        </View>
      )}
    </TouchableOpacity>
  );
}

function DaySection({ day, goals }: { day: Day; goals: string[] }) {
  return (
    <View style={styles.daySection}>
      <View style={styles.dayHeader}>
        <Text style={styles.dayNumber}>Day {day.day}</Text>
        <Text style={styles.dayTheme}>{day.theme}</Text>
      </View>
      {day.stops.map((stop, i) => <StopCard key={i} stop={stop} goals={goals} />)}
      <Text style={styles.daySummary}>{day.summary}</Text>
    </View>
  );
}

// ── Map view ──────────────────────────────────────────────────────────────────

function MapScreen({ itinerary }: { itinerary: Itinerary }) {
  const [activeDay, setActiveDay] = useState(0);
  const day = itinerary.days[activeDay];

  if (Platform.OS === 'web') {
    return (
      <View style={styles.noCoords}>
        <Text style={styles.noCoordsText}>Map view is available in the mobile app.</Text>
      </View>
    );
  }

  if (!day) return null;

  const stops = day.stops.filter(s => s.lat && s.lon);
  if (stops.length === 0) return (
    <View style={styles.noCoords}>
      <Text style={styles.noCoordsText}>No coordinates available for this day.</Text>
    </View>
  );

  const centerLon = stops.reduce((s, p) => s + p.lon, 0) / stops.length;
  const centerLat = stops.reduce((s, p) => s + p.lat, 0) / stops.length;
  const dayColor = DAY_COLORS[activeDay % DAY_COLORS.length];

  const { MapView, Camera, ShapeSource, LineLayer, CircleLayer, SymbolLayer } = MapLibreGL;

  const routeLine: GeoJSON.Feature<GeoJSON.LineString> = {
    type: 'Feature',
    geometry: { type: 'LineString', coordinates: stops.map(s => [s.lon, s.lat]) },
    properties: {},
  };

  const stopPoints: GeoJSON.FeatureCollection<GeoJSON.Point> = {
    type: 'FeatureCollection',
    features: stops.map((s, i) => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [s.lon, s.lat] },
      properties: { name: s.name, index: i + 1, color: dayColor },
    })),
  };

  return (
    <View style={styles.mapContainer}>
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        style={styles.dayTabs}
        contentContainerStyle={styles.dayTabsContent}
      >
        {itinerary.days.map((d, i) => (
          <TouchableOpacity
            key={i}
            style={[styles.dayTab, activeDay === i && { backgroundColor: DAY_COLORS[i % DAY_COLORS.length] }]}
            onPress={() => setActiveDay(i)}
          >
            <Text style={[styles.dayTabText, activeDay === i && styles.dayTabTextActive]}>
              Day {d.day}
            </Text>
          </TouchableOpacity>
        ))}
      </ScrollView>

      <MapView style={styles.map} styleURL="https://demotiles.maplibre.org/style.json">
        <Camera centerCoordinate={[centerLon, centerLat]} zoomLevel={13} animationDuration={500} />
        <ShapeSource id="route" shape={routeLine}>
          <LineLayer
            id="routeLine"
            style={{ lineColor: dayColor, lineWidth: 3, lineOpacity: 0.8, lineDasharray: [2, 1] }}
          />
        </ShapeSource>
        <ShapeSource id="stops" shape={stopPoints}>
          <CircleLayer
            id="stopCircles"
            style={{
              circleRadius: 14,
              circleColor: dayColor,
              circleStrokeWidth: 2,
              circleStrokeColor: '#0f0f0f',
            }}
          />
          <SymbolLayer
            id="stopLabels"
            style={{
              textField: ['get', 'index'],
              textSize: 11,
              textColor: '#fff',
              textFont: ['DIN Offc Pro Bold', 'Arial Unicode MS Bold'],
              textAllowOverlap: true,
            }}
          />
        </ShapeSource>
      </MapView>

      <ScrollView style={styles.mapStopList} contentContainerStyle={{ padding: 16 }}>
        {stops.map((stop, i) => (
          <View key={i} style={styles.mapStopRow}>
            <View style={[styles.mapStopNum, { backgroundColor: dayColor }]}>
              <Text style={styles.mapStopNumText}>{i + 1}</Text>
            </View>
            <View style={styles.mapStopInfo}>
              <Text style={styles.mapStopName}>{stop.name}</Text>
              <Text style={styles.mapStopTime}>{stop.arrival_time} · {stop.duration_min} min</Text>
            </View>
          </View>
        ))}
      </ScrollView>
    </View>
  );
}

// ── Main screen ───────────────────────────────────────────────────────────────

export default function ItineraryScreen() {
  const { data, goals: goalsParam } = useLocalSearchParams<{ data: string; goals: string }>();
  const router = useRouter();
  const itinerary: Itinerary = JSON.parse(data);
  const goals: string[] = goalsParam ? JSON.parse(goalsParam) : [];
  const [view, setView] = useState<'list' | 'map'>('list');

  return (
    <SafeAreaView style={styles.container}>
      <View style={styles.topBar}>
        <TouchableOpacity onPress={() => router.back()}>
          <Text style={styles.back}>← Back</Text>
        </TouchableOpacity>
        <Text style={styles.screenTitle}>Your Trip</Text>
        <View style={styles.viewToggle}>
          <TouchableOpacity onPress={() => setView('list')}>
            <Text style={[styles.toggleBtn, view === 'list' && styles.toggleBtnActive]}>List</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={() => setView('map')}>
            <Text style={[styles.toggleBtn, view === 'map' && styles.toggleBtnActive]}>Map</Text>
          </TouchableOpacity>
        </View>
      </View>

      {view === 'list' ? (
        <ScrollView contentContainerStyle={styles.scroll}>
          <Text style={styles.overview}>{itinerary.overview}</Text>
          {itinerary.days.map(day => <DaySection key={day.day} day={day} goals={goals} />)}
        </ScrollView>
      ) : (
        <MapScreen itinerary={itinerary} />
      )}
    </SafeAreaView>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const { height } = Dimensions.get('window');

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0f0f0f' },
  topBar: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 20, paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: '#1a1a1a',
  },
  back: { color: '#888', fontSize: 14 },
  screenTitle: { color: '#fff', fontSize: 16, fontWeight: '600' },
  viewToggle: { flexDirection: 'row', gap: 12 },
  toggleBtn: { color: '#555', fontSize: 14, fontWeight: '500' },
  toggleBtnActive: { color: '#fff', fontWeight: '700' },

  // List view
  scroll: { padding: 20, paddingBottom: 60 },
  overview: { color: '#aaa', fontSize: 15, lineHeight: 22, marginBottom: 32 },
  daySection: { marginBottom: 40 },
  dayHeader: { marginBottom: 16 },
  dayNumber: { color: '#fff', fontSize: 22, fontWeight: '700' },
  dayTheme: { color: '#666', fontSize: 13, marginTop: 2 },
  stopCard: {
    backgroundColor: '#1a1a1a', borderRadius: 14, padding: 16,
    marginBottom: 10, borderWidth: 1, borderColor: '#2a2a2a',
  },
  stopHeader: { flexDirection: 'row', alignItems: 'center', marginBottom: 8 },
  dot: { width: 8, height: 8, borderRadius: 4, marginRight: 10 },
  stopMeta: { flexDirection: 'row', gap: 12 },
  stopTime: { color: '#fff', fontSize: 13, fontWeight: '600' },
  stopDuration: { color: '#555', fontSize: 13 },
  stopName: { color: '#fff', fontSize: 16, fontWeight: '600', marginBottom: 8 },
  categoryBadge: { alignSelf: 'flex-start', paddingHorizontal: 10, paddingVertical: 4, borderRadius: 8 },
  categoryText: { fontSize: 11, fontWeight: '600', textTransform: 'uppercase', letterSpacing: 0.5 },
  stopDetails: { marginTop: 12 },
  stopDescription: { color: '#aaa', fontSize: 14, lineHeight: 20, marginBottom: 12 },
  tipBox: { backgroundColor: '#111', borderRadius: 10, padding: 12 },
  tipLabel: { color: '#555', fontSize: 11, fontWeight: '600', textTransform: 'uppercase', marginBottom: 4 },
  tipText: { color: '#888', fontSize: 13, lineHeight: 18 },
  daySummary: { color: '#444', fontSize: 13, fontStyle: 'italic', marginTop: 8 },

  // Map view
  mapContainer: { flex: 1 },
  dayTabs: { maxHeight: 48, borderBottomWidth: 1, borderBottomColor: '#1a1a1a' },
  dayTabsContent: { paddingHorizontal: 16, gap: 8, alignItems: 'center' },
  dayTab: {
    paddingHorizontal: 16, paddingVertical: 8, borderRadius: 20,
    backgroundColor: '#1a1a1a', borderWidth: 1, borderColor: '#2a2a2a',
  },
  dayTabText: { color: '#666', fontSize: 13, fontWeight: '500' },
  dayTabTextActive: { color: '#fff', fontWeight: '700' },
  map: { height: height * 0.45 },
  mapStopList: { flex: 1, backgroundColor: '#0f0f0f' },
  mapStopRow: { flexDirection: 'row', alignItems: 'center', marginBottom: 14 },
  mapStopNum: {
    width: 28, height: 28, borderRadius: 14,
    alignItems: 'center', justifyContent: 'center', marginRight: 12,
  },
  mapStopNumText: { color: '#fff', fontSize: 12, fontWeight: '700' },
  mapStopInfo: { flex: 1 },
  mapStopName: { color: '#fff', fontSize: 14, fontWeight: '600' },
  mapStopTime: { color: '#555', fontSize: 12, marginTop: 2 },
  noCoords: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  noCoordsText: { color: '#555', fontSize: 14 },
});
