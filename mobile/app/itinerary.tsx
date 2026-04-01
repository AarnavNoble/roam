import { useLocalSearchParams, useRouter } from 'expo-router';
import { useState, useRef, useEffect, useCallback } from 'react';
import {
  View, Text, ScrollView, TouchableOpacity,
  StyleSheet, SafeAreaView, Dimensions, Platform, Animated, Image,
} from 'react-native';
import { Itinerary, Day, Stop, FeatureExplanation, submitFeedback, getStoredItinerary } from '../services/api';

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

// ── Toast ─────────────────────────────────────────────────────────────────────

export function useToast() {
  const [message, setMessage] = useState('');
  const opacity = useRef(new Animated.Value(0)).current;
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const show = useCallback((msg: string) => {
    if (timer.current) clearTimeout(timer.current);
    setMessage(msg);
    Animated.sequence([
      Animated.timing(opacity, { toValue: 1, duration: 200, useNativeDriver: true }),
      Animated.delay(2200),
      Animated.timing(opacity, { toValue: 0, duration: 300, useNativeDriver: true }),
    ]).start();
    timer.current = setTimeout(() => setMessage(''), 2700);
  }, [opacity]);

  const Toast = message ? (
    <Animated.View style={[styles.toast, { opacity }]} pointerEvents="none">
      <Text style={styles.toastText}>{message}</Text>
    </Animated.View>
  ) : null;

  return { show, Toast };
}

// ── List view components ──────────────────────────────────────────────────────

const FEATURE_LABELS: Record<string, string> = {
  semantic_score: 'Relevance',
  category_match: 'Goal match',
  name_length_norm: 'Specificity',
  has_description: 'Well known',
  cuisine_match: 'Food match',
  nature_match: 'Nature match',
  history_match: 'Culture match',
  nightlife_match: 'Nightlife match',
};

function ContributionBar({ name, value, max }: { name: string; value: number; max: number }) {
  const width = max > 0 ? Math.abs(value) / max * 100 : 0;
  const positive = value >= 0;
  return (
    <View style={styles.contribRow}>
      <Text style={styles.contribName}>{FEATURE_LABELS[name] ?? name}</Text>
      <View style={styles.contribBarBg}>
        <View style={[
          styles.contribBar,
          { width: `${Math.min(width, 100)}%`, backgroundColor: positive ? '#10B981' : '#EF4444' },
        ]} />
      </View>
      <Text style={styles.contribValue}>{value > 0 ? '+' : ''}{value.toFixed(2)}</Text>
    </View>
  );
}

