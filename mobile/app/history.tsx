import { useState, useCallback, useRef, useEffect } from 'react';
import { View, Text, ScrollView, TouchableOpacity, StyleSheet, SafeAreaView, Animated, RefreshControl } from 'react-native';
import { useRouter, useFocusEffect } from 'expo-router';
import { SavedTrip, getSavedTrips, deleteTrip, storeItinerary } from '../services/api';

const DAY_COLORS = ['#3B82F6', '#10B981', '#F59E0B', '#EC4899', '#8B5CF6', '#EF4444'];

function timeAgo(ts: number): string {
  const diff = Date.now() - ts;
  const mins  = Math.floor(diff / 60000);
  const hours = Math.floor(diff / 3600000);
  const days  = Math.floor(diff / 86400000);
  if (mins < 1)   return 'just now';
  if (mins < 60)  return `${mins}m ago`;
  if (hours < 24) return `${hours}h ago`;
  if (days < 7)   return `${days}d ago`;
  return new Date(ts).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export default function HistoryScreen() {
  const router = useRouter();
  const [trips, setTrips] = useState<SavedTrip[]>([]);
  const [refreshing, setRefreshing] = useState(false);
  const fadeAnim = useRef(new Animated.Value(0)).current;
  const slideAnim = useRef(new Animated.Value(16)).current;

  useEffect(() => {
    Animated.parallel([
      Animated.timing(fadeAnim,  { toValue: 1, duration: 260, useNativeDriver: true }),
      Animated.timing(slideAnim, { toValue: 0, duration: 280, useNativeDriver: true }),
    ]).start();
  }, []);

  useFocusEffect(useCallback(() => {
    getSavedTrips().then(setTrips);
  }, []));

  const handleOpen = (trip: SavedTrip) => {
    storeItinerary(trip.itinerary);
    router.push({ pathname: '/itinerary', params: { goals: JSON.stringify(trip.goals), city: trip.city, ...(trip.tripDate ? { tripDate: trip.tripDate } : {}) } });
  };

  const handleRefresh = async () => {
    setRefreshing(true);
    const updated = await getSavedTrips();
    setTrips(updated);
    setRefreshing(false);
  };

  const handleDelete = async (id: string) => {
    await deleteTrip(id);
    setTrips(prev => prev.filter(t => t.id !== id));
  };

  return (
    <SafeAreaView style={styles.container}>
      <View style={styles.topBar}>
        <TouchableOpacity onPress={() => router.back()} style={styles.backBtn}>
          <Text style={styles.backChevron}>‹</Text>
          <Text style={styles.backLabel}>Back</Text>
        </TouchableOpacity>
        <Text style={styles.title}>Saved Trips</Text>
        <View style={{ width: 56 }} />
      </View>

      <Animated.View style={{ flex: 1, opacity: fadeAnim, transform: [{ translateY: slideAnim }] }}>
      {trips.length === 0 ? (
        <View style={styles.empty}>
          {/* Geometric illustration — three stacked route dots */}
          <View style={styles.emptyIllustration}>
            <View style={styles.emptyGlow} />
            <View style={[styles.emptyRing, { width: 80, height: 80, borderRadius: 40, borderColor: 'rgba(59,130,246,0.15)' }]} />
            <View style={[styles.emptyRing, { width: 48, height: 48, borderRadius: 24, borderColor: 'rgba(59,130,246,0.3)' }]} />
            <View style={styles.emptyDot} />
          </View>
          <Text style={styles.emptyTitle}>No saved trips yet</Text>
          <Text style={styles.emptySubtitle}>Generate a trip and it'll appear here</Text>
        </View>
      ) : (
        <ScrollView
          contentContainerStyle={styles.list}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={handleRefresh}
              tintColor="rgba(255,255,255,0.3)"
            />
          }
        >
          {trips.map((trip, i) => {
            const color = DAY_COLORS[i % DAY_COLORS.length];
            const stopCount = trip.itinerary.days.reduce((s, d) => s + d.stops.length, 0);
            return (
              <TouchableOpacity key={trip.id} style={styles.card} onPress={() => handleOpen(trip)} activeOpacity={0.7}>
                {/* Color accent bar */}
                <View style={[styles.cardBar, { backgroundColor: color }]} />

                <View style={styles.cardBody}>
                  <View style={styles.cardMain}>
                    <Text style={styles.cardCity}>{trip.city}</Text>
                    <Text style={styles.cardMeta}>
                      {trip.itinerary.days.length} {trip.itinerary.days.length === 1 ? 'day' : 'days'}  ·  {stopCount} stops  ·  {timeAgo(trip.savedAt)}
                    </Text>
                    {trip.tripDate ? (
                      <Text style={styles.cardDate}>
                        {new Date(trip.tripDate + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}
                      </Text>
                    ) : null}
                    <View style={styles.goalRow}>
                      {trip.goals.slice(0, 4).map(g => (
                        <View key={g} style={styles.goalPill}>
                          <Text style={styles.goalPillText}>{g}</Text>
                        </View>
                      ))}
                      {trip.goals.length > 4 && (
                        <Text style={styles.goalMore}>+{trip.goals.length - 4}</Text>
                      )}
                    </View>
                  </View>

                  <TouchableOpacity
                    style={styles.deleteBtn}
                    onPress={() => handleDelete(trip.id)}
                    hitSlop={{ top: 12, bottom: 12, left: 12, right: 12 }}
                  >
                    <Text style={styles.deleteIcon}>×</Text>
                  </TouchableOpacity>
                </View>
              </TouchableOpacity>
            );
          })}
        </ScrollView>
      )}
      </Animated.View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#09090b' },

  topBar: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 20, paddingVertical: 14,
    borderBottomWidth: 1, borderBottomColor: 'rgba(255,255,255,0.06)',
  },
  backBtn:     { flexDirection: 'row', alignItems: 'center', gap: 2, width: 56 },
  backChevron: { color: 'rgba(255,255,255,0.4)', fontSize: 22, fontWeight: '300', lineHeight: 26 },
  backLabel:   { color: 'rgba(255,255,255,0.4)', fontSize: 14 },
  title:       { color: '#fff', fontSize: 16, fontWeight: '700', letterSpacing: 0.3 },

  empty: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 12 },
  emptyIllustration: { width: 100, height: 100, alignItems: 'center', justifyContent: 'center', marginBottom: 8 },
  emptyGlow:    { position: 'absolute', width: 100, height: 100, borderRadius: 50, backgroundColor: '#3B82F6', opacity: 0.07 },
  emptyRing:    { position: 'absolute', borderWidth: 1 },
  emptyDot:     { width: 14, height: 14, borderRadius: 7, backgroundColor: '#3B82F6' },
  emptyTitle:    { color: '#fff', fontSize: 18, fontWeight: '600' },
  emptySubtitle: { color: 'rgba(255,255,255,0.4)', fontSize: 14 },

  list: { padding: 20, gap: 12 },

  card: {
    backgroundColor: '#111113',
    borderRadius: 16,
    borderWidth: 1, borderColor: 'rgba(255,255,255,0.07)',
    borderTopColor: 'rgba(255,255,255,0.10)',
    overflow: 'hidden',
  },
  cardBar:  { height: 3, width: '100%' },
  cardBody: { flexDirection: 'row', alignItems: 'center', padding: 16 },
  cardMain: { flex: 1, gap: 6 },
  cardCity: { color: '#fff', fontSize: 18, fontWeight: '700', letterSpacing: -0.3 },
  cardMeta: { color: 'rgba(255,255,255,0.35)', fontSize: 13 },
  cardDate: { color: 'rgba(255,255,255,0.22)', fontSize: 11, marginTop: -2 },

  goalRow:      { flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginTop: 2 },
  goalPill:     { backgroundColor: 'rgba(255,255,255,0.07)', borderRadius: 100, paddingHorizontal: 10, paddingVertical: 3 },
  goalPillText: { color: 'rgba(255,255,255,0.5)', fontSize: 11 },
  goalMore:     { color: 'rgba(255,255,255,0.3)', fontSize: 11, alignSelf: 'center' },

  deleteBtn:  { padding: 4 },
  deleteIcon: { color: 'rgba(255,255,255,0.2)', fontSize: 22, fontWeight: '300', lineHeight: 26 },
});
