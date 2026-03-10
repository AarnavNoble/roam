import { useLocalSearchParams, useRouter } from 'expo-router';
import { useState } from 'react';
import {
  View, Text, ScrollView, TouchableOpacity,
  StyleSheet, SafeAreaView,
} from 'react-native';
import { Itinerary, Day, Stop } from '../services/api';

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

function StopCard({ stop }: { stop: Stop }) {
  const [expanded, setExpanded] = useState(false);
  const color = CATEGORY_COLORS[stop.category] || '#6B7280';

  return (
    <TouchableOpacity style={styles.stopCard} onPress={() => setExpanded(e => !e)} activeOpacity={0.8}>
      <View style={styles.stopHeader}>
        <View style={[styles.dot, { backgroundColor: color }]} />
        <View style={styles.stopMeta}>
          <Text style={styles.stopTime}>{stop.arrival_time}</Text>
          <Text style={styles.stopDuration}>{stop.duration_min} min</Text>
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

function DaySection({ day }: { day: Day }) {
  return (
    <View style={styles.daySection}>
      <View style={styles.dayHeader}>
        <Text style={styles.dayNumber}>Day {day.day}</Text>
        <Text style={styles.dayTheme}>{day.theme}</Text>
      </View>
      {day.stops.map((stop, i) => <StopCard key={i} stop={stop} />)}
      <Text style={styles.daySummary}>{day.summary}</Text>
    </View>
  );
}

export default function ItineraryScreen() {
  const { data } = useLocalSearchParams<{ data: string }>();
  const router = useRouter();
  const itinerary: Itinerary = JSON.parse(data);

  return (
    <SafeAreaView style={styles.container}>
      <View style={styles.topBar}>
        <TouchableOpacity onPress={() => router.back()}>
          <Text style={styles.back}>← Back</Text>
        </TouchableOpacity>
        <Text style={styles.screenTitle}>Your Trip</Text>
        <View style={{ width: 50 }} />
      </View>

      <ScrollView contentContainerStyle={styles.scroll}>
        <Text style={styles.overview}>{itinerary.overview}</Text>
        {itinerary.days.map(day => <DaySection key={day.day} day={day} />)}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0f0f0f' },
  topBar: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 20, paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: '#1a1a1a',
  },
  back: { color: '#888', fontSize: 14 },
  screenTitle: { color: '#fff', fontSize: 16, fontWeight: '600' },
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
});