function StopCard({ stop, goals, explanation, onRetrained }: {
  stop: Stop; goals: string[]; explanation?: FeatureExplanation; onRetrained?: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [feedback, setFeedback] = useState<'up' | 'down' | null>(null);
  const color = CATEGORY_COLORS[stop.category] || '#6B7280';

  const handleFeedback = async (relevant: boolean) => {
    if (feedback) return;   // already voted
    setFeedback(relevant ? 'up' : 'down');
    try {
      const result = await submitFeedback(stop.id || 0, relevant, stop.name, stop.category, goals);
      if (result.retrained) onRetrained?.();
    } catch {}
  };

  return (
    <TouchableOpacity style={styles.stopCard} onPress={() => setExpanded(e => !e)} activeOpacity={0.8}>
      {stop.photo_url ? (
        <Image
          source={{ uri: stop.photo_url }}
          style={styles.stopPhoto}
          resizeMode="cover"
        />
      ) : null}
      <View style={styles.stopContent}>
      <View style={styles.stopHeader}>
        <View style={[styles.dot, { backgroundColor: color }]} />
        <View style={styles.stopMeta}>
          <Text style={styles.stopTime}>{stop.arrival_time}</Text>
          <Text style={styles.stopDuration}>{stop.duration_min} min</Text>
        </View>
        <View style={styles.feedbackRow}>
          <TouchableOpacity
            onPress={() => handleFeedback(true)}
            hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
            disabled={!!feedback}
          >
            <Text style={[
              styles.feedbackBtn,
              feedback === 'up' && styles.feedbackUp,
              feedback === 'down' && styles.feedbackDimmed,
            ]}>👍</Text>
          </TouchableOpacity>
          <TouchableOpacity
            onPress={() => handleFeedback(false)}
            hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
            disabled={!!feedback}
          >
            <Text style={[
              styles.feedbackBtn,
              feedback === 'down' && styles.feedbackDown,
              feedback === 'up' && styles.feedbackDimmed,
            ]}>👎</Text>
          </TouchableOpacity>
        </View>
      </View>
      <Text style={styles.stopName}>{stop.name}</Text>
      <View style={styles.stopSubRow}>
        <View style={[styles.categoryBadge, { backgroundColor: color + '20' }]}>
          <Text style={[styles.categoryText, { color }]}>{stop.category.toUpperCase()}</Text>
        </View>
        {stop.description ? (
          <Text style={styles.stopDescPreview} numberOfLines={1}>
            {stop.description}
          </Text>
        ) : null}
      </View>
      {expanded && (
        <View style={styles.stopDetails}>
          <Text style={styles.stopDescription}>{stop.description}</Text>
          <View style={styles.tipBox}>
            <Text style={styles.tipLabel}>Tip</Text>
            <Text style={styles.tipText}>{stop.tip}</Text>
          </View>
          {explanation && (
            <View style={styles.explainBox}>
              <Text style={styles.explainTitle}>Why this place?</Text>
              {(() => {
                const features = explanation.features;
                const maxVal = Math.max(...Object.values(features), 0.01);
                return Object.entries(features)
                  .filter(([, v]) => v > 0)
                  .sort((a, b) => b[1] - a[1])
                  .slice(0, 4)
                  .map(([name, value]) => (
                    <ContributionBar key={name} name={name} value={value} max={maxVal} />
                  ));
              })()}
            </View>
          )}
        </View>
      )}
      </View>
    </TouchableOpacity>
  );
}

function DaySection({ day, goals, explanations, onRetrained }: {
  day: Day; goals: string[]; explanations?: Record<string, FeatureExplanation>; onRetrained?: () => void;
}) {
  return (
    <View style={styles.daySection}>
      <View style={styles.dayHeader}>
        <Text style={styles.dayNumber}>Day {day.day}</Text>
        <Text style={styles.dayTheme}>{day.theme}</Text>
      </View>
      {day.stops.map((stop, i) => (
        <StopCard key={i} stop={stop} goals={goals} explanation={explanations?.[stop.name]} onRetrained={onRetrained} />
      ))}
      <Text style={styles.daySummary}>{day.summary}</Text>
    </View>
  );
}

// ── Web map (Leaflet) ─────────────────────────────────────────────────────────

function WebMapView({ stops, color, mapId }: { stops: Stop[]; color: string; mapId: string }) {
  const mapInstanceRef = useRef<any>(null);

  useEffect(() => {
    if (stops.length === 0) return;
    let cancelled = false;

    // Inject Leaflet CSS once
    if (!document.getElementById('leaflet-css')) {
      const link = document.createElement('link');
      link.id = 'leaflet-css';
      link.rel = 'stylesheet';
      link.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
      document.head.appendChild(link);
    }

    // Destroy previous instance
    if (mapInstanceRef.current) {
      mapInstanceRef.current.remove();
      mapInstanceRef.current = null;
    }

    import('leaflet').then((mod) => {
      if (cancelled) return;
      const container = document.getElementById(mapId);
      if (!container) return;

      const L = (mod as any).default ?? mod;
      const latlngs: [number, number][] = stops.map(s => [s.lat, s.lon]);

      const map = L.map(container, { zoomControl: true });
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '© OpenStreetMap contributors',
        maxZoom: 19,
      }).addTo(map);

      // Route polyline
      L.polyline(latlngs, { color, weight: 3, opacity: 0.85, dashArray: '8 5' }).addTo(map);

      // Numbered markers
      stops.forEach((stop, i) => {
        const icon = L.divIcon({
          html: `<div style="width:28px;height:28px;border-radius:50%;background:${color};color:#fff;font-size:12px;font-weight:700;display:flex;align-items:center;justify-content:center;border:2px solid #0f0f0f;box-sizing:border-box;line-height:28px;text-align:center;">${i + 1}</div>`,
          className: '',
          iconSize: [28, 28],
          iconAnchor: [14, 14],
          popupAnchor: [0, -16],
        });
        L.marker([stop.lat, stop.lon], { icon })
          .bindPopup(
            `<div style="font-family:sans-serif;min-width:160px"><b style="font-size:13px">${stop.name}</b><br/><span style="color:#666;font-size:12px">${stop.arrival_time} · ${stop.duration_min} min</span></div>`,
            { closeButton: false }
          )
          .addTo(map);
      });

      map.fitBounds(L.latLngBounds(latlngs), { padding: [40, 40] });
      mapInstanceRef.current = map;
    });

    return () => {
      cancelled = true;
      if (mapInstanceRef.current) {
        mapInstanceRef.current.remove();
        mapInstanceRef.current = null;
      }
    };
  }, [stops, color, mapId]);

  return <View nativeID={mapId} style={styles.map} />;
}

