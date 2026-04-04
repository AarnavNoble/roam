import { useLocalSearchParams, useRouter } from 'expo-router';
import { useState, useRef, useEffect, useCallback } from 'react';
import {
  View, Text, ScrollView, TouchableOpacity,
  StyleSheet, SafeAreaView, Dimensions, Platform, Animated, Image,
  LayoutAnimation, UIManager, Share,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Itinerary, Day, Stop, FeatureExplanation, submitFeedback, getStoredItinerary, formatItineraryAsText } from '../services/api';

if (Platform.OS === 'android' && UIManager.setLayoutAnimationEnabledExperimental) {
  UIManager.setLayoutAnimationEnabledExperimental(true);
}

let MapLibreGL: any = null;
if (Platform.OS !== 'web') {
  MapLibreGL = require('@maplibre/maplibre-react-native').default;
  MapLibreGL.setAccessToken(null);
}

const CATEGORY_COLORS: Record<string, string> = {
  food: '#F59E0B', nature: '#10B981', history: '#8B5CF6', culture: '#3B82F6',
  nightlife: '#EC4899', shopping: '#F97316', adventure: '#EF4444', attraction: '#6B7280',
};
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
      <View style={styles.toastDot} />
      <Text style={styles.toastText}>{message}</Text>
    </Animated.View>
  ) : null;

  return { show, Toast };
}

// ── List components ───────────────────────────────────────────────────────────

const FEATURE_LABELS: Record<string, string> = {
  semantic_score: 'Relevance', category_match: 'Goal match',
  name_length_norm: 'Specificity', has_description: 'Well known',
  cuisine_match: 'Food match', nature_match: 'Nature match',
  history_match: 'Culture match', nightlife_match: 'Nightlife match',
};

function ContributionBar({ name, value, max }: { name: string; value: number; max: number }) {
  const finalWidth = max > 0 ? Math.abs(value) / max * 100 : 0;
  const widthAnim = useRef(new Animated.Value(0)).current;
  const positive = value >= 0;
  useEffect(() => { Animated.timing(widthAnim, { toValue: Math.min(finalWidth, 100), duration: 600, delay: 100, useNativeDriver: false }).start(); }, []);
  return (
    <View style={styles.contribRow}>
      <Text style={styles.contribName}>{FEATURE_LABELS[name] ?? name}</Text>
      <View style={styles.contribBarBg}>
        <Animated.View style={[styles.contribBar, {
          width: widthAnim.interpolate({ inputRange: [0, 100], outputRange: ['0%', '100%'] }),
          backgroundColor: positive ? '#10B981' : '#EF4444',
        }]} />
      </View>
      <Text style={styles.contribValue}>{value > 0 ? '+' : ''}{value.toFixed(2)}</Text>
    </View>
  );
}

