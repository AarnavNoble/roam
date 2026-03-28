import { useState } from 'react';
import {
  View, Text, TextInput, TouchableOpacity,
  ScrollView, StyleSheet, ActivityIndicator, Alert,
} from 'react-native';
import { useRouter } from 'expo-router';
import { generateItineraryStreaming, storeItinerary, TripRequest, PipelineProgress } from '../services/api';

const GOAL_OPTIONS = ['food', 'nature', 'history', 'culture', 'nightlife', 'shopping', 'adventure'];
const PACE_OPTIONS = ['relaxed', 'moderate', 'packed'] as const;
const BUDGET_OPTIONS = ['free', 'budget', 'mid', 'splurge'] as const;
const STYLE_OPTIONS = ['solo', 'couple', 'family', 'group'] as const;
const DIETARY_OPTIONS = ['none', 'vegetarian', 'vegan', 'halal', 'kosher'] as const;
const MOBILITY_OPTIONS = ['easy', 'moderate', 'active'] as const;
const FAMILIARITY_OPTIONS = ['first_time', 'returning'] as const;
const START_TIME_OPTIONS = ['morning', 'afternoon', 'evening'] as const;
const DURATION_OPTIONS = [2, 3, 4, 6, 8, 10] as const;

const MOBILITY_LABELS = { easy: 'Easy (short walks)', moderate: 'Moderate', active: 'Active (lots of walking)' };
const FAMILIARITY_LABELS = { first_time: 'First time here', returning: 'I\'ve been before' };
const DURATION_LABELS: Record<number, string> = { 2: '2h', 3: '3h', 4: '4h', 6: '6h', 8: '8h', 10: 'Full day' };

const PIPELINE_STEPS = [
  { key: 'geocoding',    label: 'Locating you' },
  { key: 'fetching_pois', label: 'Finding places' },
  { key: 'ranking',     label: 'ML Ranking' },
  { key: 'optimizing',  label: 'Building route' },
  { key: 'retrieving',  label: 'Local knowledge' },
  { key: 'generating',  label: 'Writing journey' },
];

