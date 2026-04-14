import { useRef, useEffect, useState } from 'react';
import {
  View, Text, ScrollView, TouchableOpacity, StyleSheet,
  SafeAreaView, Image, Animated, Platform, Linking,
} from 'react-native';
import { useRouter } from 'expo-router';
import { getSelectedStop, submitFeedback } from '../services/api';

const CATEGORY_COLORS: Record<string, string> = {
  food: '#F59E0B', nature: '#10B981', history: '#8B5CF6', culture: '#3B82F6',
  nightlife: '#EC4899', shopping: '#F97316', adventure: '#EF4444', attraction: '#6B7280',
};

const DAY_ABBR: Record<string, string> = {
  Mo: 'Mon', Tu: 'Tue', We: 'Wed', Th: 'Thu', Fr: 'Fri', Sa: 'Sat', Su: 'Sun',
};

function fmtTime(t: string): string {
  const [h, m] = t.split(':').map(Number);
  const ampm = h < 12 ? 'am' : 'pm';
  const h12 = h === 0 ? 12 : h > 12 ? h - 12 : h;
  return m === 0 ? `${h12}${ampm}` : `${h12}:${m.toString().padStart(2, '0')}${ampm}`;
}

function formatOpeningHours(oh: string): string {
  if (!oh) return '';
  if (oh.trim().toLowerCase() === '24/7') return '24/7';
  const rules = oh.split(';').map(r => r.trim()).filter(Boolean).slice(0, 3);
  return rules.map(rule => {
    const m = rule.match(/^([A-Za-z]{2}(?:[-,][A-Za-z]{2})*)\s+(.+)$/);
    const [dayPart, timePart] = m ? [m[1], m[2].trim()] : ['', rule.trim()];
    const days = dayPart.replace(/Mo|Tu|We|Th|Fr|Sa|Su/g, k => DAY_ABBR[k] ?? k).replace(/-/g, '–');
    if (timePart.toLowerCase() === 'off') return days ? `${days}: closed` : 'closed';
    const tm = timePart.match(/^(\d{1,2}:\d{2})-(\d{1,2}:\d{2})$/);
    if (tm) {
      const range = `${fmtTime(tm[1])}–${fmtTime(tm[2])}`;
      return days ? `${days} ${range}` : range;
    }
    return days ? `${days} ${timePart}` : timePart;
  }).join('\n');
}

function openInMaps(lat: number, lon: number, name: string) {
  const encoded = encodeURIComponent(name);
  const url = Platform.select({
    ios:     `maps://maps.apple.com/?daddr=${lat},${lon}&q=${encoded}`,
    android: `geo:${lat},${lon}?q=${encoded}`,
    default: `https://www.google.com/maps/dir/?api=1&destination=${lat},${lon}`,
  })!;
  Linking.canOpenURL(url).then(can => {
    Linking.openURL(can ? url : `https://www.google.com/maps/dir/?api=1&destination=${lat},${lon}`);
  });
}

