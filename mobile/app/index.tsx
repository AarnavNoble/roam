import { useState } from 'react';
import {
  View, Text, TextInput, TouchableOpacity,
  ScrollView, StyleSheet, ActivityIndicator, Alert,
} from 'react-native';
import { useRouter } from 'expo-router';
import { generateItinerary, TripRequest } from '../services/api';

const TRANSPORT_OPTIONS = ['driving', 'walking', 'cycling', 'transit'] as const;
const GOAL_OPTIONS = ['food', 'nature', 'history', 'culture', 'nightlife', 'shopping', 'adventure'];

export default function HomeScreen() {
  const router = useRouter();
  const [destination, setDestination] = useState('');
  const [days, setDays] = useState('3');
  const [transport, setTransport] = useState<TripRequest['transport']>('walking');
  const [goals, setGoals] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);

  const toggleGoal = (goal: string) => {
    setGoals(prev =>
      prev.includes(goal) ? prev.filter(g => g !== goal) : [...prev, goal]
    );
  };

  const handleGenerate = async () => {
    if (!destination.trim()) return Alert.alert('Enter a destination');
    if (goals.length === 0) return Alert.alert('Select at least one interest');

    setLoading(true);
    try {
      const itinerary = await generateItinerary({
        destination: destination.trim(),
        days: parseInt(days),
        transport,
        goals,
      });
      router.push({ pathname: '/itinerary', params: { data: JSON.stringify(itinerary), goals: JSON.stringify(goals) } });
    } catch (e: any) {
      Alert.alert('Error', e?.response?.data?.detail || 'Something went wrong');
    } finally {
      setLoading(false);
    }
  };

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={styles.title}>roam</Text>
      <Text style={styles.subtitle}>AI-optimized travel itineraries</Text>

      <Text style={styles.label}>Where to?</Text>
      <TextInput
        style={styles.input}
        placeholder="Tokyo, Paris, New York..."
        placeholderTextColor="#666"
        value={destination}
        onChangeText={setDestination}
      />

      <Text style={styles.label}>How many days?</Text>
      <TextInput
        style={styles.input}
        keyboardType="number-pad"
        value={days}
        onChangeText={setDays}
      />

      <Text style={styles.label}>Getting around</Text>
      <View style={styles.row}>
        {TRANSPORT_OPTIONS.map(t => (
          <TouchableOpacity
            key={t}
            style={[styles.chip, transport === t && styles.chipSelected]}
            onPress={() => setTransport(t)}
          >
            <Text style={[styles.chipText, transport === t && styles.chipTextSelected]}>
              {t}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      <Text style={styles.label}>What are you into?</Text>
      <View style={styles.row}>
        {GOAL_OPTIONS.map(g => (
          <TouchableOpacity
            key={g}
            style={[styles.chip, goals.includes(g) && styles.chipSelected]}
            onPress={() => toggleGoal(g)}
          >
            <Text style={[styles.chipText, goals.includes(g) && styles.chipTextSelected]}>
              {g}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      <TouchableOpacity
        style={[styles.button, loading && styles.buttonDisabled]}
        onPress={handleGenerate}
        disabled={loading}
      >
        {loading
          ? <ActivityIndicator color="#fff" />
          : <Text style={styles.buttonText}>Generate Itinerary</Text>
        }
      </TouchableOpacity>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0f0f0f' },
  content: { padding: 24, paddingTop: 72 },
  title: { fontSize: 36, fontWeight: '700', color: '#fff', letterSpacing: -1 },
  subtitle: { fontSize: 14, color: '#666', marginTop: 4, marginBottom: 40 },
  label: { fontSize: 13, color: '#888', marginBottom: 10, marginTop: 24, textTransform: 'uppercase', letterSpacing: 0.5 },
  input: {
    backgroundColor: '#1a1a1a', color: '#fff', borderRadius: 12,
    padding: 16, fontSize: 16, borderWidth: 1, borderColor: '#2a2a2a',
  },
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  chip: {
    paddingHorizontal: 14, paddingVertical: 8, borderRadius: 20,
    backgroundColor: '#1a1a1a', borderWidth: 1, borderColor: '#2a2a2a',
  },
  chipSelected: { backgroundColor: '#fff', borderColor: '#fff' },
  chipText: { color: '#888', fontSize: 13 },
  chipTextSelected: { color: '#000', fontWeight: '600' },
  button: {
    backgroundColor: '#fff', borderRadius: 14, padding: 18,
    alignItems: 'center', marginTop: 40,
  },
  buttonDisabled: { opacity: 0.5 },
  buttonText: { color: '#000', fontSize: 16, fontWeight: '700' },
});
