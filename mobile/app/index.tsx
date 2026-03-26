import { useState } from 'react';
import {
  View, Text, TextInput, TouchableOpacity,
  ScrollView, StyleSheet, ActivityIndicator, Alert,
} from 'react-native';
import { useRouter } from 'expo-router';
import { generateItineraryStreaming, storeItinerary, TripRequest, PipelineProgress } from '../services/api';

const TRANSPORT_OPTIONS = ['driving', 'walking', 'cycling', 'transit'] as const;
const GOAL_OPTIONS = ['food', 'nature', 'history', 'culture', 'nightlife', 'shopping', 'adventure'];
const PACE_OPTIONS = ['relaxed', 'moderate', 'packed'] as const;
const BUDGET_OPTIONS = ['free', 'budget', 'mid', 'splurge'] as const;
const STYLE_OPTIONS = ['solo', 'couple', 'family', 'group'] as const;

const PIPELINE_STEPS = [
  { key: 'geocoding', label: 'Geocoding' },
  { key: 'fetching_pois', label: 'Fetching POIs' },
  { key: 'ranking', label: 'ML Ranking' },
  { key: 'optimizing', label: 'Route Optimization' },
  { key: 'retrieving', label: 'RAG Retrieval' },
  { key: 'generating', label: 'LLM Synthesis' },
];

export default function HomeScreen() {
  const router = useRouter();
  const [destination, setDestination] = useState('');
  const [days, setDays] = useState('3');
  const [transport, setTransport] = useState<TripRequest['transport']>('walking');
  const [goals, setGoals] = useState<string[]>([]);
  const [pace, setPace] = useState<TripRequest['pace']>('moderate');
  const [budget, setBudget] = useState<TripRequest['budget']>('mid');
  const [style, setStyle] = useState<TripRequest['style']>('solo');
  const [notes, setNotes] = useState('');
  const [loading, setLoading] = useState(false);
  const [currentStep, setCurrentStep] = useState<string | null>(null);
  const [completedSteps, setCompletedSteps] = useState<string[]>([]);

  const toggleGoal = (goal: string) => {
    setGoals(prev =>
      prev.includes(goal) ? prev.filter(g => g !== goal) : [...prev, goal]
    );
  };

  const handleGenerate = async () => {
    if (!destination.trim()) return Alert.alert('Enter a destination');
    if (goals.length === 0) return Alert.alert('Select at least one interest');

    setLoading(true);
    setCurrentStep(null);
    setCompletedSteps([]);

    try {
      const itinerary = await generateItineraryStreaming(
        {
          destination: destination.trim(),
          days: parseInt(days),
          transport,
          goals,
          pace,
          budget,
          style,
          notes: notes.trim(),
        },
        (progress: PipelineProgress) => {
          setCompletedSteps(prev => {
            const newCompleted = [...prev];
            if (currentStep && !newCompleted.includes(currentStep)) {
              // Mark previous step as complete when new one starts
            }
            return newCompleted;
          });
          setCurrentStep(prev => {
            if (prev && !completedSteps.includes(prev)) {
              setCompletedSteps(c => [...c, prev]);
            }
            return progress.step;
          });
        },
      );
      storeItinerary(itinerary);
      router.push({ pathname: '/itinerary', params: { goals: JSON.stringify(goals) } });
    } catch (e: any) {
      const msg = e?.message || 'Something went wrong';
      const clean = msg.includes('504') || msg.includes('Gateway')
        ? 'Location service timed out. Try a smaller city or fewer interests.'
        : msg.includes('No POIs') ? 'No places found for that destination and interests.'
        : 'Something went wrong. Please try again.';
      Alert.alert('Error', clean);
    } finally {
      setLoading(false);
      setCurrentStep(null);
      setCompletedSteps([]);
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

      <Text style={styles.label}>Pace</Text>
      <View style={styles.row}>
        {PACE_OPTIONS.map(p => (
          <TouchableOpacity
            key={p}
            style={[styles.chip, pace === p && styles.chipSelected]}
            onPress={() => setPace(p)}
          >
            <Text style={[styles.chipText, pace === p && styles.chipTextSelected]}>{p}</Text>
          </TouchableOpacity>
        ))}
      </View>

      <Text style={styles.label}>Budget</Text>
      <View style={styles.row}>
        {BUDGET_OPTIONS.map(b => (
          <TouchableOpacity
            key={b}
            style={[styles.chip, budget === b && styles.chipSelected]}
            onPress={() => setBudget(b)}
          >
            <Text style={[styles.chipText, budget === b && styles.chipTextSelected]}>{b}</Text>
          </TouchableOpacity>
        ))}
      </View>

      <Text style={styles.label}>Traveling as</Text>
      <View style={styles.row}>
        {STYLE_OPTIONS.map(s => (
          <TouchableOpacity
            key={s}
            style={[styles.chip, style === s && styles.chipSelected]}
            onPress={() => setStyle(s)}
          >
            <Text style={[styles.chipText, style === s && styles.chipTextSelected]}>{s}</Text>
          </TouchableOpacity>
        ))}
      </View>

      <Text style={styles.label}>Anything else?</Text>
      <TextInput
        style={styles.input}
        placeholder="I love street food, hate museums..."
        placeholderTextColor="#666"
        value={notes}
        onChangeText={setNotes}
      />

      <TouchableOpacity
        style={[styles.button, loading && styles.buttonDisabled]}
        onPress={handleGenerate}
        disabled={loading}
      >
        {loading
          ? <View style={styles.progressContainer}>
              {PIPELINE_STEPS.map(step => {
                const isCompleted = completedSteps.includes(step.key);
                const isCurrent = currentStep === step.key;
                return (
                  <View key={step.key} style={styles.progressStep}>
                    <View style={[
                      styles.progressDot,
                      isCompleted && styles.progressDotDone,
                      isCurrent && styles.progressDotCurrent,
                    ]}>
                      {isCurrent && <ActivityIndicator size="small" color="#000" />}
                      {isCompleted && <Text style={styles.checkmark}>✓</Text>}
                    </View>
                    <Text style={[
                      styles.progressLabel,
                      (isCompleted || isCurrent) && styles.progressLabelActive,
                    ]}>{step.label}</Text>
                  </View>
                );
              })}
            </View>
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
    alignItems: 'center', marginTop: 40, marginBottom: 40,
  },
  buttonDisabled: { opacity: 0.9, paddingVertical: 20 },
  buttonText: { color: '#000', fontSize: 16, fontWeight: '700' },
  progressContainer: { width: '100%', gap: 8 },
  progressStep: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  progressDot: {
    width: 24, height: 24, borderRadius: 12,
    backgroundColor: '#e0e0e0', alignItems: 'center', justifyContent: 'center',
  },
  progressDotDone: { backgroundColor: '#10B981' },
  progressDotCurrent: { backgroundColor: '#3B82F6' },
  progressLabel: { color: '#999', fontSize: 13 },
  progressLabelActive: { color: '#000', fontWeight: '600' },
  checkmark: { color: '#fff', fontSize: 12, fontWeight: '700' },
});
