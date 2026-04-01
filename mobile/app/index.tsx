import { useState, useRef, useEffect } from 'react';
import {
  View, Text, TextInput, TouchableOpacity,
  ScrollView, StyleSheet, ActivityIndicator,
  Animated, Platform, UIManager, LayoutAnimation,
} from 'react-native';
import { useRouter } from 'expo-router';
import { generateItineraryStreaming, storeItinerary, TripRequest, PipelineProgress } from '../services/api';

// Enable LayoutAnimation on Android
if (Platform.OS === 'android' && UIManager.setLayoutAnimationEnabledExperimental) {
  UIManager.setLayoutAnimationEnabledExperimental(true);
}

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

// Per-goal accent colors for the chip dots
const GOAL_COLORS: Record<string, string> = {
  food: '#F59E0B',
  nature: '#10B981',
  history: '#8B5CF6',
  culture: '#3B82F6',
  nightlife: '#EC4899',
  shopping: '#F97316',
  adventure: '#EF4444',
};

const PIPELINE_STEPS = [
  { key: 'geocoding',     label: 'Locating you' },
  { key: 'fetching_pois', label: 'Finding places' },
  { key: 'ranking',       label: 'ML Ranking' },
  { key: 'optimizing',    label: 'Building route' },
  { key: 'retrieving',    label: 'Local knowledge' },
  { key: 'generating',    label: 'Writing journey' },
];

const ALL_CHIP_KEYS = [
  ...GOAL_OPTIONS,
  ...DURATION_OPTIONS.map(String),
  ...START_TIME_OPTIONS,
  ...PACE_OPTIONS,
  ...BUDGET_OPTIONS,
  ...STYLE_OPTIONS,
  ...DIETARY_OPTIONS,
  ...MOBILITY_OPTIONS,
  ...FAMILIARITY_OPTIONS,
];