export default function StopDetailScreen() {
  const router = useRouter();
  const selected = getSelectedStop();
  const fadeAnim  = useRef(new Animated.Value(0)).current;
  const slideAnim = useRef(new Animated.Value(20)).current;
  const [feedback, setFeedback] = useState<'up' | 'down' | null>(null);

  useEffect(() => {
    Animated.parallel([
      Animated.timing(fadeAnim,  { toValue: 1, duration: 240, useNativeDriver: true }),
      Animated.timing(slideAnim, { toValue: 0, duration: 280, useNativeDriver: true }),
    ]).start();
  }, []);

  if (!selected) {
    return (
      <SafeAreaView style={styles.container}>
        <View style={styles.topBar}>
          <TouchableOpacity onPress={() => router.back()} style={styles.backBtn}>
            <Text style={styles.backChevron}>‹</Text>
            <Text style={styles.backLabel}>Back</Text>
          </TouchableOpacity>
        </View>
        <View style={styles.empty}>
          <Text style={styles.emptyText}>No stop selected.</Text>
        </View>
      </SafeAreaView>
    );
  }

  const { stop, goals, dayColor } = selected;
  const catColor = CATEGORY_COLORS[stop.category] ?? '#6B7280';
  const hours = stop.opening_hours ? formatOpeningHours(stop.opening_hours) : null;

  const handleFeedback = async (relevant: boolean) => {
    if (feedback) return;
    setFeedback(relevant ? 'up' : 'down');
    try { await submitFeedback(stop.id || 0, relevant, stop.name, stop.category, goals); } catch {}
  };

  return (
    <SafeAreaView style={styles.container}>
      {/* Top bar */}
      <View style={styles.topBar}>
        <TouchableOpacity onPress={() => router.back()} style={styles.backBtn}>
          <Text style={styles.backChevron}>‹</Text>
          <Text style={styles.backLabel}>Back</Text>
        </TouchableOpacity>
        <View style={[styles.topBarAccent, { backgroundColor: dayColor }]} />
      </View>

      <Animated.ScrollView
        style={{ opacity: fadeAnim, transform: [{ translateY: slideAnim }] }}
        contentContainerStyle={styles.scroll}
        showsVerticalScrollIndicator={false}
      >
        {/* Hero photo or color band */}
        {stop.photo_url ? (
          <View style={styles.photoContainer}>
            <Image source={{ uri: stop.photo_url }} style={styles.photo} resizeMode="cover" />
            <View style={styles.photoOverlay} />
          </View>
        ) : (
          <View style={[styles.colorBand, { backgroundColor: catColor + '18' }]}>
            <View style={[styles.colorBandDot, { backgroundColor: catColor }]} />
          </View>
        )}

        <View style={styles.body}>
          {/* Name + meta */}
          <Text style={styles.stopName}>{stop.name}</Text>

          <View style={styles.metaRow}>
            <View style={styles.timeBadge}>
              <Text style={styles.timeBadgeText}>{stop.arrival_time}</Text>
            </View>
            <View style={styles.durationBadge}>
              <Text style={styles.durationBadgeText}>{stop.duration_min} min</Text>
            </View>
            <View style={[styles.categoryBadge, { backgroundColor: catColor + '15', borderColor: catColor + '35' }]}>
              <Text style={[styles.categoryBadgeText, { color: catColor }]}>{stop.category.toUpperCase()}</Text>
            </View>
          </View>

          {/* Description */}
          {stop.description ? (
            <View style={styles.section}>
              <Text style={styles.sectionLabel}>About</Text>
              <Text style={styles.description}>{stop.description}</Text>
            </View>
          ) : null}

          {/* Opening hours */}
          {hours ? (
            <View style={styles.section}>
              <Text style={styles.sectionLabel}>Hours</Text>
              <View style={styles.hoursCard}>
                <Text style={styles.hoursText}>{hours}</Text>
              </View>
            </View>
          ) : null}

          {/* Tip */}
          {stop.tip ? (
            <View style={[styles.tipCard, { borderLeftColor: dayColor }]}>
              <Text style={styles.tipLabel}>Tip</Text>
              <Text style={styles.tipText}>{stop.tip}</Text>
            </View>
          ) : null}

          {/* Navigate */}
          {(stop.lat && stop.lon) ? (
            <TouchableOpacity
              style={[styles.navBtn, { borderColor: dayColor + '40' }]}
              onPress={() => openInMaps(stop.lat, stop.lon, stop.name)}
              activeOpacity={0.75}
            >
              <Text style={[styles.navBtnText, { color: dayColor }]}>Get directions</Text>
              <Text style={[styles.navBtnArrow, { color: dayColor }]}>↗</Text>
            </TouchableOpacity>
          ) : null}

          {/* Feedback */}
          <View style={styles.feedbackSection}>
            <Text style={styles.feedbackPrompt}>Was this a good match?</Text>
            <View style={styles.feedbackBtns}>
              <TouchableOpacity
                style={[styles.feedbackBtn, feedback === 'up' && styles.feedbackBtnActive]}
                onPress={() => handleFeedback(true)}
                disabled={!!feedback}
                activeOpacity={0.7}
              >
                <Text style={[styles.feedbackBtnText, feedback === 'up' && styles.feedbackBtnTextActive]}>
                  Yes, great pick
                </Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[styles.feedbackBtn, feedback === 'down' && styles.feedbackBtnActiveRed]}
                onPress={() => handleFeedback(false)}
                disabled={!!feedback}
                activeOpacity={0.7}
              >
                <Text style={[styles.feedbackBtnText, feedback === 'down' && styles.feedbackBtnTextActiveRed]}>
                  Not my thing
                </Text>
              </TouchableOpacity>
            </View>
            {feedback && (
              <Text style={styles.feedbackThanks}>Thanks — we'll learn from this.</Text>
            )}
          </View>
        </View>
      </Animated.ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#09090b' },

  topBar: {
    flexDirection: 'row', alignItems: 'center',
    paddingHorizontal: 20, paddingVertical: 14,
    borderBottomWidth: 1, borderBottomColor: 'rgba(255,255,255,0.06)',
  },
  backBtn:     { flexDirection: 'row', alignItems: 'center', gap: 2 },
  backChevron: { color: 'rgba(255,255,255,0.4)', fontSize: 22, fontWeight: '300', lineHeight: 26 },
  backLabel:   { color: 'rgba(255,255,255,0.4)', fontSize: 14 },
  topBarAccent: { position: 'absolute', bottom: 0, left: 0, right: 0, height: 2, opacity: 0.6 },

  empty:     { flex: 1, alignItems: 'center', justifyContent: 'center' },
  emptyText: { color: 'rgba(255,255,255,0.3)', fontSize: 14 },

  scroll: { paddingBottom: 60 },

  // ── Photo / color band ──
  photoContainer: { position: 'relative' },
  photo:          { width: '100%', height: 220 },
  photoOverlay: {
    position: 'absolute', bottom: 0, left: 0, right: 0, height: 80,
    backgroundColor: 'transparent',
  },
  colorBand: {
    height: 100, alignItems: 'center', justifyContent: 'center',
  },
  colorBandDot: { width: 20, height: 20, borderRadius: 10, opacity: 0.6 },

  // ── Body ──
  body: { padding: 24, gap: 24 },

  stopName: { color: '#fff', fontSize: 26, fontWeight: '800', letterSpacing: -0.5, lineHeight: 32 },

  metaRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: -8 },
  timeBadge: {
    paddingHorizontal: 12, paddingVertical: 5, borderRadius: 100,
    backgroundColor: 'rgba(255,255,255,0.08)', borderWidth: 1, borderColor: 'rgba(255,255,255,0.12)',
  },
  timeBadgeText: { color: '#fff', fontSize: 13, fontWeight: '600' },
  durationBadge: {
    paddingHorizontal: 12, paddingVertical: 5, borderRadius: 100,
    backgroundColor: 'rgba(255,255,255,0.05)', borderWidth: 1, borderColor: 'rgba(255,255,255,0.08)',
  },
  durationBadgeText: { color: 'rgba(255,255,255,0.5)', fontSize: 13 },
  categoryBadge: {
    paddingHorizontal: 10, paddingVertical: 4, borderRadius: 100, borderWidth: 1,
  },
  categoryBadgeText: { fontSize: 10, fontWeight: '700', letterSpacing: 1 },

  // ── Sections ──
  section:      { gap: 8 },
  sectionLabel: { fontSize: 11, color: 'rgba(255,255,255,0.3)', textTransform: 'uppercase', letterSpacing: 1.2 },
  description:  { color: 'rgba(255,255,255,0.7)', fontSize: 15, lineHeight: 24 },

  hoursCard: {
    backgroundColor: 'rgba(255,255,255,0.04)', borderRadius: 10,
    borderWidth: 1, borderColor: 'rgba(255,255,255,0.07)',
    padding: 14,
  },
  hoursText: { color: 'rgba(255,255,255,0.55)', fontSize: 13, lineHeight: 20 },

  tipCard: {
    backgroundColor: 'rgba(255,255,255,0.04)', borderRadius: 10,
    borderWidth: 1, borderColor: 'rgba(255,255,255,0.07)',
    borderLeftWidth: 3, padding: 14, gap: 4,
  },
  tipLabel: { color: 'rgba(255,255,255,0.35)', fontSize: 11, textTransform: 'uppercase', letterSpacing: 1 },
  tipText:  { color: 'rgba(255,255,255,0.65)', fontSize: 14, lineHeight: 21 },

  // ── Navigate ──
  navBtn: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8,
    paddingVertical: 15, borderRadius: 12,
    backgroundColor: 'rgba(255,255,255,0.04)',
    borderWidth: 1,
  },
  navBtnText:  { fontSize: 15, fontWeight: '600' },
  navBtnArrow: { fontSize: 16, fontWeight: '600' },

  // ── Feedback ──
  feedbackSection: { gap: 12, paddingTop: 8 },
  feedbackPrompt: { color: 'rgba(255,255,255,0.35)', fontSize: 13, textAlign: 'center' },
  feedbackBtns: { flexDirection: 'row', gap: 10 },
  feedbackBtn: {
    flex: 1, paddingVertical: 12, borderRadius: 10, alignItems: 'center',
    backgroundColor: 'rgba(255,255,255,0.05)',
    borderWidth: 1, borderColor: 'rgba(255,255,255,0.08)',
  },
  feedbackBtnActive:     { backgroundColor: 'rgba(16,185,129,0.12)', borderColor: 'rgba(16,185,129,0.35)' },
  feedbackBtnActiveRed:  { backgroundColor: 'rgba(239,68,68,0.10)', borderColor: 'rgba(239,68,68,0.30)' },
  feedbackBtnText:       { color: 'rgba(255,255,255,0.45)', fontSize: 13 },
  feedbackBtnTextActive: { color: '#10B981', fontWeight: '600' },
  feedbackBtnTextActiveRed: { color: '#EF4444', fontWeight: '600' },
  feedbackThanks: { color: 'rgba(255,255,255,0.25)', fontSize: 12, textAlign: 'center' },
});