function StopCard({ stop, goals, explanation, onRetrained, index, isLast, dayColor }: {
  stop: Stop; goals: string[]; explanation?: FeatureExplanation;
  onRetrained?: () => void; index: number; isLast: boolean; dayColor: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const [feedback, setFeedback] = useState<'up' | 'down' | null>(null);
  const pressScale = useRef(new Animated.Value(1)).current;
  const color = CATEGORY_COLORS[stop.category] || '#6B7280';

  const onPressIn  = () => Animated.spring(pressScale, { toValue: 0.98, useNativeDriver: true, speed: 30, bounciness: 0 }).start();
  const onPressOut = () => Animated.spring(pressScale, { toValue: 1.0,  useNativeDriver: true, speed: 20, bounciness: 4 }).start();

  const toggleExpand = () => {
    LayoutAnimation.configureNext(LayoutAnimation.create(250, LayoutAnimation.Types.easeInEaseOut, LayoutAnimation.Properties.opacity));
    setExpanded(e => !e);
  };

  const handleFeedback = async (relevant: boolean) => {
    if (feedback) return;
    setFeedback(relevant ? 'up' : 'down');
    try { const r = await submitFeedback(stop.id || 0, relevant, stop.name, stop.category, goals); if (r.retrained) onRetrained?.(); } catch {}
  };

  return (
    <View style={styles.timelineRow}>
      {/* Timeline connector */}
      <View style={styles.timelineTrack}>
        <View style={[styles.timelineDot, { backgroundColor: dayColor }]}>
          <Text style={styles.timelineDotText}>{index + 1}</Text>
        </View>
        {!isLast && <View style={[styles.timelineLine, { backgroundColor: dayColor + '30' }]} />}
      </View>

      {/* Card */}
      <Animated.View style={[styles.cardOuter, { transform: [{ scale: pressScale }] }]}>
        <LinearGradient
          colors={['rgba(255,255,255,0.07)', 'rgba(255,255,255,0.02)', 'rgba(255,255,255,0.0)']}
          start={{ x: 0, y: 0 }} end={{ x: 0, y: 1 }}
          style={styles.cardGradientBorder}
        >
          <TouchableOpacity style={styles.stopCard} onPress={toggleExpand} onPressIn={onPressIn} onPressOut={onPressOut} activeOpacity={1}>
            {stop.photo_url ? <Image source={{ uri: stop.photo_url }} style={styles.stopPhoto} resizeMode="cover" /> : null}

            <View style={styles.stopContent}>
              <View style={styles.stopHeader}>
                <View style={styles.stopMeta}>
                  <Text style={styles.stopTime}>{stop.arrival_time}</Text>
                  <Text style={styles.stopDuration}>{stop.duration_min} min</Text>
                </View>
                <View style={styles.feedbackRow}>
                  <TouchableOpacity onPress={() => handleFeedback(true)} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }} disabled={!!feedback}>
                    <Text style={[styles.feedbackBtn, feedback === 'up' && styles.feedbackUp, feedback === 'down' && styles.feedbackDimmed]}>👍</Text>
                  </TouchableOpacity>
                  <TouchableOpacity onPress={() => handleFeedback(false)} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }} disabled={!!feedback}>
                    <Text style={[styles.feedbackBtn, feedback === 'down' && styles.feedbackDown, feedback === 'up' && styles.feedbackDimmed]}>👎</Text>
                  </TouchableOpacity>
                </View>
              </View>

              <Text style={styles.stopName}>{stop.name}</Text>

              <View style={styles.stopSubRow}>
                <View style={[styles.categoryBadge, { backgroundColor: color + '15', borderColor: color + '30' }]}>
                  <Text style={[styles.categoryText, { color }]}>{stop.category.toUpperCase()}</Text>
                </View>
                {stop.description ? <Text style={styles.stopDescPreview} numberOfLines={1}>{stop.description}</Text> : null}
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
                        const f = explanation.features;
                        const mx = Math.max(...Object.values(f), 0.01);
                        return Object.entries(f).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]).slice(0, 4)
                          .map(([n, v]) => <ContributionBar key={n} name={n} value={v} max={mx} />);
                      })()}
                    </View>
                  )}
                </View>
              )}
            </View>
          </TouchableOpacity>
        </LinearGradient>
      </Animated.View>
    </View>
  );
}

function DaySection({ day, goals, explanations, onRetrained }: {
  day: Day; goals: string[]; explanations?: Record<string, FeatureExplanation>; onRetrained?: () => void;
}) {
  const dayColor = DAY_COLORS[(day.day - 1) % DAY_COLORS.length];
  return (
    <View style={styles.daySection}>
      <View style={styles.dayHeader}>
        <View style={styles.dayHeaderLeft}>
          <Text style={[styles.dayNumber, { color: dayColor }]}>Day {day.day}</Text>
          <Text style={styles.dayTheme}>{day.theme}</Text>
        </View>
        <View style={[styles.dayStopCount, { backgroundColor: dayColor + '18', borderColor: dayColor + '30' }]}>
          <Text style={[styles.dayStopCountText, { color: dayColor }]}>{day.stops.length} stops</Text>
        </View>
      </View>

      {day.stops.map((stop, i) => (
        <StopCard
          key={i} index={i} stop={stop} goals={goals}
          explanation={explanations?.[stop.name]}
          onRetrained={onRetrained}
          isLast={i === day.stops.length - 1}
          dayColor={dayColor}
        />
      ))}

      {day.summary ? <Text style={styles.daySummary}>{day.summary}</Text> : null}
    </View>
  );
}

// ── Web map (Leaflet) ─────────────────────────────────────────────────────────