export default function HomeScreen() {
  const router = useRouter();

  // Core
  const [city, setCity] = useState('');
  const [startLocation, setStartLocation] = useState('');
  const [goals, setGoals] = useState<string[]>([]);
  const [durationHours, setDurationHours] = useState<number>(6);

  // Preferences
  const [pace, setPace] = useState<TripRequest['pace']>('moderate');
  const [budget, setBudget] = useState<TripRequest['budget']>('mid');
  const [style, setStyle] = useState<TripRequest['style']>('solo');
  const [dietary, setDietary] = useState<TripRequest['dietary']>('none');
  const [mobility, setMobility] = useState<TripRequest['mobility']>('moderate');
  const [familiarity, setFamiliarity] = useState<TripRequest['familiarity']>('first_time');
  const [startTime, setStartTime] = useState<TripRequest['start_time']>('morning');
  const [notes, setNotes] = useState('');

  // UI state
  const [loading, setLoading] = useState(false);
  const [currentStep, setCurrentStep] = useState<string | null>(null);
  const [completedSteps, setCompletedSteps] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  const toggleGoal = (goal: string) => {
    setGoals(prev => prev.includes(goal) ? prev.filter(g => g !== goal) : [...prev, goal]);
  };

  const handleGenerate = async () => {
    if (!city.trim()) { setError('Enter a city or area'); return; }
    if (!startLocation.trim()) { setError('Enter where you\'re starting from'); return; }
    if (goals.length === 0) { setError('Pick at least one interest'); return; }

    setLoading(true);
    setCurrentStep(null);
    setCompletedSteps([]);
    setError(null);

    try {
      const itinerary = await generateItineraryStreaming(
        {
          city: city.trim(),
          start_location: startLocation.trim(),
          duration_hours: durationHours,
          goals,
          transport: 'walking',
          pace,
          budget,
          style,
          dietary,
          mobility,
          familiarity,
          start_time: startTime,
          notes: notes.trim(),
        },
        (progress: PipelineProgress) => {
          setCurrentStep(prev => {
            if (prev) setCompletedSteps(c => c.includes(prev) ? c : [...c, prev]);
            return progress.step;
          });
        },
      );
      storeItinerary(itinerary);
      router.push({ pathname: '/itinerary', params: { goals: JSON.stringify(goals) } });
    } catch (e: any) {
      const msg = e?.message || '';
      const clean = msg.includes('504') || msg.includes('Gateway') || msg.includes('mirrors failed')
        ? 'Location service timed out. Try a different area or fewer interests.'
        : msg.includes('No POIs') || msg.includes('No places')
        ? 'No places found there. Try a different starting point or broader interests.'
        : 'Something went wrong. Please try again.';
      setError(clean);
    } finally {
      setLoading(false);
      setCurrentStep(null);
      setCompletedSteps([]);
    }
  };

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={styles.title}>roam</Text>
      <Text style={styles.subtitle}>Your journey, crafted for you</Text>

      {/* ── Core inputs ── */}
      <Text style={styles.label}>What city or area?</Text>
      <TextInput
        style={styles.input}
        placeholder="Paris, Tokyo, New York..."
        placeholderTextColor="#555"
        value={city}
        onChangeText={setCity}
      />

      <Text style={styles.label}>Where are you starting from?</Text>
      <TextInput
        style={styles.input}
        placeholder="Montmartre, Shibuya station, Times Square..."
        placeholderTextColor="#555"
        value={startLocation}
        onChangeText={setStartLocation}
      />

      <Text style={styles.label}>What are you into?</Text>
      <View style={styles.row}>
        {GOAL_OPTIONS.map(g => (
          <TouchableOpacity
            key={g}
            style={[styles.chip, goals.includes(g) && styles.chipSelected]}
            onPress={() => toggleGoal(g)}
          >
            <Text style={[styles.chipText, goals.includes(g) && styles.chipTextSelected]}>{g}</Text>
          </TouchableOpacity>
        ))}
      </View>

      <Text style={styles.label}>How much time do you have?</Text>
      <View style={styles.row}>
        {DURATION_OPTIONS.map(d => (
          <TouchableOpacity
            key={d}
            style={[styles.chip, durationHours === d && styles.chipSelected]}
            onPress={() => setDurationHours(d)}
          >
            <Text style={[styles.chipText, durationHours === d && styles.chipTextSelected]}>
              {DURATION_LABELS[d]}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      <Text style={styles.label}>When are you starting?</Text>
      <View style={styles.row}>
        {START_TIME_OPTIONS.map(t => (
          <TouchableOpacity
            key={t}
            style={[styles.chip, startTime === t && styles.chipSelected]}
            onPress={() => setStartTime(t)}
          >
            <Text style={[styles.chipText, startTime === t && styles.chipTextSelected]}>{t}</Text>
          </TouchableOpacity>
        ))}
      </View>

      {/* ── Preferences ── */}
      <Text style={styles.sectionHeader}>Your preferences</Text>

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

      <Text style={styles.label}>Walking comfort</Text>
      <View style={styles.column}>
        {MOBILITY_OPTIONS.map(m => (
          <TouchableOpacity
            key={m}
            style={[styles.optionRow, mobility === m && styles.optionRowSelected]}
            onPress={() => setMobility(m)}
          >
            <Text style={[styles.chipText, mobility === m && styles.chipTextSelected]}>
              {MOBILITY_LABELS[m]}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      <Text style={styles.label}>Have you been here before?</Text>
      <View style={styles.row}>
        {FAMILIARITY_OPTIONS.map(f => (
          <TouchableOpacity
            key={f}
            style={[styles.chip, familiarity === f && styles.chipSelected]}
            onPress={() => setFamiliarity(f)}
          >
            <Text style={[styles.chipText, familiarity === f && styles.chipTextSelected]}>
              {FAMILIARITY_LABELS[f]}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      <Text style={styles.label}>Dietary preference</Text>
      <View style={styles.row}>
        {DIETARY_OPTIONS.map(d => (
          <TouchableOpacity
            key={d}
            style={[styles.chip, dietary === d && styles.chipSelected]}
            onPress={() => setDietary(d)}
          >
            <Text style={[styles.chipText, dietary === d && styles.chipTextSelected]}>{d}</Text>
          </TouchableOpacity>
        ))}
      </View>

      <Text style={styles.label}>Anything else?</Text>
      <TextInput
        style={[styles.input, styles.inputMultiline]}
        placeholder="I love street food, hate tourist traps, want hidden gems..."
        placeholderTextColor="#555"
        value={notes}
        onChangeText={setNotes}
        multiline
        numberOfLines={3}
      />

      {error && <Text style={styles.errorText}>{error}</Text>}

      <TouchableOpacity
        style={[styles.button, loading && styles.buttonDisabled]}
        onPress={loading ? undefined : handleGenerate}
        activeOpacity={loading ? 1 : 0.8}
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
          : <Text style={styles.buttonText}>Build My Journey</Text>
        }
      </TouchableOpacity>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0f0f0f' },
  content: { padding: 24, paddingTop: 72, paddingBottom: 60 },
  title: { fontSize: 36, fontWeight: '700', color: '#fff', letterSpacing: -1 },
  subtitle: { fontSize: 14, color: '#555', marginTop: 4, marginBottom: 40 },
  sectionHeader: {
    fontSize: 11, color: '#444', marginTop: 36, marginBottom: 4,
    textTransform: 'uppercase', letterSpacing: 1.5,
  },
  label: { fontSize: 13, color: '#888', marginBottom: 10, marginTop: 24, textTransform: 'uppercase', letterSpacing: 0.5 },
  input: {
    backgroundColor: '#1a1a1a', color: '#fff', borderRadius: 12,
    padding: 16, fontSize: 16, borderWidth: 1, borderColor: '#2a2a2a',
  },
  inputMultiline: { minHeight: 80, textAlignVertical: 'top' },
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  column: { gap: 8 },
  chip: {
    paddingHorizontal: 14, paddingVertical: 8, borderRadius: 20,
    backgroundColor: '#1a1a1a', borderWidth: 1, borderColor: '#2a2a2a',
  },
  chipSelected: { backgroundColor: '#fff', borderColor: '#fff' },
  chipText: { color: '#888', fontSize: 13 },
  chipTextSelected: { color: '#000', fontWeight: '600' },
  optionRow: {
    paddingHorizontal: 16, paddingVertical: 12, borderRadius: 12,
    backgroundColor: '#1a1a1a', borderWidth: 1, borderColor: '#2a2a2a',
  },
  optionRowSelected: { backgroundColor: '#fff', borderColor: '#fff' },
  button: {
    backgroundColor: '#fff', borderRadius: 14, padding: 18,
    alignItems: 'center', marginTop: 40, marginBottom: 40,
  },
  buttonDisabled: { opacity: 0.9, paddingVertical: 20 },
  buttonText: { color: '#000', fontSize: 16, fontWeight: '700' },
  errorText: { color: '#EF4444', fontSize: 13, textAlign: 'center', marginTop: 16, marginBottom: 4 },
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
