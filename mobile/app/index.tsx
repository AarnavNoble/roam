import { useState, useRef, useEffect } from 'react';
import {
  View, Text, TextInput, TouchableOpacity,
  ScrollView, StyleSheet, ActivityIndicator,
  Animated, Platform, UIManager, LayoutAnimation,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { generateItineraryStreaming, storeItinerary, saveTrip, loadPrefs, savePrefs, TripRequest, PipelineProgress } from '../services/api';
import { ONBOARDING_KEY } from './onboarding';

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
const TRANSPORT_OPTIONS = ['walking', 'transit'] as const;

const MOBILITY_LABELS = { easy: 'Easy (short walks)', moderate: 'Moderate', active: 'Active (lots of walking)' };
const FAMILIARITY_LABELS = { first_time: 'First time here', returning: 'I\'ve been before' };
const DURATION_LABELS: Record<number, string> = { 2: '2h', 3: '3h', 4: '4h', 6: '6h', 8: '8h', 10: 'Full day' };

const GOAL_COLORS: Record<string, string> = {
  food: '#F59E0B', nature: '#10B981', history: '#8B5CF6',
  culture: '#3B82F6', nightlife: '#EC4899', shopping: '#F97316', adventure: '#EF4444',
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
  ...GOAL_OPTIONS, ...DURATION_OPTIONS.map(String), ...START_TIME_OPTIONS,
  ...TRANSPORT_OPTIONS, ...PACE_OPTIONS, ...BUDGET_OPTIONS, ...STYLE_OPTIONS,
  ...DIETARY_OPTIONS, ...MOBILITY_OPTIONS, ...FAMILIARITY_OPTIONS,
];

export default function HomeScreen() {
  const router = useRouter();

  const [city, setCity] = useState('');
  const [startLocation, setStartLocation] = useState('');
  const [tripDate, setTripDate] = useState<Date>(() => {
    const d = new Date(); d.setHours(0, 0, 0, 0); return d;
  });
  const [goals, setGoals] = useState<string[]>([]);
  const [durationHours, setDurationHours] = useState<number>(6);
  const [transport, setTransport] = useState<TripRequest['transport']>('walking');
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
  const [cityError, setCityError] = useState<string | null>(null);
  const [locationError, setLocationError] = useState<string | null>(null);

  // ── Animations ───────────────────────────────────────────────────────────────

  const heroTitleAnim    = useRef(new Animated.Value(0)).current;
  const heroSubtitleAnim = useRef(new Animated.Value(0)).current;
  const heroAccentAnim   = useRef(new Animated.Value(0)).current;
  const chipScales = useRef<Record<string, Animated.Value>>(
    Object.fromEntries(ALL_CHIP_KEYS.map(k => [k, new Animated.Value(1)]))
  ).current;
  const cityBorderAnim     = useRef(new Animated.Value(0)).current;
  const locationBorderAnim = useRef(new Animated.Value(0)).current;
  const notesBorderAnim    = useRef(new Animated.Value(0)).current;
  const buttonScaleAnim    = useRef(new Animated.Value(1)).current;
  const loadingPulseAnim   = useRef(new Animated.Value(1)).current;
  const stepSlideAnims     = useRef(PIPELINE_STEPS.map(() => new Animated.Value(0))).current;
  const progressBarAnim    = useRef(new Animated.Value(0)).current;

  // Redirect to onboarding on first launch, then restore saved prefs
  useEffect(() => {
    AsyncStorage.getItem(ONBOARDING_KEY).then(val => {
      if (!val) { router.replace('/onboarding' as any); return; }
      loadPrefs().then(p => {
        if (p.pace)         setPace(p.pace);
        if (p.budget)       setBudget(p.budget);
        if (p.style)        setStyle(p.style);
        if (p.dietary)      setDietary(p.dietary);
        if (p.mobility)     setMobility(p.mobility);
        if (p.familiarity)  setFamiliarity(p.familiarity);
        if (p.transport)    setTransport(p.transport);
        if (p.durationHours) setDurationHours(p.durationHours);
        if (p.startTime)    setStartTime(p.startTime);
        if (p.goals?.length) setGoals(p.goals);
      });
    });
  }, []);

  useEffect(() => {
    Animated.stagger(80, [
      Animated.timing(heroTitleAnim,    { toValue: 1, duration: 600, useNativeDriver: true }),
      Animated.timing(heroSubtitleAnim, { toValue: 1, duration: 500, useNativeDriver: true }),
      Animated.timing(heroAccentAnim,   { toValue: 1, duration: 400, useNativeDriver: false }),
    ]).start();
  }, []);

  useEffect(() => {
    if (!loading) { loadingPulseAnim.setValue(1); return; }
    const pulse = Animated.loop(Animated.sequence([
      Animated.timing(loadingPulseAnim, { toValue: 0.7, duration: 900, useNativeDriver: true }),
      Animated.timing(loadingPulseAnim, { toValue: 1,   duration: 900, useNativeDriver: true }),
    ]));
    pulse.start();
    return () => pulse.stop();
  }, [loading]);

  useEffect(() => {
    if (!loading) { progressBarAnim.setValue(0); stepSlideAnims.forEach(a => a.setValue(0)); return; }
    const stepIndex = PIPELINE_STEPS.findIndex(s => s.key === currentStep);
    if (stepIndex < 0) return;
    Animated.timing(progressBarAnim, { toValue: (completedSteps.length + 1) / PIPELINE_STEPS.length, duration: 400, useNativeDriver: false }).start();
    Animated.timing(stepSlideAnims[stepIndex], { toValue: 1, duration: 250, useNativeDriver: true }).start();
  }, [currentStep, loading]);

  // ── Helpers ──────────────────────────────────────────────────────────────────

  const animateChip = (key: string) => {
    const anim = chipScales[key]; if (!anim) return;
    Animated.sequence([
      Animated.spring(anim, { toValue: 0.90, useNativeDriver: true, speed: 30, bounciness: 0 }),
      Animated.spring(anim, { toValue: 1.0,  useNativeDriver: true, speed: 20, bounciness: 10 }),
    ]).start();
  };
  const selectChip = <T extends string | number>(key: string, setter: (v: T) => void, value: T) => {
    animateChip(key); setter(value);
  };

  const validateLocation = (value: string): string | null => {
    const v = value.trim();
    if (!v) return null; // empty handled separately on submit
    if (v.length < 3) return 'Too short — enter a real place';
    if (!/[a-zA-Z]/.test(v)) return 'Must contain letters';
    if (!/[aeiouAEIOU]/.test(v)) return 'Doesn\'t look like a real place';
    // Check for keyboard mashing: more than 40% repeated single char
    const freq = [...v.replace(/\s/g, '')].reduce<Record<string, number>>((acc, c) => {
      acc[c.toLowerCase()] = (acc[c.toLowerCase()] || 0) + 1; return acc;
    }, {});
    const maxFreq = Math.max(...Object.values(freq));
    if (maxFreq / v.replace(/\s/g, '').length > 0.5) return 'Doesn\'t look like a real place';
    // Check consonant runs — 5+ consonants in a row is gibberish
    if (/[^aeiou\s\d]{5,}/i.test(v)) return 'Doesn\'t look like a real place';
    return null;
  };

  const focusInput = (anim: Animated.Value) =>
    Animated.timing(anim, { toValue: 1, duration: 200, useNativeDriver: false }).start();
  const blurInput = (anim: Animated.Value) =>
    Animated.timing(anim, { toValue: 0, duration: 200, useNativeDriver: false }).start();
  const inputBorder = (anim: Animated.Value) =>
    anim.interpolate({ inputRange: [0, 1], outputRange: ['rgba(255,255,255,0.08)', 'rgba(255,255,255,0.20)'] });

  const toggleGoal = (goal: string) => {
    animateChip(goal);
    setGoals(prev => prev.includes(goal) ? prev.filter(g => g !== goal) : [...prev, goal]);
  };

  const onBtnIn  = () => Animated.spring(buttonScaleAnim, { toValue: 0.97, useNativeDriver: true, speed: 30, bounciness: 0 }).start();
  const onBtnOut = () => Animated.spring(buttonScaleAnim, { toValue: 1.0,  useNativeDriver: true, speed: 20, bounciness: 6 }).start();

  const handleGenerate = async () => {
    if (!city.trim())          { setError('Enter a city or area'); return; }
    if (!startLocation.trim()) { setError('Enter where you\'re starting from'); return; }
    const cityErr = validateLocation(city);
    const locErr  = validateLocation(startLocation);
    if (cityErr)    { setCityError(cityErr); setError('Fix the fields above'); return; }
    if (locErr)     { setLocationError(locErr); setError('Fix the fields above'); return; }
    if (goals.length === 0) { setError('Pick at least one interest'); return; }
    setLoading(true); setCurrentStep(null); setCompletedSteps([]); setError(null);
    try {
      const isoDate = tripDate.toISOString().split('T')[0];
      const itinerary = await generateItineraryStreaming(
        { city: city.trim(), start_location: startLocation.trim(), duration_hours: durationHours, goals, transport, pace, budget, style, dietary, mobility, familiarity, start_time: startTime, notes: notes.trim(), trip_date: isoDate },
        (progress: PipelineProgress) => {
          setCurrentStep(prev => { if (prev) setCompletedSteps(c => c.includes(prev) ? c : [...c, prev]); return progress.step; });
        },
      );
      storeItinerary(itinerary);
      saveTrip(city.trim(), goals, itinerary, isoDate);
      savePrefs({ pace, budget, style, dietary, mobility, familiarity, transport, durationHours, startTime, goals });
      router.push({ pathname: '/itinerary', params: { goals: JSON.stringify(goals), city: city.trim(), tripDate: tripDate.toISOString() } });
    } catch (e: any) {
      const msg = e?.message || '';
      setError(msg.includes('504') || msg.includes('Gateway') || msg.includes('mirrors failed')
        ? 'Location service timed out. Try a different area or fewer interests.'
        : msg.includes('No POIs') || msg.includes('No places')
        ? 'No places found there. Try a different starting point or broader interests.'
        : 'Something went wrong. Please try again.');
    } finally { setLoading(false); setCurrentStep(null); setCompletedSteps([]); }
  };

  // ── Renderers ────────────────────────────────────────────────────────────────

  const renderGoalChip = (g: string) => {
    const selected = goals.includes(g);
    const accent = GOAL_COLORS[g];
    return (
      <Animated.View key={g} style={{ transform: [{ scale: chipScales[g] }] }}>
        <TouchableOpacity
          style={[styles.chip, selected && { backgroundColor: accent + '18', borderColor: accent + '55' }]}
          onPress={() => toggleGoal(g)}
        >
          <View style={[styles.goalDot, { backgroundColor: selected ? accent : accent + '44' }]} />
          <Text style={[styles.chipText, selected && { color: accent, fontWeight: '600' }]}>{g[0].toUpperCase() + g.slice(1)}</Text>
        </TouchableOpacity>
      </Animated.View>
    );
  };

  const renderChip = <T extends string | number>(
    value: T, current: T, setter: (v: T) => void, label?: string
  ) => {
    const key = String(value);
    const selected = current === value;
    return (
      <Animated.View key={key} style={{ transform: [{ scale: chipScales[key] }] }}>
        <TouchableOpacity
          style={[styles.chip, selected && styles.chipSelected]}
          onPress={() => selectChip(key, setter, value)}
        >
          <Text style={[styles.chipText, selected && styles.chipTextSelected]}>{(label ?? key).replace(/^\w/, c => c.toUpperCase())}</Text>
        </TouchableOpacity>
      </Animated.View>
    );
  };

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>

      {/* ── Hero ── */}
      <View style={styles.heroContainer}>
        <View style={styles.glow1} pointerEvents="none" />
        <View style={styles.glow2} pointerEvents="none" />

        <View style={styles.heroBtns}>
          <TouchableOpacity style={styles.heroBtn} onPress={() => router.push('/history' as any)}>
            <Text style={styles.heroBtnText}>Saved</Text>
          </TouchableOpacity>
          <TouchableOpacity style={styles.heroBtn} onPress={() => router.push('/settings' as any)}>
            <Text style={styles.heroBtnText}>⚙</Text>
          </TouchableOpacity>
        </View>

        <Animated.View style={{
          opacity: heroTitleAnim,
          transform: [{ translateY: heroTitleAnim.interpolate({ inputRange: [0, 1], outputRange: [20, 0] }) }],
        }}>
          <Text style={styles.title}>roam</Text>
          <Animated.View style={[styles.titleAccent, {
            width: heroAccentAnim.interpolate({ inputRange: [0, 1], outputRange: ['0%', '100%'] }),
          }]} />
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
      <Text style={styles.label}>Where to?</Text>
      <Animated.View style={[styles.inputWrapper, { borderColor: inputBorder(cityBorderAnim) }]}>
        <TextInput
          style={styles.inputInner}
          placeholder="Paris, Tokyo, New York..."
          placeholderTextColor="rgba(255,255,255,0.25)"
          value={city} onChangeText={v => { setCity(v); if (cityError) setCityError(validateLocation(v)); }}
          onFocus={() => focusInput(cityBorderAnim)}
          onBlur={() => { blurInput(cityBorderAnim); setCityError(validateLocation(city)); }}
        />
      </Animated.View>

      {cityError && <Text style={styles.fieldError}>{cityError}</Text>}

      <Text style={styles.label}>Starting from</Text>
      <Animated.View style={[styles.inputWrapper, { borderColor: inputBorder(locationBorderAnim) }]}>
        <TextInput
          style={styles.inputInner}
          placeholder="Montmartre, Shibuya station, Times Square..."
          placeholderTextColor="rgba(255,255,255,0.25)"
          value={startLocation} onChangeText={v => { setStartLocation(v); if (locationError) setLocationError(validateLocation(v)); }}
          onFocus={() => focusInput(locationBorderAnim)}
          onBlur={() => { blurInput(locationBorderAnim); setLocationError(validateLocation(startLocation)); }}
        />
      </Animated.View>

      {locationError && <Text style={styles.fieldError}>{locationError}</Text>}

      <Text style={styles.label}>Trip date</Text>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.dateRow} contentContainerStyle={styles.dateRowContent}>
        {Array.from({ length: 30 }, (_, i) => {
          const d = new Date(); d.setHours(0, 0, 0, 0); d.setDate(d.getDate() + i);
          const selected = d.toDateString() === tripDate.toDateString();
          const dayLabel = i === 0 ? 'Today' : i === 1 ? 'Tomorrow'
            : d.toLocaleDateString(undefined, { weekday: 'short' });
          const dateNum = d.getDate();
          const monthLabel = i > 1 ? d.toLocaleDateString(undefined, { month: 'short' }) : undefined;
          const showMonth = i > 1 && d.getDate() === 1;
          return (
            <TouchableOpacity
              key={i}
              style={[styles.dateChip, selected && styles.dateChipSelected]}
              onPress={() => setTripDate(d)}
            >
              <Text style={[styles.dateChipDay, selected && styles.dateChipDaySelected]}>{dayLabel}</Text>
              {i > 1 && <Text style={[styles.dateChipNum, selected && styles.dateChipNumSelected]}>{showMonth ? monthLabel : dateNum}</Text>}
            </TouchableOpacity>
          );
        })}
      </ScrollView>

      <Text style={styles.label}>Interests</Text>
      <View style={styles.row}>{GOAL_OPTIONS.map(renderGoalChip)}</View>

      <Text style={styles.label}>Duration</Text>
      <View style={styles.row}>
        {DURATION_OPTIONS.map(d => renderChip(d, durationHours, setDurationHours as any, DURATION_LABELS[d]))}
      </View>

      <Text style={styles.label}>Start time</Text>
      <View style={styles.row}>
        {START_TIME_OPTIONS.map(t => renderChip(t, startTime, setStartTime as any))}
      </View>

      <Text style={styles.label}>Getting around</Text>
      <View style={styles.row}>
        {TRANSPORT_OPTIONS.map(t => renderChip(t, transport, setTransport as any, t === 'walking' ? 'Walking' : 'Transit'))}
      </View>

      <View style={styles.divider} />

      {/* ── Preferences ── */}
      <View style={styles.sectionHeaderRow}>
        <View style={styles.sectionDot} />
        <Text style={styles.sectionHeader}>Preferences</Text>
      </View>

      <Text style={styles.label}>Pace</Text>
      <View style={styles.row}>
        {PACE_OPTIONS.map(p => renderChip(p, pace, setPace as any))}
      </View>

      <Text style={styles.label}>Budget</Text>
      <View style={styles.row}>
        {BUDGET_OPTIONS.map(b => renderChip(b, budget, setBudget as any))}
      </View>

      <Text style={styles.label}>Traveling as</Text>
      <View style={styles.row}>
        {STYLE_OPTIONS.map(s => renderChip(s, style, setStyle as any))}
      </View>

      <Text style={styles.label}>Walking comfort</Text>
      <View style={styles.column}>
        {MOBILITY_OPTIONS.map(m => {
          const selected = mobility === m;
          return (
            <Animated.View key={m} style={{ transform: [{ scale: chipScales[m] }] }}>
              <TouchableOpacity
                style={[styles.optionRow, selected && styles.optionRowSelected]}
                onPress={() => selectChip(m, setMobility as any, m)}
              >
                <Text style={[styles.chipText, selected && styles.chipTextSelected]}>
                  {MOBILITY_LABELS[m]}
                </Text>
              </TouchableOpacity>
            </Animated.View>
          );
        })}
      </View>

      <Text style={styles.label}>Familiarity</Text>
      <View style={styles.row}>
        {FAMILIARITY_OPTIONS.map(f => renderChip(f, familiarity, setFamiliarity as any, FAMILIARITY_LABELS[f]))}
      </View>

      <Text style={styles.label}>Dietary</Text>
      <View style={styles.row}>
        {DIETARY_OPTIONS.map(d => renderChip(d, dietary, setDietary as any))}
      </View>

      <Text style={styles.label}>Anything else?</Text>
      <Animated.View style={[styles.inputWrapper, { borderColor: inputBorder(notesBorderAnim) }]}>
        <TextInput
          style={[styles.inputInner, styles.inputMultiline]}
          placeholder="I love street food, hate tourist traps..."
          placeholderTextColor="rgba(255,255,255,0.25)"
          value={notes} onChangeText={setNotes}
          onFocus={() => focusInput(notesBorderAnim)} onBlur={() => blurInput(notesBorderAnim)}
          multiline numberOfLines={3}
        />
      </Animated.View>

      {error && (
        <View style={styles.errorBox}>
          <Text style={styles.errorText}>{error}</Text>
        </View>
      )}

      {/* ── CTA Button ── */}
      <Animated.View style={[
        styles.ctaContainer,
        { transform: [{ scale: buttonScaleAnim }] },
        loading && { opacity: loadingPulseAnim },
      ]}>
        {/* Glow behind button */}
        {!loading && <View style={styles.ctaGlow} />}

        <TouchableOpacity
          style={[styles.button, loading && styles.buttonLoading]}
          onPress={loading ? undefined : handleGenerate}
          onPressIn={loading ? undefined : onBtnIn}
          onPressOut={loading ? undefined : onBtnOut}
          activeOpacity={1}
        >
          {loading ? (
            <View style={styles.progressContainer}>
              <View style={styles.progressBarTrack}>
                <Animated.View style={[styles.progressBarFill, {
                  width: progressBarAnim.interpolate({ inputRange: [0, 1], outputRange: ['0%', '100%'] }),
                }]} />
              </View>
              {PIPELINE_STEPS.map((step, index) => {
                const done    = completedSteps.includes(step.key);
                const current = currentStep === step.key;
                return (
                  <View key={step.key} style={styles.progressStep}>
                    <View style={[styles.progressDot, done && styles.progressDotDone, current && styles.progressDotCurrent]}>
                      {current && <ActivityIndicator size="small" color="#fff" />}
                      {done && <Text style={styles.checkmark}>✓</Text>}
                    </View>
                    <Animated.Text style={[
                      styles.progressLabel,
                      (done || current) && styles.progressLabelActive,
                      { opacity: done || current ? 1 : 0.3, transform: [{ translateX: stepSlideAnims[index].interpolate({ inputRange: [0, 1], outputRange: current ? [-12, 0] : [0, 0] }) }] },
                    ]}>{step.label}</Animated.Text>
                  </View>
                );
              })}
            </View>
          ) : (
            <LinearGradient
              colors={['#fff', '#e8e8e8']}
              start={{ x: 0, y: 0 }} end={{ x: 0, y: 1 }}
              style={styles.buttonGradient}
            >
              <Text style={styles.buttonText}>Build My Journey</Text>
            </LinearGradient>
          )}
        </TouchableOpacity>
      </Animated.View>

    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#09090b' },
  content:   { padding: 24, paddingTop: 72, paddingBottom: 60 },

  // ── Hero ──
  heroContainer: { paddingBottom: 16, marginBottom: 4, overflow: 'hidden' },
  heroBtns: {
    position: 'absolute', top: 0, right: 0,
    flexDirection: 'row', gap: 8, zIndex: 2,
  },
  heroBtn: {
    paddingHorizontal: 12, paddingVertical: 6,
    backgroundColor: 'rgba(255,255,255,0.06)',
    borderRadius: 100, borderWidth: 1, borderColor: 'rgba(255,255,255,0.10)',
  },
  heroBtnText: { color: 'rgba(255,255,255,0.5)', fontSize: 12, fontWeight: '500' },
  glow1: {
    position: 'absolute', top: -30, left: -60,
    width: 260, height: 260, borderRadius: 130,
    backgroundColor: '#3B82F6', opacity: 0.12,
  },
  glow2: {
    position: 'absolute', top: 10, right: -40,
    width: 180, height: 180, borderRadius: 90,
    backgroundColor: '#8B5CF6', opacity: 0.08,
  },
  title: { fontSize: 56, fontWeight: '800', color: '#fff', letterSpacing: -3 },
  titleAccent: {
    height: 3, maxWidth: 40,
    backgroundColor: '#3B82F6', borderRadius: 2,
    marginTop: 8, marginBottom: 12,
  },
  subtitle: { fontSize: 15, color: 'rgba(255,255,255,0.4)', letterSpacing: 0.2 },

  // ── Layout ──
  divider: { height: 1, backgroundColor: 'rgba(255,255,255,0.06)', marginVertical: 28 },
  sectionHeaderRow: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 4 },
  sectionDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: '#3B82F6', opacity: 0.8 },
  sectionHeader: { fontSize: 12, color: 'rgba(255,255,255,0.35)', textTransform: 'uppercase', letterSpacing: 1.5 },
  label: { fontSize: 12, color: 'rgba(255,255,255,0.4)', marginBottom: 10, marginTop: 24, textTransform: 'uppercase', letterSpacing: 0.8 },

  // ── Inputs ──
  inputWrapper: { backgroundColor: 'rgba(255,255,255,0.04)', borderRadius: 12, borderWidth: 1, borderTopColor: 'rgba(255,255,255,0.10)' },
  inputInner: { color: '#fff', padding: 16, fontSize: 15 },
  inputMultiline: { minHeight: 80, textAlignVertical: 'top' },

  // ── Chips ──
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  column: { gap: 8 },
  chip: {
    flexDirection: 'row', alignItems: 'center', gap: 7,
    paddingHorizontal: 14, paddingVertical: 9, borderRadius: 100,
    backgroundColor: 'rgba(255,255,255,0.05)',
    borderWidth: 1, borderColor: 'rgba(255,255,255,0.08)',
  },
  chipSelected: {
    backgroundColor: 'rgba(255,255,255,0.12)',
    borderColor: 'rgba(255,255,255,0.30)',
  },
  chipText:         { color: 'rgba(255,255,255,0.45)', fontSize: 13 },
  chipTextSelected: { color: '#fff', fontWeight: '600' },
  goalDot: { width: 7, height: 7, borderRadius: 3.5 },
  optionRow: {
    paddingHorizontal: 16, paddingVertical: 13, borderRadius: 12,
    backgroundColor: 'rgba(255,255,255,0.04)', borderWidth: 1, borderColor: 'rgba(255,255,255,0.08)',
  },
  optionRowSelected: { backgroundColor: 'rgba(255,255,255,0.10)', borderColor: 'rgba(255,255,255,0.25)' },

  // ── Date picker ──
  dateRow:        { marginRight: -24 },
  dateRowContent: { gap: 8, paddingRight: 24 },
  dateChip: {
    alignItems: 'center', justifyContent: 'center',
    paddingHorizontal: 16, paddingVertical: 10, borderRadius: 12, minWidth: 68,
    backgroundColor: 'rgba(255,255,255,0.05)',
    borderWidth: 1, borderColor: 'rgba(255,255,255,0.08)',
  },
  dateChipSelected:    { backgroundColor: 'rgba(255,255,255,0.12)', borderColor: 'rgba(255,255,255,0.30)' },
  dateChipDay:         { color: 'rgba(255,255,255,0.45)', fontSize: 13 },
  dateChipDaySelected: { color: '#fff', fontWeight: '600' },
  dateChipNum:         { color: 'rgba(255,255,255,0.3)', fontSize: 11, marginTop: 2 },
  dateChipNumSelected: { color: 'rgba(255,255,255,0.6)' },

  fieldError: { color: '#EF4444', fontSize: 12, marginTop: 6, marginLeft: 2, opacity: 0.85 },

  // ── Error ──
  errorBox: {
    backgroundColor: 'rgba(239,68,68,0.08)', borderRadius: 10,
    borderWidth: 1, borderColor: 'rgba(239,68,68,0.2)',
    padding: 12, marginTop: 16,
  },
  errorText: { color: '#EF4444', fontSize: 13, textAlign: 'center' },

  // ── CTA ──
  ctaContainer: { marginTop: 40, marginBottom: 40, alignItems: 'center' },
  ctaGlow: {
    position: 'absolute', top: 8, width: '50%', height: 44,
    borderRadius: 40, backgroundColor: 'rgba(255,255,255,0.08)',
    ...(Platform.OS === 'ios' ? {
      shadowColor: '#fff', shadowOffset: { width: 0, height: 0 },
      shadowOpacity: 0.2, shadowRadius: 30,
    } : {}),
  },
  button: {
    width: '100%', borderRadius: 14, overflow: 'hidden',
  },
  buttonGradient: {
    paddingVertical: 18, alignItems: 'center',
  },
  buttonLoading: {
    backgroundColor: 'rgba(255,255,255,0.06)',
    borderWidth: 1, borderColor: 'rgba(255,255,255,0.08)',
    borderRadius: 14, padding: 20,
  },
  buttonText: { color: '#09090b', fontSize: 16, fontWeight: '700', letterSpacing: 0.3 },

  // ── Progress ──
  progressContainer: { width: '100%', gap: 10 },
  progressBarTrack: {
    width: '100%', height: 2, backgroundColor: 'rgba(255,255,255,0.08)',
    borderRadius: 1, marginBottom: 6, overflow: 'hidden',
  },
  progressBarFill: { height: 2, backgroundColor: '#3B82F6', borderRadius: 1 },
  progressStep: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  progressDot: {
    width: 24, height: 24, borderRadius: 12,
    backgroundColor: 'rgba(255,255,255,0.06)', borderWidth: 1, borderColor: 'rgba(255,255,255,0.1)',
    alignItems: 'center', justifyContent: 'center',
  },
  progressDotDone: { backgroundColor: '#10B981', borderColor: '#10B981' },
  progressDotCurrent: {
    backgroundColor: '#3B82F6', borderColor: '#3B82F6',
    ...(Platform.OS === 'ios' ? { shadowColor: '#3B82F6', shadowOffset: { width: 0, height: 0 }, shadowOpacity: 0.9, shadowRadius: 8 } : {}),
  },
  progressLabel:       { color: 'rgba(255,255,255,0.35)', fontSize: 13 },
  progressLabelActive: { color: '#fff', fontWeight: '600' },
  checkmark: { color: '#fff', fontSize: 12, fontWeight: '700' },
});