function WebMapView({ stops, color, mapId }: { stops: Stop[]; color: string; mapId: string }) {
  const mapInstanceRef = useRef<any>(null);
  useEffect(() => {
    if (stops.length === 0) return;
    let cancelled = false;
    if (!document.getElementById('leaflet-css')) {
      const link = document.createElement('link');
      link.id = 'leaflet-css'; link.rel = 'stylesheet';
      link.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
      document.head.appendChild(link);
    }
    if (mapInstanceRef.current) { mapInstanceRef.current.remove(); mapInstanceRef.current = null; }
    import('leaflet').then((mod) => {
      if (cancelled) return;
      const container = document.getElementById(mapId);
      if (!container) return;
      const L = (mod as any).default ?? mod;
      const latlngs: [number, number][] = stops.map(s => [s.lat, s.lon]);
      const map = L.map(container, { zoomControl: true });
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '© OSM contributors', maxZoom: 19 }).addTo(map);
      L.polyline(latlngs, { color, weight: 3, opacity: 0.85, dashArray: '8 5' }).addTo(map);
      stops.forEach((stop, i) => {
        const icon = L.divIcon({
          html: `<div style="width:28px;height:28px;border-radius:50%;background:${color};color:#fff;font-size:12px;font-weight:700;display:flex;align-items:center;justify-content:center;border:2px solid #09090b;box-sizing:border-box;">${i + 1}</div>`,
          className: '', iconSize: [28, 28], iconAnchor: [14, 14], popupAnchor: [0, -16],
        });
        L.marker([stop.lat, stop.lon], { icon }).bindPopup(
          `<div style="font-family:system-ui;min-width:160px"><b style="font-size:13px">${stop.name}</b><br/><span style="color:#666;font-size:12px">${stop.arrival_time} · ${stop.duration_min} min</span></div>`,
          { closeButton: false }
        ).addTo(map);
      });
      map.fitBounds(L.latLngBounds(latlngs), { padding: [40, 40] });
      mapInstanceRef.current = map;
    });
    return () => { cancelled = true; if (mapInstanceRef.current) { mapInstanceRef.current.remove(); mapInstanceRef.current = null; } };
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
    <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.dayTabs} contentContainerStyle={styles.dayTabsContent}>
      {itinerary.days.map((d, i) => (
        <TouchableOpacity
          key={i} style={[styles.dayTab, activeDay === i && { backgroundColor: DAY_COLORS[i % DAY_COLORS.length], borderColor: DAY_COLORS[i % DAY_COLORS.length] }]}
          onPress={() => setActiveDay(i)}
        >
          <Text style={[styles.dayTabText, activeDay === i && styles.dayTabTextActive]}>Day {d.day}</Text>
        </TouchableOpacity>
      ))}
    </ScrollView>
  );

  const StopList = (
    <ScrollView style={styles.mapStopList} contentContainerStyle={{ padding: 16 }}>
      {stops.length === 0
        ? <Text style={styles.noCoordsText}>No coordinates for this day.</Text>
        : stops.map((stop, i) => (
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
          : <View style={[styles.map, styles.noCoords]}><Text style={styles.noCoordsText}>No coordinates.</Text></View>}
        {StopList}
      </View>
    );
  }

  const cLon = stops.reduce((s, p) => s + p.lon, 0) / stops.length;
  const cLat = stops.reduce((s, p) => s + p.lat, 0) / stops.length;
  const { MapView, Camera, ShapeSource, LineLayer, CircleLayer, SymbolLayer } = MapLibreGL;
  const routeLine: GeoJSON.Feature<GeoJSON.LineString> = { type: 'Feature', geometry: { type: 'LineString', coordinates: stops.map(s => [s.lon, s.lat]) }, properties: {} };
  const stopPoints: GeoJSON.FeatureCollection<GeoJSON.Point> = { type: 'FeatureCollection', features: stops.map((s, i) => ({ type: 'Feature', geometry: { type: 'Point', coordinates: [s.lon, s.lat] }, properties: { name: s.name, index: i + 1, color: dayColor } })) };

  return (
    <View style={styles.mapContainer}>
      {DayTabs}
      {stops.length === 0
        ? <View style={[styles.map, styles.noCoords]}><Text style={styles.noCoordsText}>No coordinates.</Text></View>
        : <MapView style={styles.map} styleURL="https://demotiles.maplibre.org/style.json">
            <Camera centerCoordinate={[cLon, cLat]} zoomLevel={13} animationDuration={500} />
            <ShapeSource id="route" shape={routeLine}><LineLayer id="routeLine" style={{ lineColor: dayColor, lineWidth: 3, lineOpacity: 0.8, lineDasharray: [2, 1] }} /></ShapeSource>
            <ShapeSource id="stops" shape={stopPoints}>
              <CircleLayer id="stopCircles" style={{ circleRadius: 14, circleColor: dayColor, circleStrokeWidth: 2, circleStrokeColor: '#09090b' }} />
              <SymbolLayer id="stopLabels" style={{ textField: ['get', 'index'], textSize: 11, textColor: '#fff', textFont: ['DIN Offc Pro Bold', 'Arial Unicode MS Bold'], textAllowOverlap: true }} />
            </ShapeSource>
          </MapView>}
      {StopList}
    </View>
  );
}