// ── Map view ──────────────────────────────────────────────────────────────────

function MapScreen({ itinerary }: { itinerary: Itinerary }) {
  const [activeDay, setActiveDay] = useState(0);
  const day = itinerary.days[activeDay];

  if (!day) return null;

  const stops = day.stops.filter(s => s.lat && s.lon);
  const dayColor = DAY_COLORS[activeDay % DAY_COLORS.length];

  const DayTabs = (
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
  );

  const StopList = (
    <ScrollView style={styles.mapStopList} contentContainerStyle={{ padding: 16 }}>
      {stops.length === 0 ? (
        <Text style={styles.noCoordsText}>No coordinates for this day.</Text>
      ) : stops.map((stop, i) => (
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
  );

  if (Platform.OS === 'web') {
    return (
      <View style={styles.mapContainer}>
        {DayTabs}
        {stops.length > 0
          ? <WebMapView key={`day-${activeDay}`} stops={stops} color={dayColor} mapId={`roam-map-${activeDay}`} />
          : <View style={[styles.map, styles.noCoords]}><Text style={styles.noCoordsText}>No coordinates for this day.</Text></View>
        }
        {StopList}
      </View>
    );
  }

  // Native: MapLibre
  const centerLon = stops.reduce((s, p) => s + p.lon, 0) / stops.length;
  const centerLat = stops.reduce((s, p) => s + p.lat, 0) / stops.length;

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
      {DayTabs}
      {stops.length === 0
        ? <View style={[styles.map, styles.noCoords]}><Text style={styles.noCoordsText}>No coordinates for this day.</Text></View>
        : <MapView style={styles.map} styleURL="https://demotiles.maplibre.org/style.json">
            <Camera centerCoordinate={[centerLon, centerLat]} zoomLevel={13} animationDuration={500} />
            <ShapeSource id="route" shape={routeLine}>
              <LineLayer id="routeLine" style={{ lineColor: dayColor, lineWidth: 3, lineOpacity: 0.8, lineDasharray: [2, 1] }} />
            </ShapeSource>
            <ShapeSource id="stops" shape={stopPoints}>
              <CircleLayer id="stopCircles" style={{ circleRadius: 14, circleColor: dayColor, circleStrokeWidth: 2, circleStrokeColor: '#0f0f0f' }} />
              <SymbolLayer id="stopLabels" style={{ textField: ['get', 'index'], textSize: 11, textColor: '#fff', textFont: ['DIN Offc Pro Bold', 'Arial Unicode MS Bold'], textAllowOverlap: true }} />
            </ShapeSource>
          </MapView>
      }
      {StopList}
    </View>
  );
}

// ── Main screen ───────────────────────────────────────────────────────────────

export default function ItineraryScreen() {
  const { goals: goalsParam } = useLocalSearchParams<{ goals: string }>();
  const router = useRouter();
  const itinerary = getStoredItinerary();
  const goals: string[] = goalsParam ? JSON.parse(goalsParam) : [];
  const [view, setView] = useState<'list' | 'map'>('list');
  const { show: showToast, Toast } = useToast();

  if (!itinerary) {
    return (
      <SafeAreaView style={styles.container}>
        <View style={styles.noCoords}>
          <Text style={styles.noCoordsText}>No itinerary loaded. Go back and generate one.</Text>
          <TouchableOpacity onPress={() => router.replace('/')} style={{ marginTop: 16 }}>
            <Text style={{ color: '#3B82F6', fontSize: 14 }}>← Go home</Text>
          </TouchableOpacity>
        </View>
      </SafeAreaView>
    );
  }

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
          {itinerary.days.map(day => (
            <DaySection
              key={day.day} day={day} goals={goals}
              explanations={itinerary.ranking_explanations}
              onRetrained={() => showToast('Model improved from your feedback')}
            />
          ))}
        </ScrollView>
      ) : (
        <MapScreen itinerary={itinerary} />
      )}

      {Toast}
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
    backgroundColor: '#1a1a1a', borderRadius: 14,
    marginBottom: 10, borderWidth: 1, borderColor: '#2a2a2a',
    overflow: 'hidden',
  },
  stopPhoto: {
    width: '100%', height: 140,
  },
  stopContent: { padding: 16 },
  stopHeader: { flexDirection: 'row', alignItems: 'center', marginBottom: 8 },
  dot: { width: 8, height: 8, borderRadius: 4, marginRight: 10 },
  stopMeta: { flexDirection: 'row', gap: 12 },
  stopTime: { color: '#fff', fontSize: 13, fontWeight: '600' },
  stopDuration: { color: '#555', fontSize: 13 },
  stopName: { color: '#fff', fontSize: 16, fontWeight: '600', marginBottom: 8 },
  categoryBadge: { alignSelf: 'flex-start', paddingHorizontal: 10, paddingVertical: 4, borderRadius: 8 },
  categoryText: { fontSize: 11, fontWeight: '600', textTransform: 'uppercase', letterSpacing: 0.5 },
  stopSubRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 6, flexWrap: 'nowrap' },
  stopDescPreview: { fontSize: 12, color: '#555', flex: 1 },
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

  // Feedback
  feedbackRow: { flexDirection: 'row', gap: 6, marginLeft: 'auto' },
  feedbackBtn: { fontSize: 16, opacity: 0.4, paddingHorizontal: 4 },
  feedbackUp: { opacity: 1 },
  feedbackDown: { opacity: 1 },
  feedbackDimmed: { opacity: 0.15 },

  // Toast
  toast: {
    position: 'absolute', bottom: 32, alignSelf: 'center',
    backgroundColor: '#1a1a1a', borderRadius: 20, paddingHorizontal: 18, paddingVertical: 10,
    borderWidth: 1, borderColor: '#2a2a2a',
  },
  toastText: { color: '#fff', fontSize: 13, fontWeight: '500' },

  // Explainability
  explainBox: { marginTop: 12, backgroundColor: '#111', borderRadius: 10, padding: 12 },
  explainTitle: { color: '#888', fontSize: 11, fontWeight: '600', textTransform: 'uppercase', marginBottom: 8, letterSpacing: 0.5 },
  contribRow: { flexDirection: 'row', alignItems: 'center', marginBottom: 6 },
  contribName: { color: '#888', fontSize: 11, width: 90 },
  contribBarBg: { flex: 1, height: 6, backgroundColor: '#222', borderRadius: 3, marginHorizontal: 8 },
  contribBar: { height: 6, borderRadius: 3 },
  contribValue: { color: '#666', fontSize: 10, width: 42, textAlign: 'right' },
});