export default function HomeScreen() {
  const router = useRouter();

  const [city, setCity] = useState('');
  const [startLocation, setStartLocation] = useState('');
  const [goals, setGoals] = useState<string[]>([]);
  const [durationHours, setDurationHours] = useState<number>(6);
  const [pace, setPace] = useState<TripRequest['pace']>('moderate');
  const [budget, setBudget] = useState<TripRequest['budget']>('mid');
  const [style, setStyle] = useState<TripRequest['style']>('solo');
  const [dietary, setDietary] = useState<TripRequest['dietary']>('none');
  const [mobility, setMobility] = useState<TripRequest['mobility']>('moderate');
  const [familiarity, setFamiliarity] = useState<TripRequest['familiarity']>('first_time');
  const [startTime, setStartTime] = useState<TripRequest['start_time']>('morning');
  const [notes, setNotes] = useState('');
  const [loading, setLoading] = useState(false);
  const [currentStep, setCurrentStep] = useState<string | null>(null);
  const [completedSteps, setCompletedSteps] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  // ── Animation refs ───────────────────────────────────────────────────────────

  const heroTitleAnim    = useRef(new Animated.Value(0)).current;
  const heroSubtitleAnim = useRef(new Animated.Value(0)).current;
  const heroAccentAnim   = useRef(new Animated.Value(0)).current;

  const chipScales = useRef<Record<string, Animated.Value>>(
    Object.fromEntries(ALL_CHIP_KEYS.map(k => [k, new Animated.Value(1)]))
  ).current;

  const cityBorderAnim     = useRef(new Animated.Value(0)).current;
  const locationBorderAnim = useRef(new Animated.Value(0)).current;
  const notesBorderAnim    = useRef(new Animated.Value(0)).current;

  const buttonScaleAnim  = useRef(new Animated.Value(1)).current;
  const loadingPulseAnim = useRef(new Animated.Value(1)).current;

  const stepSlideAnims  = useRef(PIPELINE_STEPS.map(() => new Animated.Value(0))).current;
  const progressBarAnim = useRef(new Animated.Value(0)).current;

  // ── Effects ──────────────────────────────────────────────────────────────────

  useEffect(() => {
    Animated.stagger(80, [
      Animated.timing(heroTitleAnim,    { toValue: 1, duration: 600, useNativeDriver: true }),
      Animated.timing(heroSubtitleAnim, { toValue: 1, duration: 500, useNativeDriver: true }),
      Animated.timing(heroAccentAnim,   { toValue: 1, duration: 400, useNativeDriver: false }),
    ]).start();
  }, []);

  useEffect(() => {
    if (!loading) { loadingPulseAnim.setValue(1); return; }
    const pulse = Animated.loop(
      Animated.sequence([
        Animated.timing(loadingPulseAnim, { toValue: 0.75, duration: 900, useNativeDriver: true }),
        Animated.timing(loadingPulseAnim, { toValue: 1,    duration: 900, useNativeDriver: true }),
      ])
    );
    pulse.start();
    return () => pulse.stop();
  }, [loading]);

  useEffect(() => {
    if (!loading) {
      progressBarAnim.setValue(0);
      stepSlideAnims.forEach(a => a.setValue(0));
      return;
    }
    const stepIndex = PIPELINE_STEPS.findIndex(s => s.key === currentStep);
    if (stepIndex < 0) return;
    const targetProgress = (completedSteps.length + 1) / PIPELINE_STEPS.length;
    Animated.timing(progressBarAnim, { toValue: targetProgress, duration: 400, useNativeDriver: false }).start();
    Animated.timing(stepSlideAnims[stepIndex], { toValue: 1, duration: 250, useNativeDriver: true }).start();
  }, [currentStep, loading]);

  // ── Helpers ──────────────────────────────────────────────────────────────────

  const animateChip = (key: string) => {
    const anim = chipScales[key];
    if (!anim) return;
    Animated.sequence([
      Animated.spring(anim, { toValue: 0.90, useNativeDriver: true, speed: 30, bounciness: 0 }),
      Animated.spring(anim, { toValue: 1.0,  useNativeDriver: true, speed: 20, bounciness: 10 }),
    ]).start();
  };

  const selectChip = <T extends string | number>(key: string, setter: (v: T) => void, value: T) => {
    animateChip(key);
    setter(value);
  };

  const focusInput  = (anim: Animated.Value) =>
    Animated.timing(anim, { toValue: 1, duration: 200, useNativeDriver: false }).start();
  const blurInput   = (anim: Animated.Value) =>
    Animated.timing(anim, { toValue: 0, duration: 200, useNativeDriver: false }).start();
  const borderColor = (anim: Animated.Value) =>
    anim.interpolate({ inputRange: [0, 1], outputRange: ['#333', '#666'] });

  const toggleGoal = (goal: string) => {
    animateChip(goal);
    setGoals(prev => prev.includes(goal) ? prev.filter(g => g !== goal) : [...prev, goal]);
  };

  const onButtonPressIn  = () =>
    Animated.spring(buttonScaleAnim, { toValue: 0.97, useNativeDriver: true, speed: 30, bounciness: 0 }).start();
  const onButtonPressOut = () =>
    Animated.spring(buttonScaleAnim, { toValue: 1.0,  useNativeDriver: true, speed: 20, bounciness: 6 }).start();

  const handleGenerate = async () => {
    if (!city.trim())          { setError('Enter a city or area'); return; }
    if (!startLocation.trim()) { setError('Enter where you\'re starting from'); return; }
    if (goals.length === 0)    { setError('Pick at least one interest'); return; }

    setLoading(true);
    setCurrentStep(null);
    setCompletedSteps([]);
    setError(null);

    try {
      const itinerary = await generateItineraryStreaming(
        {
          city: city.trim(), start_location: startLocation.trim(),
          duration_hours: durationHours, goals, transport: 'walking',
          pace, budget, style, dietary, mobility, familiarity,
          start_time: startTime, notes: notes.trim(),
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

      {/* ── Hero ── */}
      <View style={styles.heroContainer}>
        {/* Decorative glow blobs */}
        <View style={styles.glow1} pointerEvents="none" />
        <View style={styles.glow2} pointerEvents="none" />

        <Animated.View style={{
          opacity: heroTitleAnim,
          transform: [{ translateY: heroTitleAnim.interpolate({ inputRange: [0, 1], outputRange: [20, 0] }) }],
        }}>
          <Text style={styles.title}>roam</Text>
          <Animated.View style={[
            styles.titleAccent,
            {
              width: heroAccentAnim.interpolate({ inputRange: [0, 1], outputRange: ['0%', '100%'] }),
            },
          ]} />
        </Animated.View>

        <Animated.View style={{
          opacity: heroSubtitleAnim,
          transform: [{ translateY: heroSubtitleAnim.interpolate({ inputRange: [0, 1], outputRange: [12, 0] }) }],
        }}>
          <Text style={styles.subtitle}>Your journey, crafted for you</Text>
        </Animated.View>
      </View>

      <View style={styles.divider} />

      {/* ── Core inputs ── */}
      <Text style={styles.label}>What city or area?</Text>
      <Animated.View style={[styles.inputWrapper, { borderColor: borderColor(cityBorderAnim) }]}>
        <TextInput
          style={styles.inputInner}
          placeholder="Paris, Tokyo, New York..."
          placeholderTextColor="#444"
          value={city}
          onChangeText={setCity}
          onFocus={() => focusInput(cityBorderAnim)}
          onBlur={() => blurInput(cityBorderAnim)}
        />
      </Animated.View>

      <Text style={styles.label}>Where are you starting from?</Text>
      <Animated.View style={[styles.inputWrapper, { borderColor: borderColor(locationBorderAnim) }]}>
        <TextInput
          style={styles.inputInner}
          placeholder="Montmartre, Shibuya station, Times Square..."
          placeholderTextColor="#444"
          value={startLocation}
          onChangeText={setStartLocation}
          onFocus={() => focusInput(locationBorderAnim)}
          onBlur={() => blurInput(locationBorderAnim)}
        />
      </Animated.View>

      {/* ── Goal chips with per-category color dots ── */}
      <Text style={styles.label}>What are you into?</Text>
      <View style={styles.row}>
        {GOAL_OPTIONS.map(g => {
          const selected = goals.includes(g);
          const accentColor = GOAL_COLORS[g];
          return (
            <Animated.View key={g} style={{ transform: [{ scale: chipScales[g] }] }}>
              <TouchableOpacity
                style={[
                  styles.chip,
                  selected && styles.chipSelected,
                  selected && { borderColor: accentColor },
                ]}
                onPress={() => toggleGoal(g)}
              >
                <View style={[
                  styles.goalDot,
                  { backgroundColor: selected ? accentColor : accentColor + '55' },
                ]} />
                <Text style={[styles.chipText, selected && { color: accentColor, fontWeight: '700' }]}>{g}</Text>
              </TouchableOpacity>
            </Animated.View>
          );
        })}
      </View>

      <Text style={styles.label}>How much time do you have?</Text>
      <View style={styles.row}>
        {DURATION_OPTIONS.map(d => (
          <Animated.View key={d} style={{ transform: [{ scale: chipScales[String(d)] }] }}>
            <TouchableOpacity
              style={[styles.chip, durationHours === d && styles.chipSelected]}
              onPress={() => selectChip(String(d), setDurationHours as (v: number) => void, d)}
            >
              <Text style={[styles.chipText, durationHours === d && styles.chipTextSelected]}>
                {DURATION_LABELS[d]}
              </Text>
            </TouchableOpacity>
          </Animated.View>
        ))}
      </View>

      <Text style={styles.label}>When are you starting?</Text>
      <View style={styles.row}>
        {START_TIME_OPTIONS.map(t => (
          <Animated.View key={t} style={{ transform: [{ scale: chipScales[t] }] }}>
            <TouchableOpacity
              style={[styles.chip, startTime === t && styles.chipSelected]}
              onPress={() => selectChip(t, setStartTime as (v: string) => void, t)}
            >
              <Text style={[styles.chipText, startTime === t && styles.chipTextSelected]}>{t}</Text>
            </TouchableOpacity>
          </Animated.View>
        ))}
      </View>

      {/* ── Preferences ── */}
      <View style={styles.divider} />
      <View style={styles.sectionHeaderRow}>
        <View style={styles.sectionHeaderAccent} />
        <Text style={styles.sectionHeader}>Your preferences</Text>
      </View>

      <Text style={styles.label}>Pace</Text>
      <View style={styles.row}>
        {PACE_OPTIONS.map(p => (
          <Animated.View key={p} style={{ transform: [{ scale: chipScales[p] }] }}>
            <TouchableOpacity
              style={[styles.chip, pace === p && styles.chipSelected]}
              onPress={() => selectChip(p, setPace as (v: string) => void, p)}
            >
              <Text style={[styles.chipText, pace === p && styles.chipTextSelected]}>{p}</Text>
            </TouchableOpacity>
          </Animated.View>
        ))}
      </View>

      <Text style={styles.label}>Budget</Text>
      <View style={styles.row}>
        {BUDGET_OPTIONS.map(b => (
          <Animated.View key={b} style={{ transform: [{ scale: chipScales[b] }] }}>
            <TouchableOpacity
              style={[styles.chip, budget === b && styles.chipSelected]}
              onPress={() => selectChip(b, setBudget as (v: string) => void, b)}
            >
              <Text style={[styles.chipText, budget === b && styles.chipTextSelected]}>{b}</Text>
            </TouchableOpacity>
          </Animated.View>
        ))}
      </View>

      <Text style={styles.label}>Traveling as</Text>
      <View style={styles.row}>
        {STYLE_OPTIONS.map(s => (
          <Animated.View key={s} style={{ transform: [{ scale: chipScales[s] }] }}>
            <TouchableOpacity
              style={[styles.chip, style === s && styles.chipSelected]}
              onPress={() => selectChip(s, setStyle as (v: string) => void, s)}
            >
              <Text style={[styles.chipText, style === s && styles.chipTextSelected]}>{s}</Text>
            </TouchableOpacity>
          </Animated.View>
        ))}
      </View>

      <Text style={styles.label}>Walking comfort</Text>
      <View style={styles.column}>
        {MOBILITY_OPTIONS.map(m => (
          <Animated.View key={m} style={{ transform: [{ scale: chipScales[m] }] }}>
            <TouchableOpacity
              style={[styles.optionRow, mobility === m && styles.optionRowSelected]}
              onPress={() => selectChip(m, setMobility as (v: string) => void, m)}
            >
              <Text style={[styles.chipText, mobility === m && styles.chipTextSelected]}>
                {MOBILITY_LABELS[m]}
              </Text>
            </TouchableOpacity>
          </Animated.View>
        ))}
      </View>

      <Text style={styles.label}>Have you been here before?</Text>
      <View style={styles.row}>
        {FAMILIARITY_OPTIONS.map(f => (
          <Animated.View key={f} style={{ transform: [{ scale: chipScales[f] }] }}>
            <TouchableOpacity
              style={[styles.chip, familiarity === f && styles.chipSelected]}
              onPress={() => selectChip(f, setFamiliarity as (v: string) => void, f)}
            >
              <Text style={[styles.chipText, familiarity === f && styles.chipTextSelected]}>
                {FAMILIARITY_LABELS[f]}
              </Text>
            </TouchableOpacity>
          </Animated.View>
        ))}
      </View>

      <Text style={styles.label}>Dietary preference</Text>
      <View style={styles.row}>
        {DIETARY_OPTIONS.map(d => (
          <Animated.View key={d} style={{ transform: [{ scale: chipScales[d] }] }}>
            <TouchableOpacity
              style={[styles.chip, dietary === d && styles.chipSelected]}
              onPress={() => selectChip(d, setDietary as (v: string) => void, d)}
            >
              <Text style={[styles.chipText, dietary === d && styles.chipTextSelected]}>{d}</Text>
            </TouchableOpacity>
          </Animated.View>
        ))}
      </View>

      <Text style={styles.label}>Anything else?</Text>
      <Animated.View style={[styles.inputWrapper, { borderColor: borderColor(notesBorderAnim) }]}>
        <TextInput
          style={[styles.inputInner, styles.inputMultiline]}
          placeholder="I love street food, hate tourist traps, want hidden gems..."
          placeholderTextColor="#444"
          value={notes}
          onChangeText={setNotes}
          onFocus={() => focusInput(notesBorderAnim)}
          onBlur={() => blurInput(notesBorderAnim)}
          multiline
          numberOfLines={3}
        />
      </Animated.View>

      {error && (
        <View style={styles.errorBox}>
          <Text style={styles.errorText}>{error}</Text>
        </View>
      )}

      {/* ── Generate button ── */}
      <Animated.View style={[
        { transform: [{ scale: buttonScaleAnim }] },
        loading && { opacity: loadingPulseAnim },
      ]}>
        <TouchableOpacity
          style={[styles.button, loading && styles.buttonDisabled]}
          onPress={loading ? undefined : handleGenerate}
          onPressIn={loading ? undefined : onButtonPressIn}
          onPressOut={loading ? undefined : onButtonPressOut}
          activeOpacity={1}
        >
          {loading ? (
            <View style={styles.progressContainer}>
              <View style={styles.progressBarTrack}>
                <Animated.View style={[
                  styles.progressBarFill,
                  {
                    width: progressBarAnim.interpolate({
                      inputRange: [0, 1],
                      outputRange: ['0%', '100%'],
                    }),
                  },
                ]} />
              </View>
              {PIPELINE_STEPS.map((step, index) => {
                const isCompleted = completedSteps.includes(step.key);
                const isCurrent   = currentStep === step.key;
                return (
                  <View key={step.key} style={styles.progressStep}>
                    <View style={[
                      styles.progressDot,
                      isCompleted && styles.progressDotDone,
                      isCurrent && styles.progressDotCurrent,
                    ]}>
                      {isCurrent   && <ActivityIndicator size="small" color="#fff" />}
                      {isCompleted && <Text style={styles.checkmark}>✓</Text>}
                    </View>
                    <Animated.Text style={[
                      styles.progressLabel,
                      (isCompleted || isCurrent) && styles.progressLabelActive,
                      {
                        opacity: isCompleted || isCurrent ? 1 : 0.4,
                        transform: [{
                          translateX: stepSlideAnims[index].interpolate({
                            inputRange: [0, 1],
                            outputRange: isCurrent ? [-12, 0] : [0, 0],
                          }),
                        }],
                      },
                    ]}>{step.label}</Animated.Text>
                  </View>
                );
              })}
            </View>
          ) : (
            <Text style={styles.buttonText}>Build My Journey</Text>
          )}
        </TouchableOpacity>
      </Animated.View>

    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0a0a0a' },
  content:   { padding: 24, paddingTop: 72, paddingBottom: 60 },

  // ── Hero ──
  heroContainer: {
    paddingBottom: 16,
    overflow: 'hidden',
    marginBottom: 4,
  },
  glow1: {
    position: 'absolute',
    top: -30, left: -50,
    width: 240, height: 240,
    borderRadius: 120,
    backgroundColor: '#3B82F6',
    opacity: 0.10,
  },
  glow2: {
    position: 'absolute',
    top: 20, right: -30,
    width: 160, height: 160,
    borderRadius: 80,
    backgroundColor: '#8B5CF6',
    opacity: 0.07,
  },
  title: {
    fontSize: 56,
    fontWeight: '800',
    color: '#fff',
    letterSpacing: -3,
  },
  titleAccent: {
    height: 3,
    width: 36,
    backgroundColor: '#3B82F6',
    borderRadius: 2,
    marginTop: 6,
    marginBottom: 10,
  },
  subtitle: {
    fontSize: 15,
    color: '#666',
    letterSpacing: 0.2,
  },

  // ── Layout ──
  divider: { height: 1, backgroundColor: '#1e1e1e', marginVertical: 28 },
  sectionHeaderRow: {
    flexDirection: 'row', alignItems: 'center', gap: 10,
    marginBottom: 4,
  },
  sectionHeaderAccent: {
    width: 18, height: 1.5,
    backgroundColor: '#3B82F6', borderRadius: 1, opacity: 0.8,
  },
  sectionHeader: {
    fontSize: 11, color: '#555',
    textTransform: 'uppercase', letterSpacing: 1.5,
  },
  label: {
    fontSize: 12, color: '#666',
    marginBottom: 10, marginTop: 24,
    textTransform: 'uppercase', letterSpacing: 0.8,
  },

  // ── Inputs ──
  inputWrapper: {
    backgroundColor: '#141414',
    borderRadius: 12,
    borderWidth: 1,
  },
  inputInner: {
    color: '#fff', padding: 16, fontSize: 16,
  },
  inputMultiline: { minHeight: 80, textAlignVertical: 'top' },

  // ── Chips ──
  row:    { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  column: { gap: 8 },
  chip: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    paddingHorizontal: 14, paddingVertical: 9,
    borderRadius: 20,
    backgroundColor: '#141414', borderWidth: 1, borderColor: '#2e2e2e',
  },
  chipSelected: { backgroundColor: '#1a1a1a', borderColor: '#fff' },
  chipText:         { color: '#777', fontSize: 13 },
  chipTextSelected: { color: '#fff', fontWeight: '600' },
  goalDot: { width: 7, height: 7, borderRadius: 3.5 },
  optionRow: {
    paddingHorizontal: 16, paddingVertical: 13, borderRadius: 12,
    backgroundColor: '#141414', borderWidth: 1, borderColor: '#2e2e2e',
  },
  optionRowSelected: { backgroundColor: '#1a1a1a', borderColor: '#fff' },

  // ── Error ──
  errorBox: {
    backgroundColor: '#1a0a0a', borderRadius: 10,
    borderWidth: 1, borderColor: '#3a1a1a',
    padding: 12, marginTop: 16,
  },
  errorText: { color: '#EF4444', fontSize: 13, textAlign: 'center' },

  // ── Button ──
  button: {
    backgroundColor: '#fff', borderRadius: 16, padding: 20,
    alignItems: 'center', marginTop: 40, marginBottom: 40,
    ...(Platform.OS === 'ios' ? {
      shadowColor: '#fff',
      shadowOffset: { width: 0, height: 0 },
      shadowOpacity: 0.15,
      shadowRadius: 20,
    } : {}),
  },
  buttonDisabled: { opacity: 0.9, paddingVertical: 22, backgroundColor: '#111' },
  buttonText: { color: '#000', fontSize: 16, fontWeight: '700', letterSpacing: 0.3 },

  // ── Progress ──
  progressContainer: { width: '100%', gap: 10 },
  progressBarTrack: {
    width: '100%', height: 2, backgroundColor: '#333',
    borderRadius: 1, marginBottom: 6, overflow: 'hidden',
  },
  progressBarFill: {
    height: 2, backgroundColor: '#3B82F6', borderRadius: 1,
  },
  progressStep:  { flexDirection: 'row', alignItems: 'center', gap: 10 },
  progressDot: {
    width: 24, height: 24, borderRadius: 12,
    backgroundColor: '#222', borderWidth: 1, borderColor: '#333',
    alignItems: 'center', justifyContent: 'center',
  },
  progressDotDone:    { backgroundColor: '#10B981', borderColor: '#10B981' },
  progressDotCurrent: {
    backgroundColor: '#3B82F6', borderColor: '#3B82F6',
    ...(Platform.OS === 'ios' ? {
      shadowColor: '#3B82F6',
      shadowOffset: { width: 0, height: 0 },
      shadowOpacity: 0.9,
      shadowRadius: 8,
    } : {}),
  },
  progressLabel:       { color: '#555', fontSize: 13 },
  progressLabelActive: { color: '#fff', fontWeight: '600' },
  checkmark: { color: '#fff', fontSize: 12, fontWeight: '700' },
});