// ── Main screen ───────────────────────────────────────────────────────────────

export default function ItineraryScreen() {
  const { goals: goalsParam, city: cityParam } = useLocalSearchParams<{ goals: string; city: string }>();
  const router = useRouter();
  const itinerary = getStoredItinerary();
  const goals: string[] = goalsParam ? JSON.parse(goalsParam) : [];
  const city = cityParam ?? 'My Trip';
  const [view, setView] = useState<'list' | 'map'>('list');
  const { show: showToast, Toast } = useToast();

  const toggleSlide = useRef(new Animated.Value(0)).current;
  const SEGMENT_WIDTH = 56;
  const setViewAnimated = (v: 'list' | 'map') => {
    setView(v);
    Animated.timing(toggleSlide, { toValue: v === 'list' ? 0 : 1, duration: 200, useNativeDriver: true }).start();
  };

  const sectionAnims = useRef((itinerary?.days ?? []).map(() => new Animated.Value(0))).current;
  useEffect(() => {
    if (!itinerary) return;
    const t = setTimeout(() => {
      Animated.stagger(80, sectionAnims.map(a => Animated.timing(a, { toValue: 1, duration: 450, useNativeDriver: true }))).start();
    }, 100);
    return () => clearTimeout(t);
  }, []);

  if (!itinerary) {
    return (
      <SafeAreaView style={styles.container}>
        <View style={styles.noCoords}>
          <Text style={styles.noCoordsText}>No itinerary loaded.</Text>
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
        <TouchableOpacity onPress={() => router.back()} style={styles.backBtn}>
          <Text style={styles.backChevron}>‹</Text>
          <Text style={styles.backLabel}>Back</Text>
        </TouchableOpacity>
        <Text style={styles.screenTitle}>{city}</Text>
        <TouchableOpacity
          style={styles.shareBtn}
          onPress={() => Share.share({ message: formatItineraryAsText(city, itinerary) })}
        >
          <Text style={styles.shareBtnText}>Share</Text>
        </TouchableOpacity>
        <View style={styles.viewTogglePill}>
          <Animated.View style={[styles.pillSelector, {
            transform: [{ translateX: toggleSlide.interpolate({ inputRange: [0, 1], outputRange: [2, SEGMENT_WIDTH + 2] }) }],
          }]} />
          <TouchableOpacity style={styles.pillOption} onPress={() => setViewAnimated('list')}>
            <Text style={[styles.pillOptionText, view === 'list' && styles.pillOptionTextActive]}>List</Text>
          </TouchableOpacity>
          <TouchableOpacity style={styles.pillOption} onPress={() => setViewAnimated('map')}>
            <Text style={[styles.pillOptionText, view === 'map' && styles.pillOptionTextActive]}>Map</Text>
          </TouchableOpacity>
        </View>
      </View>

      {view === 'list' ? (
        <ScrollView contentContainerStyle={styles.scroll}>
          <View style={styles.overviewAccent}>
            <Text style={styles.overview}>{itinerary.overview}</Text>
          </View>
          {itinerary.days.map((day, i) => (
            <Animated.View key={day.day} style={{
              opacity: sectionAnims[i],
              transform: [{ translateY: sectionAnims[i].interpolate({ inputRange: [0, 1], outputRange: [20, 0] }) }],
            }}>
              <DaySection day={day} goals={goals} explanations={itinerary.ranking_explanations} onRetrained={() => showToast('Model improved from your feedback')} />
            </Animated.View>
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
  container: { flex: 1, backgroundColor: '#09090b' },

  // Top bar
  topBar: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 20, paddingVertical: 14,
    borderBottomWidth: 1, borderBottomColor: 'rgba(255,255,255,0.06)',
  },
  backBtn:     { flexDirection: 'row', alignItems: 'center', gap: 2 },
  backChevron: { color: 'rgba(255,255,255,0.4)', fontSize: 22, fontWeight: '300', lineHeight: 26 },
  backLabel:   { color: 'rgba(255,255,255,0.4)', fontSize: 14 },
  screenTitle: { color: '#fff', fontSize: 16, fontWeight: '700', letterSpacing: 0.3 },

  shareBtn: {
    paddingHorizontal: 12, paddingVertical: 6,
    backgroundColor: 'rgba(255,255,255,0.06)',
    borderRadius: 100, borderWidth: 1, borderColor: 'rgba(255,255,255,0.10)',
  },
  shareBtnText: { color: 'rgba(255,255,255,0.5)', fontSize: 12, fontWeight: '500' },

  // Pill toggle
  viewTogglePill: {
    flexDirection: 'row', backgroundColor: 'rgba(255,255,255,0.05)',
    borderRadius: 20, padding: 2,
    borderWidth: 1, borderColor: 'rgba(255,255,255,0.08)',
    position: 'relative', width: 120,
  },
  pillSelector: {
    position: 'absolute', top: 2, left: 0,
    width: 56, height: 28, backgroundColor: '#fff', borderRadius: 18,
  },
  pillOption:          { width: 56, height: 28, alignItems: 'center', justifyContent: 'center', zIndex: 1 },
  pillOptionText:      { color: 'rgba(255,255,255,0.4)', fontSize: 13, fontWeight: '500' },
  pillOptionTextActive: { color: '#09090b', fontWeight: '700' },

  // List
  scroll: { padding: 20, paddingBottom: 60 },
  overviewAccent: { borderLeftWidth: 2, borderLeftColor: 'rgba(255,255,255,0.08)', paddingLeft: 14, marginBottom: 36 },
  overview: { color: 'rgba(255,255,255,0.55)', fontSize: 15, lineHeight: 24 },

  // Day section
  daySection: { marginBottom: 48 },
  dayHeader: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    marginBottom: 20, paddingBottom: 12,
    borderBottomWidth: 1, borderBottomColor: 'rgba(255,255,255,0.06)',
  },
  dayHeaderLeft: {},
  dayNumber: { fontSize: 24, fontWeight: '800', letterSpacing: -0.5 },
  dayTheme:  { color: 'rgba(255,255,255,0.35)', fontSize: 14, marginTop: 2 },
  dayStopCount: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 100, borderWidth: 1 },
  dayStopCountText: { fontSize: 11, fontWeight: '600' },

  // Timeline
  timelineRow: { flexDirection: 'row', minHeight: 80 },
  timelineTrack: { width: 32, alignItems: 'center', marginRight: 4 },
  timelineDot: {
    width: 24, height: 24, borderRadius: 12,
    alignItems: 'center', justifyContent: 'center',
    zIndex: 1,
  },
  timelineDotText: { color: '#fff', fontSize: 10, fontWeight: '800' },
  timelineLine: { width: 2, flex: 1, marginTop: -2, marginBottom: -2 },

  // Card with gradient border
  cardOuter: { flex: 1, marginBottom: 12 },
  cardGradientBorder: { borderRadius: 16, padding: 1 },
  stopCard: {
    backgroundColor: '#111113', borderRadius: 15,
    overflow: 'hidden',
    borderTopWidth: 1, borderTopColor: 'rgba(255,255,255,0.06)',
  },
  stopPhoto:   { width: '100%', height: 140 },
  stopContent: { padding: 16 },
  stopHeader:  { flexDirection: 'row', alignItems: 'center', marginBottom: 10 },
  stopMeta:    { flexDirection: 'row', gap: 10, flex: 1 },
  stopTime:     { color: '#fff', fontSize: 13, fontWeight: '600' },
  stopDuration: { color: 'rgba(255,255,255,0.3)', fontSize: 13 },
  stopName:     { color: '#fff', fontSize: 17, fontWeight: '700', marginBottom: 8, letterSpacing: -0.3 },

  categoryBadge: { alignSelf: 'flex-start', paddingHorizontal: 10, paddingVertical: 4, borderRadius: 8, borderWidth: 1 },
  categoryText:  { fontSize: 10, fontWeight: '700', textTransform: 'uppercase', letterSpacing: 0.6 },
  stopSubRow:    { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 4, flexWrap: 'nowrap' },
  stopDescPreview: { fontSize: 12, color: 'rgba(255,255,255,0.3)', flex: 1 },

  stopDetails:     { marginTop: 14, paddingTop: 14, borderTopWidth: 1, borderTopColor: 'rgba(255,255,255,0.06)' },
  stopDescription: { color: 'rgba(255,255,255,0.6)', fontSize: 14, lineHeight: 21, marginBottom: 12 },
  tipBox:   { backgroundColor: 'rgba(255,255,255,0.03)', borderRadius: 10, padding: 12, borderWidth: 1, borderColor: 'rgba(255,255,255,0.06)' },
  tipLabel: { color: 'rgba(255,255,255,0.3)', fontSize: 11, fontWeight: '700', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 4 },
  tipText:  { color: 'rgba(255,255,255,0.5)', fontSize: 13, lineHeight: 18 },
  daySummary: { color: 'rgba(255,255,255,0.2)', fontSize: 13, fontStyle: 'italic', marginTop: 10, paddingLeft: 36 },

  // Map
  mapContainer:   { flex: 1 },
  dayTabs:        { maxHeight: 48, borderBottomWidth: 1, borderBottomColor: 'rgba(255,255,255,0.06)' },
  dayTabsContent: { paddingHorizontal: 16, gap: 8, alignItems: 'center' },
  dayTab: {
    paddingHorizontal: 16, paddingVertical: 8, borderRadius: 20,
    backgroundColor: 'rgba(255,255,255,0.05)', borderWidth: 1, borderColor: 'rgba(255,255,255,0.08)',
  },
  dayTabText:       { color: 'rgba(255,255,255,0.4)', fontSize: 13, fontWeight: '500' },
  dayTabTextActive: { color: '#fff', fontWeight: '700' },
  map:              { height: height * 0.45 },
  mapStopList:      { flex: 1, backgroundColor: '#09090b' },
  mapStopRow:       { flexDirection: 'row', alignItems: 'center', marginBottom: 14 },
  mapStopNum:       { width: 28, height: 28, borderRadius: 14, alignItems: 'center', justifyContent: 'center', marginRight: 12 },
  mapStopNumText:   { color: '#fff', fontSize: 12, fontWeight: '700' },
  mapStopInfo:      { flex: 1 },
  mapStopName:      { color: '#fff', fontSize: 14, fontWeight: '600' },
  mapStopTime:      { color: 'rgba(255,255,255,0.3)', fontSize: 12, marginTop: 2 },
  noCoords:         { flex: 1, alignItems: 'center', justifyContent: 'center' },
  noCoordsText:     { color: 'rgba(255,255,255,0.3)', fontSize: 14 },

  // Feedback
  feedbackRow:    { flexDirection: 'row', gap: 6, marginLeft: 'auto' },
  feedbackBtn:    { fontSize: 15, opacity: 0.25, paddingHorizontal: 4 },
  feedbackUp:     { opacity: 1 },
  feedbackDown:   { opacity: 1 },
  feedbackDimmed: { opacity: 0.08 },

  // Toast
  toast: {
    position: 'absolute', bottom: 32, alignSelf: 'center',
    backgroundColor: 'rgba(255,255,255,0.08)', borderRadius: 20,
    paddingHorizontal: 16, paddingVertical: 10,
    borderWidth: 1, borderColor: 'rgba(255,255,255,0.1)',
    flexDirection: 'row', alignItems: 'center', gap: 8,
  },
  toastDot:  { width: 6, height: 6, borderRadius: 3, backgroundColor: '#10B981' },
  toastText: { color: '#fff', fontSize: 13, fontWeight: '500' },

  // Explainability
  explainBox:   { marginTop: 12, backgroundColor: 'rgba(255,255,255,0.03)', borderRadius: 10, padding: 12, borderWidth: 1, borderColor: 'rgba(255,255,255,0.06)' },
  explainTitle: { color: 'rgba(255,255,255,0.4)', fontSize: 11, fontWeight: '700', textTransform: 'uppercase', marginBottom: 10, letterSpacing: 0.5 },
  contribRow:   { flexDirection: 'row', alignItems: 'center', marginBottom: 8 },
  contribName:  { color: 'rgba(255,255,255,0.45)', fontSize: 11, width: 90 },
  contribBarBg: { flex: 1, height: 4, backgroundColor: 'rgba(255,255,255,0.06)', borderRadius: 2, marginHorizontal: 8 },
  contribBar:   { height: 4, borderRadius: 2 },
  contribValue: { color: 'rgba(255,255,255,0.3)', fontSize: 10, width: 42, textAlign: 'right' },
});
